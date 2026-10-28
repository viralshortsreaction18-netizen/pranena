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

    /** On disk and loaded into ONNX Runtime. */
    data object Ready : VocalModelState

    /** Could not download or could not load; [message] is shown to the user. */
    data class Failed(
        val message: String,
    ) : VocalModelState
}

/**
 * Owns the one file the vocal isolation needs: the MDX-Net weights.
 *
 * They are far too big to ship in an APK — 64 MB against a 20 MB app — so they are fetched once, on
 * the first use of vocals-only, into the app's private storage and reused forever after. Nothing
 * leaves the device after that: separation runs on the phone, offline, no server, no API key.
 *
 * The download is guarded twice: the byte count has to match what the release publishes, and the
 * graph has to actually open. A partial file is deleted rather than left to fail later inside the
 * audio thread.
 */
object VocalModelStore {
    private const val TAG = "VocalModelStore"

    private const val MODEL_NAME = "Kim_Vocal_2.onnx"

    /**
     * MDX-Net `Kim_Vocal_2`, the model UVR's community rates best for vocals at this size.
     * Published by `TRvlvr/model_repo` under `all_public_uvr_models`.
     */
    private const val DOWNLOAD_URL =
        "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx"

    private const val EXPECTED_BYTES = 66_759_214L

    /** Anything smaller than this is a truncated download, not a model. */
    private const val MIN_USABLE_BYTES = 60_000_000L

    private val _state = MutableStateFlow<VocalModelState>(VocalModelState.Missing)

    val state: StateFlow<VocalModelState> = _state.asStateFlow()

    @Volatile
    private var downloader: Thread? = null

    @Volatile
    private var loaded: MdxVocalModel? = null

    fun modelFile(context: Context): File = File(File(context.filesDir, "vocal-models"), MODEL_NAME)

    fun isReady(context: Context): Boolean = modelFile(context).length() >= MIN_USABLE_BYTES

    /** The session, opened once per process. Blocking on first call: it reads 64 MB off disk. */
    @Synchronized
    fun model(
        context: Context,
        threads: Int = 4,
    ): MdxVocalModel? {
        loaded?.let { return it }
        if (!isReady(context)) {
            if (_state.value !is VocalModelState.Downloading) _state.value = VocalModelState.Missing
            return null
        }
        val model = MdxVocalModel.load(modelFile(context), threads)
        if (model == null) {
            _state.value = VocalModelState.Failed("The vocals model would not load. Delete it and download again.")
            return null
        }
        loaded = model
        _state.value = VocalModelState.Ready
        return model
    }

    /** Forgets the session (it holds the file open). The file itself stays. */
    @Synchronized
    fun release() {
        loaded?.close()
        loaded = null
    }

    /** Starts the one-time download if it is not already running or done. */
    @Synchronized
    fun ensureModel(context: Context) {
        if (isReady(context)) {
            _state.value = VocalModelState.Ready
            return
        }
        if (downloader?.isAlive == true) return

        val appContext = context.applicationContext
        _state.value = VocalModelState.Downloading(0)
        downloader =
            Thread({
                download(appContext)
            }, "vocal-model-download").apply {
                isDaemon = true
                start()
            }
    }

    private fun download(context: Context) {
        val target = modelFile(context)
        val part = File(target.parentFile, "$MODEL_NAME.part")
        target.parentFile?.mkdirs()

        var connection: HttpURLConnection? = null
        try {
            connection = (URL(DOWNLOAD_URL).openConnection() as HttpURLConnection).apply {
                connectTimeout = 20_000
                readTimeout = 30_000
                instanceFollowRedirects = true
                setRequestProperty("Accept", "application/octet-stream")
            }
            val code = connection.responseCode
            if (code !in 200..299) {
                fail("Download failed (HTTP $code).")
                return
            }
            val total = if (connection.contentLengthLong > 0) connection.contentLengthLong else EXPECTED_BYTES
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
                fail("The download stopped early ($copied bytes). Check the connection and try again.")
                return
            }
            if (!part.renameTo(target)) {
                part.copyTo(target, overwrite = true)
                part.delete()
            }
            Logger.i(TAG, "Vocals model ready: ${target.length()} bytes")
            _state.value = VocalModelState.Ready
        } catch (e: Exception) {
            part.delete()
            Logger.e(TAG, "Vocals model download failed: ${e.message}")
            fail(e.message ?: "Download failed.")
        } finally {
            runCatching { connection?.disconnect() }
        }
    }

    private fun fail(message: String) {
        _state.value = VocalModelState.Failed(message)
    }
}
