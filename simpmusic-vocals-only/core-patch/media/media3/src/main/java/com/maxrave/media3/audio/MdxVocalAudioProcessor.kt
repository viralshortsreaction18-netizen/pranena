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
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.roundToInt

/**
 * Media3 [AudioProcessor] that plays the **vocals only** — the accompaniment is removed by an
 * MDX-Net model running on the device, not by a filter.
 *
 * Sits in the chain ahead of the equalizer so the EQ shapes the isolated voice, and follows the same
 * contract as [EqualizerAudioProcessor]: [enabled] is sampled on every buffer, so flipping the
 * toggle takes effect on the next few seconds of audio rather than on the next track.
 *
 * The model needs 44.1 kHz, which is what the models were trained on. The chain puts a
 * `SonicAudioProcessor` ahead of this one for exactly that reason; if a stream still arrives at
 * another rate, this processor declines to activate and the audio plays untouched instead of being
 * separated at the wrong frequency scale.
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

    private var inLeft = FloatArray(0)
    private var inRight = FloatArray(0)
    private var outLeft = FloatArray(0)
    private var outRight = FloatArray(0)

    override fun onConfigure(inputAudioFormat: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat {
        if (inputAudioFormat.encoding != C.ENCODING_PCM_16BIT) return AudioProcessor.AudioFormat.NOT_SET
        if (inputAudioFormat.channelCount != 1 && inputAudioFormat.channelCount != 2) {
            return AudioProcessor.AudioFormat.NOT_SET
        }
        if (inputAudioFormat.sampleRate != MODEL_SAMPLE_RATE) {
            if (!toldAboutOtherRate) {
                toldAboutOtherRate = true
                Logger.w("MdxVocalAudioProcessor", "Vocals only needs ${MODEL_SAMPLE_RATE} Hz, got ${inputAudioFormat.sampleRate}; passing audio through.")
            }
            return AudioProcessor.AudioFormat.NOT_SET
        }
        sampleRate = inputAudioFormat.sampleRate
        channelCount = inputAudioFormat.channelCount
        extractor = null
        wasEnabled = false
        return inputAudioFormat
    }

    override fun onFlush() {
        extractor?.flush()
    }

    override fun onReset() {
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
        val remaining = inputBuffer.remaining()
        if (remaining == 0) return

        val current = if (enabled()) obtainExtractor() else null
        if (current == null) {
            if (wasEnabled) {
                extractor?.flush()
                wasEnabled = false
            }
            val output = replaceOutputBuffer(remaining)
            output.put(inputBuffer)
            output.flip()
            return
        }
        wasEnabled = true

        inputBuffer.order(ByteOrder.nativeOrder())
        val bytesPerFrame = channelCount * SHORT_BYTES
        val frameCount = remaining / bytesPerFrame
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
        writeAvailable(current, makeRoom = true)
    }

    /**
     * The last few seconds of a track sit in a chunk that is not full yet. Without this they are
     * dropped, which is heard as a song ending early.
     */
    override fun onQueueEndOfStream() {
        val current = extractor ?: return
        if (!wasEnabled) return
        current.finish()
        writeAvailable(current, makeRoom = false)
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
     * The extractor appears the moment the model is on disk and opened, so turning vocals-only on
     * while the model is still downloading starts working on its own once the download lands —
     * no need to toggle twice or restart the track.
     */
    private fun obtainExtractor(): MdxVocalExtractor? {
        extractor?.let { return it }
        if (!VocalModelStore.isReady(context)) {
            VocalModelStore.ensureModel(context)
            if (!toldAboutDownload) {
                toldAboutDownload = true
                toast("Preparing vocals-only. The 64 MB model downloads once, then works offline.")
            }
            return null
        }
        val model =
            VocalModelStore.model(context)
                ?: run {
                    toast("The vocals model could not be loaded.")
                    return null
                }
        val created = MdxVocalExtractor(model)
        extractor = created
        return created
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
        const val SHORT_BYTES = 2
        const val PCM16_SCALE = 32_768.0f
        const val INV_PCM16 = 1.0f / 32_768.0f
        const val MODEL_SAMPLE_RATE = 44_100
    }
}
