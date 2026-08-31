package com.tdg.loader

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.lifecycle.lifecycleScope
import org.json.JSONObject

/**
 * One screen: type where the control plane is and the six digits it showed
 * you, then fill or wipe.
 *
 * The pairing code alone resolves to a pack — a phone has a keypad and no way
 * to know a twelve-hex-digit job id.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var host: EditText
    private lateinit var code: EditText
    private lateinit var summary: TextView
    private lateinit var receipts: TextView
    private lateinit var progress: ProgressBar
    private lateinit var loadBtn: Button
    private lateinit var cancelBtn: Button
    private lateinit var wipeBtn: Button

    private var manifestUrl: String? = null
    private var jobId: String? = null

    private val prefs by lazy { getSharedPreferences("tdg", Context.MODE_PRIVATE) }

    private val progressReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context?, intent: Intent?) {
            intent ?: return
            val state = intent.getStringExtra(LoaderService.EXTRA_STATE) ?: return
            val done = intent.getLongExtra(LoaderService.EXTRA_DONE, 0)
            val total = intent.getLongExtra(LoaderService.EXTRA_TOTAL, 0)
            val msg = intent.getStringExtra(LoaderService.EXTRA_MESSAGE) ?: ""
            summary.text = msg
            if (total > 0) {
                progress.visibility = View.VISIBLE
                progress.progress = ((done * 1000) / total).toInt()
            }
            val busy = state == "running"
            cancelBtn.visibility = if (busy) View.VISIBLE else View.GONE
            loadBtn.isEnabled = !busy && manifestUrl != null
            if (!busy) refreshReceipts()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        host = findViewById(R.id.host)
        code = findViewById(R.id.code)
        summary = findViewById(R.id.summary)
        receipts = findViewById(R.id.receipts)
        progress = findViewById(R.id.progress)
        loadBtn = findViewById(R.id.load)
        cancelBtn = findViewById(R.id.cancel)
        wipeBtn = findViewById(R.id.wipe)

        host.setText(prefs.getString("host", ""))

        findViewById<Button>(R.id.pair).setOnClickListener { pair() }
        loadBtn.setOnClickListener { startFill() }
        cancelBtn.setOnClickListener {
            startService(Intent(this, LoaderService::class.java)
                .setAction(LoaderService.ACTION_STOP))
        }
        wipeBtn.setOnClickListener { confirmWipe() }

        askForNotifications()
        refreshReceipts()
    }

    override fun onStart() {
        super.onStart()
        ContextCompat.registerReceiver(
            this, progressReceiver, IntentFilter(LoaderService.BROADCAST),
            ContextCompat.RECEIVER_NOT_EXPORTED)
    }

    override fun onStop() {
        super.onStop()
        unregisterReceiver(progressReceiver)
    }

    /**
     * The ongoing notification is what keeps a long fill alive on Android 13+,
     * so it is worth asking for. A refusal is survivable — the service still
     * runs — so this never blocks the screen.
     */
    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
    }

    private fun pair() {
        val base = host.text.toString().trim().trimEnd('/')
            .let { if (it.startsWith("http")) it else "http://$it" }
        val pin = code.text.toString().trim()
        if (pin.length != 6) {
            summary.text = "The pairing code is six digits."
            return
        }
        prefs.edit().putString("host", base).apply()
        summary.text = "Looking up $pin…"
        loadBtn.isEnabled = false

        lifecycleScope.launch {
            try {
                val body = withContext(Dispatchers.IO) {
                    Downloader.getText("$base/api/pair/$pin")
                }
                val o = JSONObject(body)
                jobId = o.getString("job_id")
                manifestUrl = base + o.getString("manifest_url")
                val label = o.optString("label", "")
                val free = MediaWriter(this@MainActivity).freeBytes()
                val need = o.getLong("total_bytes")
                summary.text = buildString {
                    append(if (label.isNotEmpty()) "$label\n" else "")
                    append("${o.getInt("file_count")} files, ${human(need)}\n")
                    append("${human(free)} free on this device")
                    if (free in 0 until need) append("  — NOT ENOUGH SPACE")
                }
                loadBtn.isEnabled = free < 0 || free >= need
            } catch (e: Downloader.HttpError) {
                // The server already explains a pruned, unfinished or unknown
                // pack in words meant to be read. Prefixing it with "could not
                // find that pack" would contradict the 410, which says the
                // pack was found and is deliberately gone.
                manifestUrl = null
                summary.text = e.serverMessage
                    ?: "Could not find that pack: HTTP ${e.code}"
            } catch (e: Exception) {
                manifestUrl = null
                summary.text = "Could not reach the control plane: ${e.message}"
            }
        }
    }

    private fun startFill() {
        val url = manifestUrl ?: return
        progress.visibility = View.VISIBLE
        progress.progress = 0
        startForegroundService(Intent(this, LoaderService::class.java).apply {
            action = LoaderService.ACTION_LOAD
            putExtra(LoaderService.EXTRA_MANIFEST_URL, url)
            putExtra(LoaderService.EXTRA_JOB_ID, jobId)
        })
    }

    private fun refreshReceipts() {
        val jobs = Receipt.knownJobs(this)
        if (jobs.isEmpty()) {
            receipts.text = "Nothing yet."
            wipeBtn.isEnabled = false
            return
        }
        receipts.text = jobs.joinToString("\n") { j ->
            "$j — ${Receipt(this, j).count} assets"
        }
        wipeBtn.isEnabled = true
    }

    /**
     * Deleting is the half that makes this usable daily, but it is still a
     * delete: confirm, and say exactly how many assets are going.
     */
    private fun confirmWipe() {
        val jobs = Receipt.knownJobs(this)
        val total = jobs.sumOf { Receipt(this, it).count }
        AlertDialog.Builder(this)
            .setTitle("Remove $total assets?")
            .setMessage("Deletes only what this app wrote — nothing else in the " +
                "gallery is touched.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Remove") { _, _ ->
                jobs.forEach { j ->
                    startForegroundService(
                        Intent(this, LoaderService::class.java).apply {
                            action = LoaderService.ACTION_WIPE
                            putExtra(LoaderService.EXTRA_JOB_ID, j)
                        })
                }
            }
            .show()
    }
}
