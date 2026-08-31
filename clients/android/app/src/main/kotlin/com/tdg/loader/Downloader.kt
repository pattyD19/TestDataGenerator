package com.tdg.loader

import org.json.JSONObject
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL

/**
 * HTTP, using only what the platform ships.
 *
 * No OkHttp. HttpURLConnection does ranged, streamed GETs perfectly well, and
 * a loader with no dependencies is a loader that still builds in two years.
 */
object Downloader {

    private const val CONNECT_TIMEOUT = 15_000
    private const val READ_TIMEOUT = 30_000

    /**
     * A refusal from the control plane, with the status kept separate from the
     * text.
     *
     * The server explains itself in JSON — a pruned pack answers 410 with the
     * reason — so the useful message is already written and only needs
     * unwrapping. Keeping [code] alongside it lets a caller react to the
     * *kind* of refusal without matching on English.
     */
    class HttpError(
        val code: Int,
        val serverMessage: String?,
        val url: String,
    ) : java.io.IOException(serverMessage ?: "HTTP $code from $url") {

        /** The pack existed and was deliberately deleted to reclaim disk. */
        val isGone get() = code == 410
    }

    fun getText(url: String): String {
        val conn = open(url)
        try {
            requireOk(conn, url)
            return conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }

    /**
     * Open a byte stream, optionally skipping the first [from] bytes.
     *
     * The Range header is what turns a dropped Wi-Fi connection halfway through
     * a 4 GB clip into a resumed transfer rather than a restarted one. If the
     * server ignores it and returns 200, the caller must treat the response as
     * starting from zero — hence [Ranged.startsAt].
     */
    fun openRange(url: String, from: Long): Ranged {
        val conn = open(url)
        if (from > 0) conn.setRequestProperty("Range", "bytes=$from-")
        requireOk(conn, url)
        val partial = conn.responseCode == HttpURLConnection.HTTP_PARTIAL
        return Ranged(conn, conn.inputStream, if (partial) from else 0L)
    }

    class Ranged(
        private val conn: HttpURLConnection,
        val stream: InputStream,
        val startsAt: Long,
    ) : AutoCloseable {
        override fun close() {
            try { stream.close() } catch (e: Exception) { }
            conn.disconnect()
        }
    }

    private fun open(url: String): HttpURLConnection =
        (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = CONNECT_TIMEOUT
            readTimeout = READ_TIMEOUT
            requestMethod = "GET"
            setRequestProperty("User-Agent", "tdg-loader/0.1 (Android)")
        }

    private fun requireOk(conn: HttpURLConnection, url: String) {
        val code = conn.responseCode
        if (code in 200..299) return
        val body = try {
            conn.errorStream?.bufferedReader()?.use { it.readText() }
        } catch (e: Exception) { null }
        // Every refusal from this server is {"error": "..."}. Show that
        // sentence, not the JSON it arrived in.
        val message = try {
            body?.let { JSONObject(it).optString("error", "").ifEmpty { null } }
        } catch (e: Exception) { body?.take(200) }
        throw HttpError(code, message, url)
    }
}
