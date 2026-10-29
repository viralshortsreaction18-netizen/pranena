package com.maxrave.media3.audio

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.media3.common.C
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.common.util.UnstableApi
import com.maxrave.logger.Logger
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.roundToInt

/**
 * Media3 [AudioProcessor] that plays the **vocals only** — the accompaniment is removed by an
 * MDX-Net model running on the device, not by a filter.
 *
 * Sits in the chain ahead of the equalizer so the EQ shapes the isolated voice, and follows the same
 * contract as [EqualizerAudioProcessor]: [enabled] is sampled on every buffer, so flipping the
 * toggle takes effect within a few seconds rather than on the next track.
 *
 * Three rules, all of them learned from the build that crashed:
 *
 * 1. **Nothing here may throw.** Every entry point catches `Throwable` — not just `Exception`,
 *    because the failures that killed the app were an `OutOfMemoryError` or a missing native
 *    library, and those are `Error`s. On failure the processor stops isolating for the rest of the
 *    session and passes audio through untouched.
 * 2. **Nothing here may block on the model.** Loading 64 MB of weights happens on a worker thread
 *    in [VocalModelStore]; this class only ever asks whether it is already done.
 * 3. **Nothing here holds memory it is not using.** The extractor is ~28 MB and is dropped the
 *    moment vocals-only is switched off.
 */
