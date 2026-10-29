package com.maxrave.media3.audio

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import ai.onnxruntime.TensorInfo
import com.maxrave.logger.Logger
import java.io.File
import java.nio.FloatBuffer

/**
 * A loaded MDX-Net vocal model: an ONNX graph that eats the complex STFT of a stereo chunk —
 * `[1, 4, dim_f, dim_t]`, real and imaginary parts of both channels — and returns the isolated
 * vocal stem at the same shape.
 *
 * **The shape comes from the model, not from this file.** The previous version hard-coded
 * `[1, 4, 2048, 256]`; if the checkpoint declared anything else, every inference was rejected and
 * the only thing the user saw was "could not separate this song". Now the declared input shape is
 * read from the session, checked, and exposed as [dimF] / [dimT] for the extractor to build its
 * STFT around. A dynamic (symbolic) shape falls back to the MDX-Net defaults.
 *
 * Everything here is defensive on purpose. Loading 64 MB of weights can fail in ways that are not
 * `Exception`s — a missing native library (`UnsatisfiedLinkError`), a phone that cannot spare the
 * memory (`OutOfMemoryError`). Every entry point catches `Throwable`, records what happened in
 * [lastError] and hands back `false`, so playback continues untouched.
 */
class MdxVocalModel private constructor(
    private val environment: OrtEnvironment,
    private var session: OrtSession,
    private val modelFile: File,
    private val threads: Int,
    dimF: Int,
    dimT: Int,
) : AutoCloseable {
    private var inputName: String = session.inputNames.first()

    /** Bins the model accepts, as verified by [selfTest] rather than assumed. */
    var dimF: Int = dimF
        private set

    /** Frames per chunk the model accepts. */
    var dimT: Int = dimT
        private set

    /** Rows the model expects: `4 * dimF * dimT`. */
    var elementCount: Int = CHANNELS * dimF * dimT
        private set

    @Volatile
    var lastError: String? = null
        private set

    /** Human-readable description of the graph, for the diagnostics file. */
    val description: String by lazy {
        val inputs = session.inputInfo.entries.joinToString { "${it.key}=${describe(it.value.info as? TensorInfo)}" }
        val outputs = session.outputInfo.entries.joinToString { "${it.key}=${describe(it.value.info as? TensorInfo)}" }
        "inputs[$inputs] outputs[$outputs] using dimF=$dimF dimT=$dimT"
    }

    /**
     * Runs one chunk: [spectrum] is `elementCount` floats, and the vocal estimate is written into
     * [out] (same size). Returns false, having replaced nothing, if the graph refused.
     *
     * [out] is passed in rather than returned because the caller reuses one buffer per chunk
     * instead of allocating a fresh several-MB array 512 times a song.
     */
    fun run(
        spectrum: FloatArray,
        out: FloatArray,
    ): Boolean {
        if (spectrum.size != elementCount || out.size != elementCount) {
            lastError = "Buffer size ${spectrum.size}/${out.size} does not match ${elementCount}."
            return false
        }
        val shape = longArrayOf(1L, CHANNELS.toLong(), dimF.toLong(), dimT.toLong())
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
            lastError = "${t.javaClass.simpleName}: ${t.message}"
            Logger.e(TAG, "Inference failed: $lastError (${description})")
            false
        }
    }

    /**
     * Runs one silent chunk through the graph. Done once, on a worker thread, when the model is
     * first opened: a checkpoint that cannot infer on this device is reported then, with the
     * reason, instead of failing in the middle of a song.
     */
    fun selfTest(): String? {
        if (attempt()) return null
        val firstError = lastError ?: "Inference failed."

        // The graph refused the shape we assumed. Walk the shapes the MDX-Net family actually
        // ships with and keep the one that runs; a wrong guess here is exactly the
        // "ORT_INVALID_ARGUMENT: invalid dimensions" the user saw.
        for ((f, t) in CANDIDATE_SHAPES) {
            if (f == dimF && t == dimT) continue
            Logger.i(TAG, "Self-test failed ($firstError); trying dimF=$f dimT=$t")
            val rebuilt = rebuild(f, t) ?: continue
            session = rebuilt
            dimF = f
            dimT = t
            elementCount = CHANNELS * f * t
            inputName = session.inputNames.first()
            if (attempt()) {
                Logger.i(TAG, "Model works with dimF=$f dimT=$t")
                return null
            }
        }
        return firstError
    }

    /** One inference on a silent chunk. */
    private fun attempt(): Boolean {
        val zeros = FloatArray(elementCount)
        val out = FloatArray(elementCount)
        if (!run(zeros, out)) return false
        if (out.any { !it.isFinite() }) {
            lastError = "Inference produced NaN/Inf on silence."
            return false
        }
        return true
    }

    private fun rebuild(
        f: Int,
        t: Int,
    ): OrtSession? =
        try {
            val options =
                OrtSession.SessionOptions().apply {
                    setIntraOpNumThreads(threads.coerceIn(1, 4))
                    setInterOpNumThreads(1)
                    setOptimizationLevel(OrtSession.SessionOptions.OptLevel.BASIC_OPT)
                    runCatching { setMemoryPatternOptimization(false) }
                    runCatching { setCPUArenaAllocator(false) }
                }
            environment.createSession(modelFile.absolutePath, options).also { runCatching { session.close() } }
        } catch (t: Throwable) {
            lastError = "${t.javaClass.simpleName}: ${t.message}"
            null
        }

    override fun close() {
        runCatching { session.close() }
    }

    companion object {
        private const val TAG = "MdxVocalModel"

        /** `dim_c` of the MDX-Net checkpoint format: `[L_re, L_im, R_re, R_im]`. */
        const val CHANNELS = 4

        /** `mdx_dim_f_set` for the Kim/MDX-Net family; used when the graph declares a dynamic axis. */
        const val DEFAULT_DIM_F = 2048

        /** `2 ** mdx_dim_t_set` for the Kim/MDX-Net family. */
        const val DEFAULT_DIM_T = 256

        /**
         * Shapes the MDX-Net checkpoints in circulation actually declare, tried in order when the
         * declared or default shape is refused: Kim/MDX-Net-B `2048x256`, UVR-MDX-NET `3072x256`,
         * the half-length `2048x128`, and `3072x128`.
         */
        private val CANDIDATE_SHAPES =
            listOf(
                2048 to 256,
                3072 to 256,
                2048 to 128,
                3072 to 128,
            )

        /** Why the last [load] failed; read by the store to show the user something useful. */
        @Volatile
        var FAILURE_HOLDER: String? = null
            private set

        /**
         * Opens [modelFile] with ONNX Runtime and reads the shape it wants, or returns `null` with
         * [FAILURE_HOLDER] filled in.
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
                        setOptimizationLevel(OrtSession.SessionOptions.OptLevel.BASIC_OPT)
                        runCatching { setMemoryPatternOptimization(false) }
                        // Keeps the session's own arena smaller; a phone is already running a
                        // music player and a Compose UI.
                        runCatching { setCPUArenaAllocator(false) }
                    }
                val session = environment.createSession(modelFile.absolutePath, options)

                val info = session.inputInfo.values.firstOrNull()?.info as? TensorInfo
                val declared = info?.shape
                val dims =
                    if (declared != null && declared.size == 4 && declared[1] == CHANNELS.toLong()) {
                        val f = if (declared[2] > 0) declared[2].toInt() else DEFAULT_DIM_F
                        val t = if (declared[3] > 0) declared[3].toInt() else DEFAULT_DIM_T
                        f to t
                    } else {
                        DEFAULT_DIM_F to DEFAULT_DIM_T
                    }
                Logger.i(
                    TAG,
                    "Model opened: declared=${declared?.joinToString(",") ?: "dynamic"}, " +
                        "dimF=${dims.first}, dimT=${dims.second}",
                )
                MdxVocalModel(environment, session, modelFile, threads, dims.first, dims.second)
            } catch (t: Throwable) {
                // UnsatisfiedLinkError when the native library did not make it into the APK,
                // OutOfMemoryError when the device cannot spare the memory, OrtException when the
                // file is not a model. All reported, none of them crash the app.
                FAILURE_HOLDER = "${t.javaClass.simpleName}: ${t.message}"
                Logger.e(TAG, "Could not load $modelFile: $FAILURE_HOLDER")
                null
            }
        }

        private fun describe(info: TensorInfo?): String {
            if (info == null) return "?"
            val shape = info.shape?.joinToString(",") ?: "dynamic"
            return "${info.type}[$shape]"
        }

        /** A Kim_Vocal_2 download that stopped early is not a model; refuse anything this small. */
        private const val MIN_MODEL_BYTES = 30_000_000L
    }
}
