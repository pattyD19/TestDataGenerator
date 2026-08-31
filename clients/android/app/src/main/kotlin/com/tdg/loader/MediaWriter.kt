package com.tdg.loader

import android.content.ContentResolver
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.StatFs
import android.provider.MediaStore
import java.io.File

/**
 * Writing into the camera roll.
 *
 * `MediaStore.insert` is the whole reason this app exists: a browser cannot do
 * it, and files merely copied into DCIM are indexed at the OEM's discretion.
 *
 * No storage permission is requested or needed. Since API 29 an app may write,
 * read back and delete media **it owns** without one, and everything here is
 * owned by this app.
 */
class MediaWriter(private val context: Context) {

    private val resolver: ContentResolver get() = context.contentResolver

    private fun collection(isVideo: Boolean): Uri =
        if (isVideo) MediaStore.Video.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
        else MediaStore.Images.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)

    /**
     * Create a pending row and hand back its URI.
     *
     * IS_PENDING hides the row from every gallery until the bytes are complete,
     * so an interrupted transfer never surfaces a half-written photo. That is
     * also why publish() is a separate step.
     */
    fun begin(item: PackItem, relativePath: String): Uri {
        val values = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, item.name)
            put(MediaStore.MediaColumns.MIME_TYPE, item.mimeType)
            put(MediaStore.MediaColumns.RELATIVE_PATH, relativePath)
            put(MediaStore.MediaColumns.IS_PENDING, 1)
            // DATE_TAKEN is milliseconds; DATE_ADDED/MODIFIED are seconds.
            // Galleries group on DATE_TAKEN, so it is the one that matters,
            // but leaving the others at "now" makes a five-year-old library
            // look like it arrived this afternoon in any view sorted by them.
            val millis = item.takenAtMillis()
            if (item.isVideo) put(MediaStore.Video.Media.DATE_TAKEN, millis)
            else put(MediaStore.Images.Media.DATE_TAKEN, millis)
            put(MediaStore.MediaColumns.DATE_MODIFIED, millis / 1000)
            put(MediaStore.MediaColumns.SIZE, item.bytes)
        }
        return resolver.insert(collection(item.isVideo), values)
            ?: throw IllegalStateException("MediaStore refused a row for ${item.name}")
    }

    fun openOutput(uri: Uri) =
        resolver.openOutputStream(uri, "w")
            ?: throw IllegalStateException("could not open $uri for writing")

    /** Clear IS_PENDING, which is what makes the asset visible to galleries. */
    fun publish(uri: Uri) {
        val values = ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }
        resolver.update(uri, values, null, null)
    }

    /** Drop a row whose transfer failed, so a retry is not blocked by a stub. */
    fun discard(uri: Uri) {
        try { resolver.delete(uri, null, null) } catch (e: Exception) { /* already gone */ }
    }

    /**
     * Delete assets this app wrote. Returns how many rows actually went.
     *
     * No RecoverableSecurityException handling and no createDeleteRequest: those
     * are for files the app does not own. Ours it does, so this is a plain
     * delete with no user prompt — which is what makes wipe usable daily.
     */
    fun delete(uris: List<String>): Int {
        var gone = 0
        for (s in uris) {
            try {
                if (resolver.delete(Uri.parse(s), null, null) > 0) gone++
            } catch (e: Exception) {
                // Someone deleted it in the gallery first. Not an error.
            }
        }
        return gone
    }

    /**
     * The distinct folders these assets live in, read from MediaStore.
     *
     * Asked *before* the delete, because afterwards the rows are gone and with
     * them the only record of where the files were. Reading it back beats
     * recording it in the receipt: it stays correct when a manifest names a
     * custom album, and needs no receipt-format change.
     */
    fun foldersOf(uris: List<String>): Set<String> {
        val out = LinkedHashSet<String>()
        val cols = arrayOf(MediaStore.MediaColumns.RELATIVE_PATH)
        for (s in uris) {
            try {
                resolver.query(Uri.parse(s), cols, null, null, null)?.use { c ->
                    if (c.moveToFirst()) c.getString(0)?.let { out.add(it) }
                }
            } catch (e: Exception) {
                // Already gone, or never ours. Nothing to clean up either way.
            }
        }
        return out
    }

    /**
     * Remove pack folders that the wipe has just emptied.
     *
     * MediaStore.delete removes rows, not the directories that held them, so a
     * wiped device kept an empty "DCIM/TDG <job>" per run. Scoped storage does
     * permit an app to remove a directory it created, provided it is empty —
     * and empty is the only case this touches. A folder still holding anything
     * is left alone, because whatever is in it is not something we wrote.
     */
    fun removeEmptyFolders(relativePaths: Set<String>): Int {
        var gone = 0
        val root = Environment.getExternalStorageDirectory() ?: return 0
        for (rel in relativePaths) {
            val dir = File(root, rel)
            try {
                if (dir.isDirectory && dir.list()?.isEmpty() == true && dir.delete()) gone++
            } catch (e: Exception) {
                // A cosmetic tidy-up must never fail a wipe that worked.
            }
        }
        return gone
    }

    /** Free bytes on the volume the pack is about to land on. */
    fun freeBytes(): Long {
        val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DCIM)
            ?: context.filesDir
        return try {
            val fs = StatFs(dir.absolutePath)
            fs.availableBlocksLong * fs.blockSizeLong
        } catch (e: Exception) {
            -1L
        }
    }
}
