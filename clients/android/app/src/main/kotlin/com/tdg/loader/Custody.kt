package com.tdg.loader

import android.annotation.SuppressLint
import android.content.Context
import android.os.Build
import android.provider.Settings
import org.json.JSONObject

/**
 * Keeping a copy of the receipt somewhere the app cannot take with it.
 *
 * The receipt on this device is authoritative and always will be — it is what
 * [LoaderService] reads to resume a fill and to wipe exactly what it wrote.
 * But it lives in app storage, so uninstalling the app destroys it while
 * leaving every asset in the gallery. The bytes stay; the only record of which
 * bytes they are goes. Nothing can then remove them precisely, and "a test you
 * cannot undo is a test you run once" stops being true.
 *
 * So after a fill, the receipt is also deposited with the control plane, and a
 * freshly installed app can ask for it back. The pack itself does not have to
 * still exist for that: recovery works after a prune, which is the situation it
 * is actually for.
 *
 * **Every call here is best effort.** A control plane that has moved, gone
 * away, or was never reachable must never fail a fill or block a wipe — the
 * device's own receipt is what those depend on. The backup is a second chance,
 * not a dependency.
 */
object Custody {

    /**
     * A device id that survives this app being uninstalled.
     *
     * `ANDROID_ID` is per-app-signing-key and per-user, and it is reset by a
     * factory reset — which is the right lifetime here, since a factory reset
     * takes the assets with it and leaves nothing to recover.
     */
    @SuppressLint("HardwareIds")
    fun deviceId(context: Context): String =
        Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
            ?: "android-unknown"

    /** What a human picks from when several devices carry the same pack. */
    fun deviceName(): String = "${Build.MANUFACTURER} ${Build.MODEL}".trim()

    private fun url(host: String, token: String, device: String? = null): String {
        val base = host.trimEnd('/') + "/api/receipts?token=" + token
        return if (device == null) base
        else base + "&device=" + java.net.URLEncoder.encode(device, "UTF-8")
    }

    /**
     * Deposit this device's receipt. Returns true if the control plane took it.
     */
    fun deposit(context: Context, host: String, token: String, receipt: Receipt): Boolean {
        if (host.isBlank() || token.isBlank()) return false
        return try {
            val entries = JSONObject()
            receipt.entries().forEach { (name, uri) -> entries.put(name, uri) }
            val body = JSONObject()
                .put("device", deviceId(context))
                .put("platform", "android")
                .put("device_name", deviceName())
                .put("entries", entries)
            Downloader.send(url(host, token), "POST", body.toString())
            true
        } catch (e: Exception) {
            false                       // see the class comment: never fatal
        }
    }

    /**
     * Ask the control plane for this device's receipt for [token]'s pack, and
     * adopt it locally. Returns how many entries were recovered.
     *
     * Only ever called when the local receipt is empty. Adopting over a receipt
     * that already has entries could only lose information, since the device is
     * the source of truth whenever it still has one.
     */
    fun recover(context: Context, host: String, token: String, receipt: Receipt): Int {
        if (host.isBlank() || token.isBlank() || receipt.count > 0) return 0
        return try {
            val body = Downloader.getText(url(host, token, deviceId(context)))
            val entries = JSONObject(body).getJSONObject("entries")
            val restored = LinkedHashMap<String, String>()
            entries.keys().forEach { name -> restored[name] = entries.getString(name) }
            receipt.adopt(restored)
            restored.size
        } catch (e: Exception) {
            0                           // no backup, or no server; carry on
        }
    }

    /**
     * Withdraw the copy after a wipe. What is on the device is what the record
     * is for; once the assets are gone the copy is a stale claim that something
     * still needs cleaning up.
     */
    fun forget(context: Context, host: String, token: String) {
        if (host.isBlank() || token.isBlank()) return
        try {
            Downloader.send(url(host, token, deviceId(context)), "DELETE", null)
        } catch (e: Exception) {
            // Nothing to do about it, and nothing depends on it.
        }
    }
}
