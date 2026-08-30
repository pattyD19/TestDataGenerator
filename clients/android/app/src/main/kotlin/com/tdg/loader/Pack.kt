package com.tdg.loader

import org.json.JSONObject
import java.net.URL

/**
 * The manifest is the contract with the generator. The app parses it and
 * nothing else: every item already carries the URL to fetch it from, so the
 * loader never has to know how the server lays out paths.
 */
data class PackItem(
    val name: String,
    val kind: String,          // "image" | "video"
    val bytes: Long,
    val sha256: String,
    val takenAtUtc: String,    // ISO-8601, always with an offset since schema v2
    val url: String,
) {
    val isVideo get() = kind == "video"

    val mimeType: String
        get() = when {
            isVideo -> "video/mp4"
            name.endsWith(".heic", true) -> "image/heic"
            name.endsWith(".png", true) -> "image/png"
            else -> "image/jpeg"
        }

    /** Epoch millis, which is what MediaStore's DATE_TAKEN wants. */
    fun takenAtMillis(): Long =
        try {
            java.time.OffsetDateTime.parse(takenAtUtc).toInstant().toEpochMilli()
        } catch (e: Exception) {
            // A v1 manifest has no offset. Falling back to the local zone is
            // the same guess iOS Photos makes, and is why schema v2 exists.
            try {
                java.time.LocalDateTime.parse(takenAtUtc)
                    .atZone(java.time.ZoneId.systemDefault()).toInstant().toEpochMilli()
            } catch (e2: Exception) {
                System.currentTimeMillis()
            }
        }
}

data class Pack(
    val jobId: String,
    val album: String,
    val fileCount: Int,
    val totalBytes: Long,
    val items: List<PackItem>,
) {
    /** DCIM/<album> — a folder most galleries surface as an album. */
    val relativePath get() = "DCIM/$album"

    companion object {
        fun parse(json: String, base: URL): Pack {
            val o = JSONObject(json)
            val arr = o.getJSONArray("items")
            val items = ArrayList<PackItem>(arr.length())
            for (i in 0 until arr.length()) {
                val it = arr.getJSONObject(i)
                val rel = it.optString("url", "")
                items.add(
                    PackItem(
                        name = it.getString("name"),
                        kind = it.optString("kind", "image"),
                        bytes = it.getLong("bytes"),
                        sha256 = it.optString("sha256", ""),
                        takenAtUtc = it.optString("taken_at_utc", it.optString("taken_at", "")),
                        // Item URLs are server-relative; resolve against the
                        // manifest's own URL so the app carries no host config.
                        url = if (rel.startsWith("http")) rel else URL(base, rel).toString(),
                    )
                )
            }
            return Pack(
                jobId = o.getString("job_id"),
                album = o.optString("album", "TDG ${o.getString("job_id")}"),
                fileCount = o.optInt("file_count", items.size),
                totalBytes = o.optLong("total_bytes", items.sumOf { it.bytes }),
                items = items,
            )
        }
    }
}
