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
 * shape the mix, never take the music out of it.
 *
 * Everything here is defensive on purpose. Loading 64 MB of weights can fail in ways that are not
 * `Exception`s — a missing native library (`UnsatisfiedLinkError`), a phone that cannot spare the
 * memory (`OutOfMemoryError`) — and the first build of this code crashed the whole app on the audio
 * thread for exactly that reason. Every entry point now catches `Throwable`, records what happened
 * in [lastError], and hands back `false` so playback simply continues untouched.
 *
 * One session is expensive to build and cheap to reuse, so [VocalModelStore] keeps a single
 * instance for the process, loaded on a worker thread. [run] is single-shot and not thread safe:
 * call it from one thread at a time, which the audio thread guarantees.
 */
class MdxVocalModel private constructor(
    private val environment: OrtEnvironment,
    private val session: OrtSession,
) : AutoCloseable {
    private val inputName: String = session.inputNames.first()

    @Volatile
    var lastError: String? = null
        private set

    /**
     * Runs one chunk: [spectrum] is `[1, 4, 2048, 256]` flattened, and the vocal estimate is written
     * into [out] (same size). Returns false, having replaced nothing, if the graph refused.
     *
     * [out] is passed in rather than returned because the caller reuses one 8 MB buffer per chunk
     * instead of allocating a fresh one 512 times a song.
     */
    fun run(
        spectrum: FloatArray,
        out: FloatArray,
    ): Boolean {
        if (out.size != spectrum.size) {
            lastError = "Output buffer is ${out.size}, expected ${spectrum.size}."
            return false
        }
        val shape = longArrayOf(1L, CHANNELS.toLong(), DIM_F.toLong(), DIM_T.toLong())
        return try {
            OnnxTensor.createTensor(environment, FloatBuffer.wrap(spectrum), shape).use { input ->
                session.run(mapOf(inputName to input)).use { result ->
                    val tensor = result.get(0) as? OnnxTensor
                    if (tensor == null) {
                        lastError = "Model returned ${result.get(0).javaClass.simpleName}, not a tensor."
                        return false
                    }
                    val buffer = tensor.floatBuffer
                    if (buffer.remaining() < out.size) {
                        lastError = "Model returned ${buffer.remaining()} values, expected ${out.size}."
                        return false
                    }
                    buffer.get(out, 0, out.size)
                    true
                }
            }
        } catch (t: Throwable) {
            // OrtException, OOM while allocating the tensor, a native abort surfacing as an
            // Error — none of them should take the player down with them.
            lastError = "${t.javaClass.simpleName}: ${t.message}"
            Logger.e(TAG, "Inference failed: $lastError")
            false
        }
    }

    override fun close() {
        runCatching { session.close() }
    }

    companion object {
        private const val TAG = "MdxVocalModel"

        /** `dim_c` of the MDX-Net checkpoint format: `[L_re, L_im, R_re, R_im]`. */
        const val CHANNELS = 4

        /** `mdx_dim_f_set`: the frequency bins the model was trained on. */
        const val DIM_F = 2048

        /** `2 ** mdx_dim_t_set`: frames per chunk, at a hop of 1024 that is 5.9 s of audio. */
        const val DIM_T = 256

        /**
         * Opens [modelFile] with ONNX Runtime, or returns `null` with [FAILURE_HOLDER] filled in
         * when the file is missing, half downloaded, or a graph this device cannot run.
         *
         * Never call this on the audio thread: it reads 64 MB off disk and builds an inference
         * session. [VocalModelStore] runs it on a worker.
         */
        fun load(
            modelFile: File,
            threads: Int = 2,
        ): MdxVocalModel? {
            if (!modelFile.isFile || modelFile.length() < MIN_MODEL_BYTES) {
                FAILURE_HOLDER = "Model file missing or truncated (${modelFile.length()} bytes)."
                return null
            }
            return try {
                val environment = OrtEnvironment.getEnvironment()
                val options =
                    OrtSession.SessionOptions().apply {
                        setIntraOpNumThreads(threads.coerceIn(1, 4))
                        setInterOpNumThreads(1)
                        // BASIC_OPT keeps the graph optimiser away from the risky fusions, and
                        // memory patterns off keeps the session's working set small on a phone
                        // that is already running a music player.
                        setOptimizationLevel(OrtSession.SessionOptions.OptLevel.BASIC_OPT)
                        runCatching { setMemoryPatternOptimization(false) }
                        runCatching { addConfigEntry("session.use_env_allocators", "0") }
                    }
                MdxVocalModel(environment, environment.createSession(modelFile.absolutePath, options))
            } catch (t: Throwable) {
                // UnsatisfiedLinkError when the native library did not make it into the APK,
                // OutOfMemoryError when the device cannot spare 64 MB, OrtException when the file
                // is not a model. All of them are reported, none of them crash the app.
                FAILURE_HOLDER = "${t.javaClass.simpleName}: ${t.message}"
                Logger.e(TAG, "Could not load $modelFile: $FAILURE_HOLDER")
                null
            }
        }

        /** Why the last [load] failed; read by the store to show the user something useful. */
        @Volatile
        var FAILURE_HOLDER: String? = null
            private set

        /** A Kim_Vocal_2 download that stopped early is not a model; refuse anything this small. */
        private const val MIN_MODEL_BYTES = 30_000_000L
    }
}