@UnstableApi
class MdxVocalAudioProcessor(
    private val context: Context,
    private val enabled: () -> Boolean,
) : BaseAudioProcessor() {
    private var sampleRate = 0
    private var channelCount = 0
    private var extractor: MdxVocalExtractor? = null
    private var wasEnabled = false
    private var toldAboutDownload = false
    private var toldAboutOtherRate = false
    private var toldAboutFailure = false

    /** Set when something went wrong; vocals-only stays off for the rest of the session. */
    private var disabledByError = false

    /** True while this processor holds the process-wide separation slot. */
    private var ownsEngine = false

    private var inLeft = FloatArray(0)
    private var inRight = FloatArray(0)
    private var outLeft = FloatArray(0)
    private var outRight = FloatArray(0)

    override fun onConfigure(inputAudioFormat: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat =
        try {
            if (inputAudioFormat.encoding != C.ENCODING_PCM_16BIT) {
                AudioProcessor.AudioFormat.NOT_SET
            } else if (inputAudioFormat.channelCount != 1 && inputAudioFormat.channelCount != 2) {
                AudioProcessor.AudioFormat.NOT_SET
            } else if (inputAudioFormat.sampleRate != MODEL_SAMPLE_RATE) {
                if (!toldAboutOtherRate) {
                    toldAboutOtherRate = true
                    Logger.w(TAG, "Vocals only needs $MODEL_SAMPLE_RATE Hz, got ${inputAudioFormat.sampleRate}")
                }
                AudioProcessor.AudioFormat.NOT_SET
            } else {
                sampleRate = inputAudioFormat.sampleRate
                channelCount = inputAudioFormat.channelCount
                extractor = null
                wasEnabled = false
                Logger.i(TAG, "configured: ${inputAudioFormat.sampleRate} Hz, ${inputAudioFormat.channelCount} ch")
                inputAudioFormat
            }
        } catch (t: Throwable) {
            reportFailure("configure", t)
            AudioProcessor.AudioFormat.NOT_SET
        }

    override fun onFlush() {
        runCatching { extractor?.flush() }
    }

    override fun onReset() {
        releaseEngine()
        extractor = null
        inLeft = FloatArray(0)
        inRight = FloatArray(0)
        outLeft = FloatArray(0)
        outRight = FloatArray(0)
        sampleRate = 0
        channelCount = 0
        wasEnabled = false
    }

    override fun queueInput(inputBuffer: ByteBuffer) {
        val start = inputBuffer.position()
        val current = if (enabled() && !disabledByError) obtainExtractor() else null
        if (current == null) {
            if (wasEnabled) {
                extractor?.flush()
                wasEnabled = false
                // Nothing is using the 28 MB any more; let it go.
                extractor = null
                releaseEngine()
            }
            passThrough(inputBuffer, start)
            return
        }

        try {
            wasEnabled = true
            inputBuffer.order(ByteOrder.nativeOrder())
            val bytesPerFrame = channelCount * SHORT_BYTES
            val frameCount = inputBuffer.remaining() / bytesPerFrame
            if (frameCount <= 0) {
                inputBuffer.position(inputBuffer.limit())
                return
            }

            ensureScratchCapacity(frameCount)
            if (channelCount == 2) {
                for (i in 0 until frameCount) {
                    inLeft[i] = inputBuffer.short * INV_PCM16
                    inRight[i] = inputBuffer.short * INV_PCM16
                }
            } else {
                for (i in 0 until frameCount) {
                    val sample = inputBuffer.short * INV_PCM16
                    inLeft[i] = sample
                    inRight[i] = sample
                }
            }

            current.writeStereo(inLeft, inRight, 0, frameCount)
            current.lastError?.let { reportModelError(it) }
            writeAvailable(current, makeRoom = true)
        } catch (t: Throwable) {
            // Never let the audio thread take the app with it: drop isolation and replay this
            // buffer untouched.
            reportFailure("separation", t)
            disabledByError = true
            extractor = null
            wasEnabled = false
            passThrough(inputBuffer, start)
        }
    }

    /**
     * The last few seconds of a track sit in a chunk that is not full yet. Without this they are
     * dropped, which is heard as a song ending early.
     */
    override fun onQueueEndOfStream() {
        val current = extractor ?: return
        if (!wasEnabled) return
        try {
            current.finish()
            writeAvailable(current, makeRoom = false)
        } catch (t: Throwable) {
            reportFailure("end of stream", t)
            disabledByError = true
            extractor = null
            wasEnabled = false
        }
    }

    private fun passThrough(
        inputBuffer: ByteBuffer,
        start: Int,
    ) {
        runCatching {
            inputBuffer.position(start)
            val remaining = inputBuffer.remaining()
            if (remaining <= 0) {
                replaceOutputBuffer(0).flip()
                return
            }
            val output = replaceOutputBuffer(remaining)
            output.put(inputBuffer)
            output.flip()
        }
    }

    private fun writeAvailable(
        current: MdxVocalExtractor,
        makeRoom: Boolean,
    ) {
        val available = current.availableFrames
        if (available <= 0) {
            if (makeRoom) replaceOutputBuffer(0).flip()
            return
        }
        if (outLeft.size < available) {
            outLeft = FloatArray(available)
            outRight = FloatArray(available)
        }
        val read = current.readStereo(outLeft, outRight, 0, available)
        val bytesPerFrame = channelCount * SHORT_BYTES
        val output = replaceOutputBuffer(read * bytesPerFrame)
        if (channelCount == 2) {
            for (i in 0 until read) {
                output.putShort(floatToPcm16(outLeft[i]))
                output.putShort(floatToPcm16(outRight[i]))
            }
        } else {
            for (i in 0 until read) {
                output.putShort(floatToPcm16(0.5f * (outLeft[i] + outRight[i])))
            }
        }
        output.flip()
    }

    /**
     * Returns the extractor only if the model is **already** loaded. If it is not, this kicks off
     * the download/load in the background and returns null, so audio plays normally in the meantime
     * and vocals-only starts working by itself once the model lands.
     */
    private fun obtainExtractor(): MdxVocalExtractor? {
        extractor?.let { return it }

        // The player builds more than one ExoPlayer (the main one, a precache one and, with
        // crossfade on, a second playing one). Each would otherwise hold its own 28 MB separator
        // and run its own inference, which is how a phone runs out of memory. The first processor
        // to need the engine gets it; the others pass audio through untouched.
        if (!ownsEngine && !ENGINE_OWNER.compareAndSet(null, this)) {
            if (ENGINE_OWNER.get() !== this) return null
        }
        ownsEngine = true
        val model = VocalModelStore.readyModel()
        if (model == null) {
            Logger.i(TAG, "model not ready yet; state=${VocalModelStore.state.value}")
            VocalModelStore.ensureModel(context)
            if (!toldAboutDownload) {
                toldAboutDownload = true
                toast("Preparing vocals-only: downloading the 64 MB model once. It runs offline after that.")
            }
            VocalModelStore.lastError?.let { reportModelError(it) }
            return null
        }
        return try {
            Logger.i(TAG, "creating separator (${Runtime.getRuntime().maxMemory() / 1048576} MB heap max)")
            MdxVocalExtractor(model).also {
                extractor = it
                Logger.i(TAG, "separator ready")
            }
        } catch (t: Throwable) {
            reportFailure("preparing the separator", t)
            disabledByError = true
            null
        }
    }

    private fun reportModelError(message: String) {
        if (toldAboutFailure) return
        toldAboutFailure = true
        val model = VocalModelStore.readyModel()
        val detail = model?.let { "${it.description} | $message" } ?: message
        log("model: $detail")
        toast("Vocals-only could not separate this song: $message")
    }

    private fun reportFailure(
        where: String,
        t: Throwable,
    ) {
        val message = "${t.javaClass.simpleName}: ${t.message}"
        Logger.e(TAG, "Vocals-only failed at $where: $message")
        log("$where: $message")
        if (!toldAboutFailure) {
            toldAboutFailure = true
            toast("Vocals-only stopped ($message). Playback continues normally.")
        }
    }

    private fun releaseEngine() {
        if (ownsEngine) {
            ENGINE_OWNER.compareAndSet(this, null)
            ownsEngine = false
        }
    }

    /** Writes the reason to a file the user can send, not just to logcat. */
    private fun log(message: String) {
        runCatching {
            File(context.filesDir, "vocals-only-error.txt").appendText("$message\n")
        }
    }

    private fun toast(message: String) {
        Handler(Looper.getMainLooper()).post {
            runCatching { Toast.makeText(context, message, Toast.LENGTH_LONG).show() }
        }
    }

    private fun ensureScratchCapacity(frames: Int) {
        if (inLeft.size < frames) {
            inLeft = FloatArray(frames)
            inRight = FloatArray(frames)
        }
    }

    private fun floatToPcm16(sample: Float): Short {
        val scaled = (sample * PCM16_SCALE).roundToInt()
        return scaled.coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt()).toShort()
    }

    private companion object {
        const val TAG = "MdxVocalAudioProcessor"

        /** Which processor instance currently owns the (expensive) separation engine. */
        val ENGINE_OWNER = java.util.concurrent.atomic.AtomicReference<MdxVocalAudioProcessor?>(null)
        const val SHORT_BYTES = 2
        const val PCM16_SCALE = 32_768.0f
        const val INV_PCM16 = 1.0f / 32_768.0f
        const val MODEL_SAMPLE_RATE = 44_100
    }
}
