package com.maxrave.media3.audio

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/**
 * Streaming vocal isolation with an MDX-Net model.
 *
 * What it does per chunk, matching `audio-separator`'s MDX pipeline sample for sample:
 *
 * 1. window the chunk ([CHUNK] samples) with a Hann taper and pad it by [HALF_FRAME] of silence on
 *    both sides, so the analysis frames line up with `torch.stft(center = true)`;
 * 2. STFT it at `n_fft = 6144`, `hop = 1024` — 256 frames, the first 2048 bins of which are handed
 *    to the model as `[1, 4, 2048, 256]`: real and imaginary parts of left and right;
 * 3. run the model, which returns the vocal spectrogram at that same shape;
 * 4. **suppress what bled through**: the vocal estimate is compared bin by bin against the mix.
 *    `music = mix - vocals`, and where the music dominates a bin the vocal estimate is pushed down
 *    hard — `gain = v^2 / (v^2 + (alpha * music)^2)`. That is what stops a hi-hat, a synth pad or a
 *    guitar riding along inside the "vocals". Bins under [VOCAL_LOW_HZ] (kick, bass) and over
 *    [VOCAL_HIGH_HZ] (cymbals, air) are zeroed outright;
 * 5. inverse STFT back to the time domain and overlap-add with a Hann taper and a matching
 *    window-sum divisor, so the seams between chunks are inaudible.
 *
 * Chunks overlap by 25 %, so a chunk is only final once the one before it has been added: each pass
 * publishes [STEP] samples, and [availableFrames] reports what is ready to read.
 *
 * One extractor belongs to one audio thread. It allocates everything it needs up front; a chunk
 * costs two 6144-point transforms per frame and one model call.
 */
