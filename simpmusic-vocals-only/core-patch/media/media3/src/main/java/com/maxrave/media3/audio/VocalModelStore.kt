package com.maxrave.media3.audio

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

    private fun loadInBackground(context: Context) {
        if (loaded != null) return
        if (loader?.isAlive == true) return
        synchronized(this) {
            if (loaded != null || loader?.isAlive == true) return
            loader =
                Thread({
                    try {
                        val model = MdxVocalModel.load(modelFile(context))
                        if (model != null) {
                            loaded = model
                            _state.value = VocalModelState.Ready
                        } else {
                            fail(MdxVocalModel.FAILURE_HOLDER ?: "The model would not load.", context)
                        }
                    } catch (t: Throwable) {
                        fail("${t.javaClass.simpleName}: ${t.message}", context)
                    }
                }, "vocal-model-load").apply {
                    isDaemon = true
                    start()
                }
        }
    }

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

            if (copied < MIN_USABLE_BYTES) {
                part.delete()
                fail("The download stopped early ($copied bytes). Check the connection and try again.", context)
                return
            }
            if (!part.renameTo(target)) {
                part.copyTo(target, overwrite = true)
                part.delete()
            }
            Logger.i(TAG, "Vocals model ready: ${target.length()} bytes")
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
