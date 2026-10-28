package com.maxrave.media3.audio

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * The 6144-point complex FFT the MDX-Net vocal model's STFT is built on.
 *
 * `6144 = 3 * 2048`, and [Fft] only does powers of two, so this is one mixed-radix stage on top of
 * three 2048-point transforms instead of a whole new FFT:
 *
 * ```
 * X[k] = sum over j in {0,1,2} of  W(6144)^(j*k) * A_j[k mod 2048]
 * ```
 *
 * where `A_j` is the 2048-point DFT of the input decimated by 3 with offset `j` (`x[3n+j]`). One
 * radix-3 pass, then three radix-2 transforms — the same decomposition the reference STFT in
 * `audio-separator` gets from `torch.stft(n_fft=6144)`, checked against numpy to 5e-13 before this
 * was written.
 *
 * The twiddles are precomputed per `j` and per `k` rather than per stage; 6144 entries is 98 KB and
 * the transform runs 512 times per five-second chunk, so the table is worth it.
 *
 * Not thread safe: one instance belongs to one [MdxVocalExtractor], which the audio thread owns.
 */
internal class MdxFft6144 {
    private val sub = Fft(SUB_SIZE)

    /** Decimated inputs, one array pair per radix-3 branch. */
    private val subReal = Array(RADIX) { DoubleArray(SUB_SIZE) }
    private val subImag = Array(RADIX) { DoubleArray(SUB_SIZE) }

    private val outReal = DoubleArray(SIZE)
    private val outImag = DoubleArray(SIZE)

    /** `exp(-2*pi*i*j*k/SIZE)` for each branch `j`. */
    private val twiddleReal = Array(RADIX) { DoubleArray(SIZE) }
    private val twiddleImag = Array(RADIX) { DoubleArray(SIZE) }

    init {
        for (j in 0 until RADIX) {
            for (k in 0 until SIZE) {
                val angle = -2.0 * PI * j * k / SIZE
                twiddleReal[j][k] = cos(angle)
                twiddleImag[j][k] = sin(angle)
            }
        }
    }

    /** In place: `X[k] = sum x[n] * exp(-2*pi*i*k*n/SIZE)`. */
    fun forward(
        real: DoubleArray,
        imag: DoubleArray,
    ) {
        for (j in 0 until RADIX) {
            val branchReal = subReal[j]
            val branchImag = subImag[j]
            var n = j
            for (i in 0 until SUB_SIZE) {
                branchReal[i] = real[n]
                branchImag[i] = imag[n]
                n += RADIX
            }
            sub.forward(branchReal, branchImag)
        }

        for (k in 0 until SIZE) {
            val subIndex = k % SUB_SIZE
            var sumReal = 0.0
            var sumImag = 0.0
            for (j in 0 until RADIX) {
                val wr = twiddleReal[j][k]
                val wi = twiddleImag[j][k]
                val ar = subReal[j][subIndex]
                val ai = subImag[j][subIndex]
                sumReal += wr * ar - wi * ai
                sumImag += wr * ai + wi * ar
            }
            outReal[k] = sumReal
            outImag[k] = sumImag
        }

        System.arraycopy(outReal, 0, real, 0, SIZE)
        System.arraycopy(outImag, 0, imag, 0, SIZE)
    }

    /** In place inverse, scaled by `1/SIZE`: conjugate, forward, conjugate and scale. */
    fun inverse(
        real: DoubleArray,
        imag: DoubleArray,
    ) {
        for (i in 0 until SIZE) {
            imag[i] = -imag[i]
        }
        forward(real, imag)
        val scale = 1.0 / SIZE
        for (i in 0 until SIZE) {
            real[i] *= scale
            imag[i] = -imag[i] * scale
        }
    }

    private companion object {
        /** `n_fft` of the MDX-Net models (`mdx_n_fft_scale_set`). */
        const val SIZE = 6144

        /** `SIZE / 3`, the largest power-of-two factor [Fft] can take. */
        const val SUB_SIZE = SIZE / 3

        const val RADIX = 3
    }
}
