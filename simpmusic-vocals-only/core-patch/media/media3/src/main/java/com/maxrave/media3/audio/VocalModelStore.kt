package com.maxrave.media3.audio

import android.app.ActivityManager
import android.content.Context
import com.maxrave.logger.Logger
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL

/** What the vocals model is doing, for anything that wants to tell the user. */
sealed interface VocalModelState {
    /** Not on disk yet, and no download running. */
    data object Missing : VocalModelState

    /** Coming down the wire; [percent] is 0-100. */
    data class Downloading(
        val percent: Int,
    ) : VocalModelState

    /** On disk and open in ONNX Runtime. */
    data object Ready : VocalModelState

    /** Could not download or could not load; [message] is shown to the user. */
    data class Failed(
        val message: String,
    ) : VocalModelState
}

/**
 * Owns the one file vocal isolation needs, and the session that runs it.
 *
 * The weights are far too big to ship in an APK — 64 MB against a 20 MB app — so they are fetched
 * once, on the first use of vocals-only, into the app's private storage and reused forever after.
 * Nothing leaves the device after that: separation runs on the phone, offline, no server.
 *
 * Two rules learned from the first build, which crashed the app on the audio thread:
 *
 * 1. **Nothing here ever blocks the audio thread.** [readyModel] only reports what a worker thread
 *    has already finished; the download and the session build both happen off to the side.
 * 2. **Every failure is caught and written down.** A missing native library, a full disk, a device
 *    out of memory: [lastError] ends up in a file next to the model and in a toast, instead of a
 *    tombstone.
 */
object VocalModelStore {
    private const val TAG = "VocalModelStore"

    private const val MODEL_NAME = "Kim_Vocal_2.onnx"

    private const val ERROR_FILE = "vocals-only-error.txt"

    /**
     * MDX-Net `Kim_Vocal_2`, the checkpoint UVR's community rates best for vocals at this size.
     * Published by `TRvlvr/model_repo` under `all_public_uvr_models`.
     */
    private const val DOWNLOAD_URL =
        "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx"

    /** Anything smaller than this is a truncated download, not a model. */
    private const val MIN_USABLE_BYTES = 60_000_000L

    /** What the release publishes; a file of any other length is a truncated or wrong download. */
    private const val EXPECTED_BYTES = 66_759_214L

    /** Set after one automatic re-download, so a hopeless model does not loop. */
    @Volatile
    private var retriedDownload = false

    /** Below this much free RAM the session build is refused rather than attempted. */
    private const val MIN_FREE_MB = 700L

    private val _state = MutableStateFlow<VocalModelState>(VocalModelState.Missing)

    val state: StateFlow<VocalModelState> = _state.asStateFlow()

    @Volatile
    private var downloader: Thread? = null

    @Volatile
    private var loader: Thread? = null

    @Volatile
    private var loaded: MdxVocalModel? = null

    /** The last thing that went wrong, for the toast and for the log file. */
    @Volatile
    var lastError: String? = null
        private set

    fun modelFile(context: Context): File = File(File(context.filesDir, "vocal-models"), MODEL_NAME)

    fun isReady(context: Context): Boolean = runCatching { modelFile(context).length() >= MIN_USABLE_BYTES }.getOrDefault(false)

    /** The session if a worker has already opened it, else null. Never blocks, never loads. */
    fun readyModel(): MdxVocalModel? = loaded

    /**
     * Kicks off the download and then the session build, in the background, once. Safe to call from
     * any thread, including the audio thread, any number of times.
     */
    fun ensureModel(context: Context) {
        if (loaded != null) return
        val appContext = context.applicationContext

        // A file that failed to load or has the wrong length is deleted by the loader, but a
        // truncated one can also arrive from an interrupted download on a previous run.
        val file = modelFile(appContext)
        if (file.isFile && file.length() != EXPECTED_BYTES && !downloading()) {
            Logger.w(TAG, "Model file is ${file.length()} bytes, expected $EXPECTED_BYTES; re-downloading")
            runCatching { file.delete() }
        }

        if (isReady(appContext)) {
            _state.value = VocalModelState.Ready
            loadInBackground(appContext)
            return
        }
        if (downloader?.isAlive == true) return
        synchronized(this) {
            if (downloader?.isAlive == true) return
            _state.value = VocalModelState.Downloading(0)
            downloader =
                Thread({
                    download(appContext)
                    if (isReady(appContext)) loadInBackground(appContext)
                }, "vocal-model-download").apply {
                    isDaemon = true
                    priority = Thread.MIN_PRIORITY
                    start()
                }
        }
    }

    private fun downloading(): Boolean = downloader?.isAlive == true