internal class MdxVocalExtractor(
    private val model: MdxVocalModel,
    /** Higher = more music removed, at the cost of thin vocals. 1.0 is neutral. */
    private val musicSuppression: Float = DEFAULT_SUPPRESSION,
) {
    private val fft = MdxFft6144()

    /** Periodic Hann over one analysis frame, as `torch.hann_window(6144, periodic = true)`. */
    private val frameWindow = DoubleArray(N_FFT) { 0.5 - 0.5 * cos(2.0 * PI * it / N_FFT) }

    /** Symmetric Hann over one chunk, the overlap-add taper (numpy's `np.hanning(chunk)`). */
    private val chunkWindow =
        FloatArray(CHUNK) { (0.5 - 0.5 * cos(2.0 * PI * it / (CHUNK - 1))).toFloat() }

    /** Incoming mix, front-padded with [HALF_FRAME] zeros like the reference implementation. */
    private val mixL = FloatArray(CHUNK)
    private val mixR = FloatArray(CHUNK)
    private var mixFill = HALF_FRAME

    /** Chunk padded for the STFT: [CHUNK] plus one frame of silence on each side. */
    private val paddedL = DoubleArray(CHUNK + 2 * HALF_FRAME)
    private val paddedR = DoubleArray(CHUNK + 2 * HALF_FRAME)

    /** Inverse-STFT accumulator and its window-sum normaliser, same padding. */
    private val accL = DoubleArray(CHUNK + 2 * HALF_FRAME)
    private val accR = DoubleArray(CHUNK + 2 * HALF_FRAME)
    private val norm = DoubleArray(CHUNK + 2 * HALF_FRAME)

    /** Model input and output: `[4, 2048, 256]`, channel-major, real before imaginary. */
    private val spectrum = FloatArray(CHANNELS_TOTAL)
    private val vocal = FloatArray(CHANNELS_TOTAL)

    /** Magnitude of the mix per bin and frame, kept for the bleed suppressor. */
    private val mixMagnitude = FloatArray(BINS_TOTAL)

    private val frameReal = DoubleArray(N_FFT)
    private val frameImag = DoubleArray(N_FFT)

    /** Overlap-add of finished chunks and its divisor. */
    private val resultL = DoubleArray(CHUNK)
    private val resultR = DoubleArray(CHUNK)
    private val divider = DoubleArray(CHUNK)

    /** Finished chunk, normalised by the inverse-STFT window sum. */
    private val chunkL = DoubleArray(CHUNK)
    private val chunkR = DoubleArray(CHUNK)

    /** Ready-to-read samples. */
    private var pendingL = FloatArray(CHUNK)
    private var pendingR = FloatArray(CHUNK)
    private var pendingHead = 0
    private var pendingTail = 0

    /** Samples at the head of the first chunk that exist only because of the front pad. */
    private var leadingTrim = HALF_FRAME

    val availableFrames: Int
        get() = pendingTail - pendingHead

    fun flush() {
        mixL.fill(0f)
        mixR.fill(0f)
        mixFill = HALF_FRAME
        resultL.fill(0.0)
        resultR.fill(0.0)
        divider.fill(0.0)
        accL.fill(0.0)
        accR.fill(0.0)
        norm.fill(0.0)
        pendingHead = 0
        pendingTail = 0
        leadingTrim = HALF_FRAME
    }

    /**
     * Accepts stereo PCM, buffering until a full [CHUNK] is available, then separates it.
     *
     * Runs on the audio thread and takes as long as the model takes. That is by design: the player
     * hands over a couple of seconds of samples at a time and this returns the separated ones, so a
     * chunk that takes longer than its own duration is heard as the audio waiting, not as a click.
     */
    fun writeStereo(
        inLeft: FloatArray,
        inRight: FloatArray,
        offset: Int,
        frameCount: Int,
    ) {
        var read = 0
        while (read < frameCount) {
            val room = CHUNK - mixFill
            if (room <= 0) {
                processChunk()
                continue
            }
            val take = min(room, frameCount - read)
            System.arraycopy(inLeft, offset + read, mixL, mixFill, take)
            System.arraycopy(inRight, offset + read, mixR, mixFill, take)
            mixFill += take
            read += take
            if (mixFill == CHUNK) processChunk()
        }
    }

    /**
     * Empties the last, partial chunk at the end of a track: pads it with silence and separates it,
     * so the final seconds are not dropped. Call once, when the stream ends.
     */
    fun finish() {
        repeat(TAIL_PASSES) {
            if (mixFill < CHUNK) {
                java.util.Arrays.fill(mixL, mixFill, CHUNK, 0f)
                java.util.Arrays.fill(mixR, mixFill, CHUNK, 0f)
                mixFill = CHUNK
            }
            processChunk()
        }
    }

    fun readStereo(
        outLeft: FloatArray,
        outRight: FloatArray,
        offset: Int,
        maxFrames: Int,
    ): Int {
        val count = min(maxFrames, availableFrames)
        System.arraycopy(pendingL, pendingHead, outLeft, offset, count)
        System.arraycopy(pendingR, pendingHead, outRight, offset, count)
        pendingHead += count
        if (pendingHead == pendingTail) {
            pendingHead = 0
            pendingTail = 0
        }
        return count
    }

    private fun processChunk() {
        analyse()
        if (infer()) {
            suppressBleed()
        } else {
            // The model refused this chunk. Hand back the mix rather than silence so the song keeps
            // playing; the failure is logged once per session by the model wrapper.
            System.arraycopy(spectrum, 0, vocal, 0, vocal.size)
        }
        synthesise()
        overlapAdd()
        publishStep()
    }

    // ---- 1 and 2: window, pad, STFT ---------------------------------------------------------------

    private fun analyse() {
        java.util.Arrays.fill(paddedL, 0.0)
        java.util.Arrays.fill(paddedR, 0.0)
        for (i in 0 until CHUNK) {
            val w = chunkWindow[i].toDouble()
            paddedL[HALF_FRAME + i] = mixL[i].toDouble() * w
            paddedR[HALF_FRAME + i] = mixR[i].toDouble() * w
        }

        java.util.Arrays.fill(mixMagnitude, 0f)
        for (frame in 0 until DIM_T) {
            val start = frame * HOP
            for (channel in 0 until 2) {
                val source = if (channel == 0) paddedL else paddedR
                for (n in 0 until N_FFT) {
                    frameReal[n] = source[start + n] * frameWindow[n]
                    frameImag[n] = 0.0
                }
                fft.forward(frameReal, frameImag)

                val base = 2 * channel * BINS_TOTAL
                for (bin in 0 until DIM_F) {
                    val index = bin * DIM_T + frame
                    spectrum[base + index] = frameReal[bin].toFloat()
                    spectrum[base + BINS_TOTAL + index] = frameImag[bin].toFloat()
                }
            }

            // Stereo peak magnitude of the mix, the reference for the bleed suppressor.
            for (bin in 0 until DIM_F) {
                val index = bin * DIM_T + frame
                val left = hypot(spectrum[index].toDouble(), spectrum[BINS_TOTAL + index].toDouble())
                val right =
                    hypot(
                        spectrum[2 * BINS_TOTAL + index].toDouble(),
                        spectrum[3 * BINS_TOTAL + index].toDouble(),
                    )
                mixMagnitude[index] = max(left, right).toFloat()
            }
        }

        // The reference pipeline zeroes the first three bins (DC to ~21 Hz) before the model.
        for (channel in 0 until 4) {
            val base = channel * BINS_TOTAL
            for (bin in 0 until 3) {
                for (frame in 0 until DIM_T) {
                    spectrum[base + bin * DIM_T + frame] = 0f
                }
            }
        }
    }

    /** Returns false when the model is gone or returned the wrong shape. */
    private fun infer(): Boolean {
        val out = model.run(spectrum) ?: return false
        if (out.size != vocal.size) return false
        System.arraycopy(out, 0, vocal, 0, vocal.size)
        return true
    }

    // ---- 4: take the music back out of the vocal estimate -------------------------------------

    private fun suppressBleed() {
        val alpha = musicSuppression.toDouble()
        for (frame in 0 until DIM_T) {
            for (bin in 0 until DIM_F) {
                val index = bin * DIM_T + frame
                val hz = bin * HZ_PER_BIN
                val bandGate =
                    when {
                        hz < VOCAL_LOW_HZ -> 0.0
                        hz < VOCAL_LOW_HZ + BAND_RAMP_HZ -> (hz - VOCAL_LOW_HZ) / BAND_RAMP_HZ
                        hz > VOCAL_HIGH_HZ -> 0.0
                        else -> 1.0
                    }

                var gain = bandGate
                if (gain > 0.0) {
                    val mixMag = mixMagnitude[index].toDouble()
                    for (channel in 0 until 2) {
                        val base = 2 * channel * BINS_TOTAL
                        val re = vocal[base + index].toDouble()
                        val im = vocal[base + BINS_TOTAL + index].toDouble()
                        val v = hypot(re, im)
                        val music = max(0.0, mixMag - v)
                        val keep = (v * v) / (v * v + (alpha * music) * (alpha * music) + EPSILON)
                        gain = min(gain, keep)
                    }
                }

                for (channel in 0 until 2) {
                    val base = 2 * channel * BINS_TOTAL
                    vocal[base + index] = (vocal[base + index].toDouble() * gain).toFloat()
                    vocal[base + BINS_TOTAL + index] =
                        (vocal[base + BINS_TOTAL + index].toDouble() * gain).toFloat()
                }
            }
        }
    }

    // ---- 5: inverse STFT, overlap-add ----------------------------------------------------------

    private fun synthesise() {
        java.util.Arrays.fill(accL, 0.0)
        java.util.Arrays.fill(accR, 0.0)
        java.util.Arrays.fill(norm, 0.0)

        for (frame in 0 until DIM_T) {
            val start = frame * HOP
            for (channel in 0 until 2) {
                val base = 2 * channel * BINS_TOTAL
                java.util.Arrays.fill(frameReal, 0.0)
                java.util.Arrays.fill(frameImag, 0.0)
                for (bin in 0 until DIM_F) {
                    val index = bin * DIM_T + frame
                    frameReal[bin] = vocal[base + index].toDouble()
                    frameImag[bin] = vocal[base + BINS_TOTAL + index].toDouble()
                }
                // Mirror the half spectrum so the inverse transform comes back real.
                for (bin in 1 until HALF_SPECTRUM) {
                    frameReal[N_FFT - bin] = frameReal[bin]
                    frameImag[N_FFT - bin] = -frameImag[bin]
                }
                frameImag[0] = 0.0
                frameImag[HALF_SPECTRUM] = 0.0

                fft.inverse(frameReal, frameImag)

                val accumulator = if (channel == 0) accL else accR
                for (n in 0 until N_FFT) {
                    val w = frameWindow[n]
                    accumulator[start + n] += frameReal[n] * w
                    if (channel == 0) norm[start + n] += w * w
                }
            }
        }

        for (i in 0 until CHUNK) {
            val n = norm[HALF_FRAME + i]
            val scale = if (n > 1e-9) 1.0 / n else 0.0
            chunkL[i] = accL[HALF_FRAME + i] * scale
            chunkR[i] = accR[HALF_FRAME + i] * scale
        }
    }

    private fun overlapAdd() {
        for (i in 0 until CHUNK) {
            val w = chunkWindow[i].toDouble()
            resultL[i] += chunkL[i] * w
            resultR[i] += chunkR[i] * w
            divider[i] += w
        }
    }

    /** Publishes [STEP] finished samples, then slides the overlap-add and input buffers forward. */
    private fun publishStep() {
        val skip = leadingTrim
        leadingTrim = 0
        val produced = STEP - skip
        ensurePendingCapacity(produced)

        var written = 0
        for (i in skip until STEP) {
            val d = divider[i]
            val inv = if (d > 1e-6) 1.0 / d else 0.0
            pendingL[pendingTail + written] = (resultL[i] * inv).toFloat()
            pendingR[pendingTail + written] = (resultR[i] * inv).toFloat()
            written++
        }
        pendingTail += written

        System.arraycopy(resultL, STEP, resultL, 0, CHUNK - STEP)
        System.arraycopy(resultR, STEP, resultR, 0, CHUNK - STEP)
        System.arraycopy(divider, STEP, divider, 0, CHUNK - STEP)
        java.util.Arrays.fill(resultL, CHUNK - STEP, CHUNK, 0.0)
        java.util.Arrays.fill(resultR, CHUNK - STEP, CHUNK, 0.0)
        java.util.Arrays.fill(divider, CHUNK - STEP, CHUNK, 0.0)

        System.arraycopy(mixL, STEP, mixL, 0, CHUNK - STEP)
        System.arraycopy(mixR, STEP, mixR, 0, CHUNK - STEP)
        mixFill = CHUNK - STEP
    }

    private fun ensurePendingCapacity(extra: Int) {
        val used = availableFrames
        if (pendingTail + extra <= pendingL.size) return
        if (used + extra <= pendingL.size) {
            System.arraycopy(pendingL, pendingHead, pendingL, 0, used)
            System.arraycopy(pendingR, pendingHead, pendingR, 0, used)
            pendingHead = 0
            pendingTail = used
            return
        }
        val nextSize = max(pendingL.size * 2, used + extra)
        val newL = FloatArray(nextSize)
        val newR = FloatArray(nextSize)
        System.arraycopy(pendingL, pendingHead, newL, 0, used)
        System.arraycopy(pendingR, pendingHead, newR, 0, used)
        pendingL = newL
        pendingR = newR
        pendingHead = 0
        pendingTail = used
    }

    private companion object {
        /** Analysis frame length, `mdx_n_fft_scale_set`. */
        const val N_FFT = 6144

        const val HOP = 1024

        /** `N_FFT / 2 + 1`: the bins a real inverse transform needs. */
        const val HALF_SPECTRUM = N_FFT / 2 + 1

        const val HALF_FRAME = N_FFT / 2

        /** `mdx_dim_f_set`. */
        const val DIM_F = MdxVocalModel.DIM_F

        /** `2 ** mdx_dim_t_set`. */
        const val DIM_T = MdxVocalModel.DIM_T

        const val BINS_TOTAL = DIM_F * DIM_T

        const val CHANNELS_TOTAL = 4 * BINS_TOTAL

        /** `hop * (dim_t - 1)`: samples per model window, 5.9 s. */
        const val CHUNK = HOP * (DIM_T - 1)

        /** 25 % overlap between windows. */
        const val OVERLAP = 0.25

        /** Samples published per window. */
        val STEP = (CHUNK * (1.0 - OVERLAP)).toInt()

        const val TAIL_PASSES = 2

        /** The models are trained at 44.1 kHz; the chain resamples to it before this point. */
        const val SAMPLE_RATE = 44100

        val HZ_PER_BIN = SAMPLE_RATE.toDouble() / N_FFT

        /** Below the lowest singable note, so only kicks and bass lines live there. */
        const val VOCAL_LOW_HZ = 85.0

        /** Width of the roll-on above [VOCAL_LOW_HZ]. */
        const val BAND_RAMP_HZ = 40.0

        /** Above the sibilance band. */
        const val VOCAL_HIGH_HZ = 16500.0

        const val DEFAULT_SUPPRESSION = 1.15f

        const val EPSILON = 1e-12
    }
}
