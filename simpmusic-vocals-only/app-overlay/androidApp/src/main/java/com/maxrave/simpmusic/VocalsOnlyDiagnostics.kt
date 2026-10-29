package com.maxrave.simpmusic

import android.content.Context
import android.os.Build
import com.maxrave.logger.Logger
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Crash and progress recorder for diagnostics builds.
 *
 * Written because "it still crashes" is not something anyone can fix from the outside: when the
 * vocals-only path dies, the reason has to survive the process. This keeps a rolling record in
 * three places:
 *
 * 1. the app's own log (Settings → Developer option → App log, which has an export/share button),
 * 2. `files/vocals-only.txt` inside the app's private storage,
 * 3. `Android/data/<package>/files/vocals-only.txt`, reachable from any file manager without root.
 *
 * It also installs itself as the default uncaught exception handler, so a Java-level crash is
 * written down before the system kills the process. A native crash (SIGSEGV/SIGABRT) cannot be
 * caught, which is exactly why every step of the vocals-only path also logs *before* it runs: the
 * last line in the file names the operation that never came back.
 */
object VocalsOnlyDiagnostics {
    private const val TAG = "VocalsOnly"

    private const val FILE_NAME = "vocals-only.txt"

    /** Keep the file bounded; a few hundred KB is plenty for a bug report. */
    private const val MAX_BYTES = 512 * 1024

    @Volatile
    private var appContext: Context? = null

    private val startedAt = System.currentTimeMillis()

    fun install(context: Context) {
        appContext = context.applicationContext
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            runCatching { recordCrash(thread, throwable) }
            previous?.uncaughtException(thread, throwable)
        }
        appendLine(
            "INIT",
            "diagnostics active: ${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} " +
                "(SDK ${Build.VERSION.SDK_INT}), heap ${Runtime.getRuntime().maxMemory() / 1048576} MB",
            null,
        )
    }

    /** A step that is about to run. Logged *before* it does, so a native abort still leaves a trail. */
    fun step(name: String) = appendLine("STEP", name, null)

    /** Something that happened and is worth keeping. */
    fun log(message: String) {
        Logger.i(TAG, message)
        appendLine("INFO", message, null)
    }

    /** Something went wrong but was survived. */
    fun error(
        message: String,
        throwable: Throwable? = null,
    ) {
        Logger.e(TAG, message, throwable)
        appendLine("ERROR", message, throwable)
    }

    /** Where the record lives, for the "send me that file" instruction. */
    fun filePath(context: Context): String {
        val external = context.getExternalFilesDir(null) ?: context.filesDir
        return File(external, FILE_NAME).absolutePath
    }

    private fun recordCrash(
        thread: Thread,
        throwable: Throwable,
    ) {
        Logger.e(TAG, "Uncaught exception on ${thread.name}", throwable)
        appendLine(
            "CRASH",
            "uncaught on thread '${thread.name}' after ${(System.currentTimeMillis() - startedAt) / 1000}s",
            throwable,
        )
    }

    private fun appendLine(
        level: String,
        message: String,
        throwable: Throwable?,
    ) {
        val body =
            buildString {
                append(SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US).format(Date()))
                append(' ')
                append(level)
                append(' ')
                append(message)
                append('\n')
                if (throwable != null) {
                    val writer = StringWriter()
                    throwable.printStackTrace(PrintWriter(writer))
                    append(writer.toString())
                    append('\n')
                }
            }
        write(level, body)
    }

    private fun write(
        level: String,
        body: String,
    ) {
        val context = appContext ?: return
        val targets =
            listOfNotNull(
                File(context.filesDir, FILE_NAME),
                context.getExternalFilesDir(null)?.let { File(it, FILE_NAME) },
            )
        for (file in targets) {
            runCatching {
                if (file.exists() && file.length() > MAX_BYTES) {
                    val kept = file.readText().takeLast(MAX_BYTES / 2)
                    file.writeText(kept)
                }
                file.appendText(body)
            }
        }
        if (level == "CRASH") {
            // Also make it visible without opening the app: the app's log screen reads this tag.
            runCatching { Logger.e(TAG, body) }
        }
    }
}
