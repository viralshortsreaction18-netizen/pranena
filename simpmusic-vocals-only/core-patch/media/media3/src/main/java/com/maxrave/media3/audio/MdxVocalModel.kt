package com.maxrave.media3.audio

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import com.maxrave.logger.Logger
import java.io.File
import java.nio.FloatBuffer

/**
 * A loaded MDX-Net vocal model: an ONNX graph that eats the complex STFT of a stereo chunk
 * (`[1, 4, 2048, 256]`, real and imaginary parts of both channels) and returns the same tensor for
 * the isolated vocal stem.
 *
 * This is the difference between the previous build and this one. The old "Spleeter" code ran a
 * hand-written filter bank and called it a U-Net: no weights were ever loaded, so it could only
 * shape the mix, never take the music out of it — which is exactly what "it still has music in it"
 * sounds like. Every weight in this class came out of `Kim_Vocal_2.onnx`, trained on real songs.
 *
 * One session is expensive to build (66 MB of weights) and cheap to reuse, so the store keeps a
 * single instance for the process. Inference itself is single-shot and allocates its own tensors;
 * call it from one thread at a time, which the audio thread guarantees.
 */
class MdxVocalModel private constructor(
    private val environment: OrtEnvironment,
    private val session: OrtSession,
) : AutoCloseable {
    private val inputName: String = session.inputNames.first()

    /**
     * Runs one chunk. [spectrum] is `[1, 4, 2048, 256]` flattened; returns the same shape for the
     * vocal estimate, or `null` if the graph refused the input.
     */
    fun run(spectrum: FloatArray): FloatArray? {
        val shape =
            longArrayOf(
                1L,
                CHANNELS.toLong(),
                DIM_F.toLong(),
                DIM_T.toLong(),
            )
        return try {
            OnnxTensor.createTensor(environment, FloatBuffer.wrap(spectrum), shape).use { input ->
                session.run(mapOf(inputName to input)).use { result ->
                    val output = result.get(0) as OnnxTensor
                    val buffer = output.floatBuffer
                    val out = FloatArray(buffer.remaining())
                    buffer.get(out)
                    out
                }
            }
        } catch (e: Exception) {
            Logger.e("MdxVocalModel", "Inference failed: ${e.message}")
            null
        }
    }

    override fun close() {
        runCatching { session.close() }
    }

    companion object {
        /** `dim_c` of the MDX-Net checkpoint format: `[L_re, L_im, R_re, R_im]`. */
        const val CHANNELS = 4

        /** `mdx_dim_f_set`: the frequency bins the model was trained on. */
        const val DIM_F = 2048

        /** `2 ** mdx_dim_t_set`: frames per chunk, at a hop of 1024 that is 5.9 s of audio. */
        const val DIM_T = 256

        /**
         * Opens [modelFile] with ONNX Runtime, or returns `null` when the file is missing, half
         * downloaded, or not a graph this runtime can run.
         */
        fun load(
            modelFile: File,
            threads: Int,
        ): MdxVocalModel? {
            if (!modelFile.isFile || modelFile.length() < MIN_MODEL_BYTES) return null
            return try {
                val environment = OrtEnvironment.getEnvironment()
                val options =
                    OrtSession.SessionOptions().apply {
                        setIntraOpNumThreads(threads.coerceIn(1, 4))
                        setInterOpNumThreads(1)
                        setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
                    }
                MdxVocalModel(environment, environment.createSession(modelFile.absolutePath, options))
            } catch (e: Exception) {
                Logger.e("MdxVocalModel", "Could not load $modelFile: ${e.message}")
                null
            }
        }

        /** A Kim_Vocal_2 download that stopped early is not a model; refuse anything this small. */
        private const val MIN_MODEL_BYTES = 30_000_000L
    }
}
