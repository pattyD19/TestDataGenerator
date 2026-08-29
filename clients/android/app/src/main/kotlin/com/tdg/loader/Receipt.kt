package com.tdg.loader

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * What this app wrote, and where.
 *
 * Two jobs, both essential. It makes a fill **resumable** — Android will kill a
 * twenty-minute foreground service eventually, and restarting a 64 GB transfer
 * from zero is not acceptable. And it makes **wipe** exact: the app deletes the
 * content URIs it recorded and nothing else.
 *
 * Keyed on the MediaStore URI rather than the filename, deliberately. Filenames
 * are not a reliable handle for an imported asset — iOS renames every one — and
 * the URI is what ContentResolver.delete actually needs.
 */
class Receipt(context: Context, val jobId: String) {

    private val file = File(context.filesDir, "receipt-$jobId.json")
    private val entries = LinkedHashMap<String, String>()   // name -> content uri

    init { load() }

    val count: Int get() = entries.size

    fun has(name: String) = entries.containsKey(name)

    fun uris(): List<String> = entries.values.toList()

    @Synchronized
    fun add(name: String, uri: String) {
        entries[name] = uri
        save()
    }

    @Synchronized
    fun remove(name: String) {
        entries.remove(name)
        save()
    }

    @Synchronized
    fun clear() {
        entries.clear()
        file.delete()
    }

    private fun load() {
        if (!file.exists()) return
        try {
            val arr = JSONArray(file.readText())
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                entries[o.getString("name")] = o.getString("uri")
            }
        } catch (e: Exception) {
            // A torn write from a killed process. Losing the receipt costs a
            // re-download; a crash loop on startup would cost far more.
            file.delete()
            entries.clear()
        }
    }

    private fun save() {
        val arr = JSONArray()
        entries.forEach { (name, uri) ->
            arr.put(JSONObject().put("name", name).put("uri", uri))
        }
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(arr.toString())
        tmp.renameTo(file)      // atomic: a receipt is never half-written
    }

    companion object {
        fun knownJobs(context: Context): List<String> =
            context.filesDir.listFiles()
                ?.mapNotNull { f ->
                    f.name.takeIf { it.startsWith("receipt-") && it.endsWith(".json") }
                        ?.removePrefix("receipt-")?.removeSuffix(".json")
                }
                ?.sorted().orEmpty()
    }
}
