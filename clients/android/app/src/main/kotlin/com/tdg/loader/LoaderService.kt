package com.tdg.loader

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.IBinder
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The fill, as a foreground service.
 *
 * This is not decoration. Android's own background limits — never mind
 * Samsung's "sleeping apps" and adaptive battery — will suspend a twenty-minute
 * network-and-IO job run any other way. A foreground service with an ongoing
 * notification is the only thing that survives, and even that is combined with
 * a receipt so a kill costs one file rather than the whole transfer.
 */
class LoaderService : Service() {

    companion object {
        const val CHANNEL = "tdg-fill"
        const val NOTIFICATION_ID = 42

        const val ACTION_LOAD = "com.tdg.loader.LOAD"
        const val ACTION_WIPE = "com.tdg.loader.WIPE"
        const val ACTION_STOP = "com.tdg.loader.STOP"
        const val EXTRA_MANIFEST_URL = "manifest_url"
        const val EXTRA_JOB_ID = "job_id"

        /** Progress, broadcast locally so the activity can render it. */
        const val BROADCAST = "com.tdg.loader.PROGRESS"
        const val EXTRA_STATE = "state"
        const val EXTRA_DONE = "done"
        const val EXTRA_TOTAL = "total"
        const val EXTRA_FILES = "files"
        const val EXTRA_OF = "of"
        const val EXTRA_MESSAGE = "message"

        @Volatile var running = false; private set
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val cancelled = AtomicBoolean(false)
    private var worker: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, "Filling the gallery",
                    NotificationManager.IMPORTANCE_LOW)
            )
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> { cancelled.set(true); return START_NOT_STICKY }
            ACTION_WIPE -> startWork(intent, wipe = true)
            else -> startWork(intent, wipe = false)
        }
        return START_NOT_STICKY
    }

    private fun startWork(intent: Intent?, wipe: Boolean) {
        if (worker?.isActive == true) return
        val jobId = intent?.getStringExtra(EXTRA_JOB_ID) ?: return stopSelf()
        val manifestUrl = intent.getStringExtra(EXTRA_MANIFEST_URL)
        cancelled.set(false)
        running = true
        startForeground(NOTIFICATION_ID, notification("Starting", 0, 0))
        worker = scope.launch {
            try {
                if (wipe) doWipe(jobId) else doLoad(jobId, manifestUrl!!)
            } catch (e: Downloader.HttpError) {
                // A pack pruned mid-fill is the one failure where the useful
                // thing to say is what survived: the receipt is written as each
                // asset lands and lives outside the pack, so everything already
                // imported is still there and still removable.
                val why = if (e.isGone) {
                    "The pack was pruned on the server while loading. " +
                        "What already landed is kept, and Wipe still removes it."
                } else {
                    e.serverMessage ?: "HTTP ${e.code}"
                }
                report("failed", 0, 0, 0, 0, why)
            } catch (e: Exception) {
                report("failed", 0, 0, 0, 0, e.message ?: e.toString())
            } finally {
                running = false
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }
    }

    // -- the fill -----------------------------------------------------------

    private suspend fun doLoad(jobId: String, manifestUrl: String) {
        report("running", 0, 0, 0, 0, "Fetching manifest")
        val base = URL(manifestUrl)
        val pack = Pack.parse(Downloader.getText(manifestUrl), base)
        val receipt = Receipt(this, pack.jobId)
        val writer = MediaWriter(this)

        val pending = pack.items.filter { !receipt.has(it.name) }
        val need = pending.sumOf { it.bytes }

        // Free space is checked before a single byte is written. Filling a
        // device until it wedges is a slow and unhelpful way to fail.
        val free = writer.freeBytes()
        if (free in 0 until need) {
            report("failed", 0, need, 0, pending.size,
                "Not enough space: need ${human(need)}, ${human(free)} free")
            return
        }

        if (pending.isEmpty()) {
            report("done", pack.totalBytes, pack.totalBytes, receipt.count,
                pack.fileCount, "Already loaded")
            return
        }

        var done = 0L
        var files = 0
        val already = receipt.count
        for (item in pending) {
            if (cancelled.get()) {
                report("cancelled", done, need, files, pending.size,
                    "Cancelled — reopen to resume")
                return
            }
            scope.ensureActive()
            writeOne(writer, receipt, pack, item)
            done += item.bytes
            files++
            val note = "${already + files} of ${pack.fileCount} files"
            updateNotification(note, done, need)
            report("running", done, need, already + files, pack.fileCount, note)
        }
        report("done", done, need, receipt.count, pack.fileCount,
            "${receipt.count} files in the gallery")
    }

    private fun writeOne(writer: MediaWriter, receipt: Receipt, pack: Pack, item: PackItem) {
        val uri: Uri = writer.begin(item, pack.relativePath)
        try {
            var written = 0L
            Downloader.openRange(item.url, 0).use { ranged ->
                writer.openOutput(uri).use { out ->
                    val buf = ByteArray(256 * 1024)
                    while (true) {
                        if (cancelled.get()) throw InterruptedException("cancelled")
                        val n = ranged.stream.read(buf)
                        if (n <= 0) break
                        out.write(buf, 0, n)
                        written += n
                    }
                    out.flush()
                }
            }
            if (written != item.bytes) {
                throw java.io.IOException(
                    "${item.name}: expected ${item.bytes} bytes, got $written")
            }
            writer.publish(uri)
            receipt.add(item.name, uri.toString())
        } catch (e: Exception) {
            // A pending row with no bytes would be invisible but real. Drop it
            // so a resumed run starts this file cleanly rather than colliding.
            writer.discard(uri)
            throw e
        }
    }

    // -- the wipe -----------------------------------------------------------

    private fun doWipe(jobId: String) {
        val receipt = Receipt(this, jobId)
        val total = receipt.count
        report("running", 0, 0, 0, total, "Removing $total assets")
        val writer = MediaWriter(this)
        val uris = receipt.uris()
        // Where they live has to be read before the rows are deleted.
        val folders = writer.foldersOf(uris)
        val gone = writer.delete(uris)
        receipt.clear()
        writer.removeEmptyFolders(folders)
        report("done", 0, 0, 0, total, "Removed $gone of $total")
    }

    // -- reporting ----------------------------------------------------------

    private fun report(state: String, done: Long, total: Long, files: Int,
                       of: Int, message: String) {
        sendBroadcast(Intent(BROADCAST).apply {
            setPackage(packageName)
            putExtra(EXTRA_STATE, state)
            putExtra(EXTRA_DONE, done)
            putExtra(EXTRA_TOTAL, total)
            putExtra(EXTRA_FILES, files)
            putExtra(EXTRA_OF, of)
            putExtra(EXTRA_MESSAGE, message)
        })
    }

    private fun notification(text: String, done: Long, total: Long): Notification {
        val tap = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val b = Notification.Builder(this, CHANNEL)
            .setContentTitle("TDG loader")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setOngoing(true)
            .setContentIntent(tap)
        if (total > 0) {
            b.setProgress(1000, ((done * 1000) / total).toInt(), false)
        }
        return b.build()
    }

    private fun updateNotification(text: String, done: Long, total: Long) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIFICATION_ID, notification(text, done, total))
    }

    override fun onDestroy() {
        scope.cancel()
        running = false
        super.onDestroy()
    }
}

fun human(n: Long): String {
    if (n < 0) return "unknown"
    val units = arrayOf("B", "KB", "MB", "GB", "TB")
    var v = n.toDouble()
    var i = 0
    while (v >= 1024 && i < units.size - 1) { v /= 1024; i++ }
    return if (i == 0) "$n B" else String.format("%.1f %s", v, units[i])
}