    private fun loadInBackground(context: Context) {
        if (loaded != null) return
        if (loader?.isAlive == true) return
        synchronized(this) {
            if (loaded != null || loader?.isAlive == true) return
            loader =
                Thread({
                    try {
                        // A 64 MB graph needs a lot more than 64 MB of native memory to open and
                        // run. Refusing before we try is the difference between a clear message
                        // and the native allocator killing the process.
                        val availableMb = availableMemoryMb(context)
                        Logger.i(TAG, "Loading vocals model; ${availableMb ?: -1} MB free, heap max ${Runtime.getRuntime().maxMemory() / 1048576} MB")
                        if (availableMb != null && availableMb < MIN_FREE_MB) {
                            fail(
                                "Only $availableMb MB of memory free, and vocals-only needs about $MIN_FREE_MB MB. " +
                                    "Close some apps and try again.",
                                context,
                            )
                            return@Thread
                        }
                        Logger.i(TAG, "Opening session (basic optimisation, 2 threads)")
                        val model = MdxVocalModel.load(modelFile(context))?.also { candidate ->
                            val problem = candidate.selfTest()
                            if (problem != null) {
                                Logger.e(TAG, "Model failed its self-test: $problem (${candidate.description})")
                                runCatching { candidate.close() }
                                throw IllegalStateException("Self-test failed: $problem")
                            }
                            Logger.i(TAG, "Model self-test passed: ${candidate.description}")
                        }
                        if (model != null) {
                            loaded = model
                            _state.value = VocalModelState.Ready
                        } else {
                            val reason = MdxVocalModel.FAILURE_HOLDER ?: "The model would not load."
                            handleUnusableModel(context, reason)
                        }
                    } catch (t: Throwable) {
                        handleUnusableModel(context, "${t.javaClass.simpleName}: ${t.message}")
                    }
                }, "vocal-model-load").apply {
                    isDaemon = true
                    start()
                }
        }
    }

    /**
     * The file is there but cannot be used — corrupt, truncated, or a graph this device refuses.
     * Delete it and let one automatic re-download happen, then report the reason if that fails too.
     */
    private fun handleUnusableModel(
        context: Context,
        reason: String,
    ) {
        runCatching { modelFile(context).delete() }
        if (!retriedDownload) {
            retriedDownload = true
            Logger.w(TAG, "Re-downloading the vocals model once after: $reason")
            _state.value = VocalModelState.Downloading(0)
            Thread({
                download(context)
                if (isReady(context)) loadInBackground(context)
            }, "vocal-model-redownload").apply {
                isDaemon = true
                start()
            }
            return
        }
        fail("The vocals model could not be used: $reason", context)
    }

    /** Free RAM the device has right now, or null if it cannot be read. */
    private fun availableMemoryMb(context: Context): Long? =
        runCatching {
            val manager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
            val info = ActivityManager.MemoryInfo()
            manager.getMemoryInfo(info)
            info.availMem / 1048576L
        }.getOrNull()

    /** Forgets the session (it holds the file open). The file itself stays. */
    @Synchronized
    fun release() {
        runCatching { loaded?.close() }
        loaded = null
    }

    /** Deletes a model that will not load, so the next attempt downloads it again. */
    fun discardBrokenModel(context: Context) {
        release()
        runCatching { modelFile(context).delete() }
        _state.value = VocalModelState.Missing
    }

    private fun download(context: Context) {
        val target = modelFile(context)
        val part = File(target.parentFile, "$MODEL_NAME.part")
        runCatching { target.parentFile?.mkdirs() }

        var connection: HttpURLConnection? = null
        try {
            connection =
                (URL(DOWNLOAD_URL).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 20_000
                    readTimeout = 30_000
                    instanceFollowRedirects = true
                    setRequestProperty("Accept", "application/octet-stream")
                }
            Logger.i(TAG, "Downloading vocals model from $DOWNLOAD_URL")
            val code = connection.responseCode
            if (code !in 200..299) {
                fail("Download failed (HTTP $code).", context)
                return
            }
            val total = if (connection.contentLengthLong > 0) connection.contentLengthLong else 1L
            var copied = 0L
            var lastPercent = -1

            connection.inputStream.use { input ->
                FileOutputStream(part).use { output ->
                    val buffer = ByteArray(256 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read <= 0) break
                        output.write(buffer, 0, read)
                        copied += read
                        val percent = ((copied * 100) / total).toInt().coerceIn(0, 100)
                        if (percent != lastPercent) {
                            lastPercent = percent
                            _state.value = VocalModelState.Downloading(percent)
                        }
                    }
                    output.flush()
                }
            }

            if (copied < MIN_USABLE_BYTES || (total > MIN_USABLE_BYTES && copied != total)) {
                part.delete()
                fail("The download stopped early ($copied of $total bytes). Check the connection and try again.", context)
                return
            }
            if (!part.renameTo(target)) {
                part.copyTo(target, overwrite = true)
                part.delete()
            }
            Logger.i(TAG, "Download complete: ${target.length()} bytes (expected $EXPECTED_BYTES)")
            _state.value = VocalModelState.Ready
        } catch (t: Throwable) {
            runCatching { part.delete() }
            fail("${t.javaClass.simpleName}: ${t.message}", context)
        } finally {
            runCatching { connection?.disconnect() }
        }
    }

    private fun fail(
        message: String,
        context: Context? = null,
    ) {
        lastError = message
        _state.value = VocalModelState.Failed(message)
        Logger.e(TAG, "Vocals model unavailable: $message")
        // Written next to the model so a bug report can carry the actual reason rather than
        // "it crashed".
        if (context != null) {
            runCatching {
                val file = File(context.filesDir, ERROR_FILE)
                file.writeText("$message\n")
            }
        }
    }
}
