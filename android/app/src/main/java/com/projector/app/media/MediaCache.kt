package com.projector.app.media

import android.content.Context
import android.os.StatFs
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * Content-addressed local copy of every clip the current program needs.
 * Playback never streams: files must be fully downloaded and verified first.
 */
class MediaCache(context: Context, private val budgetMbProvider: () -> Long) {
    data class Asset(val id: String, val sha256: String, val url: String, val size: Long)

    data class Status(
        val ready: Boolean,
        val have: Int,
        val missing: Int,
        val progress: Double,
        val storageFreeMb: Long,
        val error: String?,
    )

    private val dir = File(context.filesDir, "media").apply { mkdirs() }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val http = OkHttpClient.Builder().readTimeout(60, TimeUnit.SECONDS).build()
    private var job: Job? = null
    @Volatile private var required: List<Asset> = emptyList()
    @Volatile private var bytesDone = 0L
    @Volatile private var lastError: String? = null

    var onStatus: ((Status) -> Unit)? = null

    fun fileFor(sha: String): File? {
        val f = File(dir, "$sha.mp4")
        return if (sha.isNotEmpty() && f.exists()) f else null
    }

    fun touch(sha: String) {
        fileFor(sha)?.setLastModified(System.currentTimeMillis())
    }

    fun freeMb(): Long = StatFs(dir.absolutePath).availableBytes / (1024 * 1024)

    fun status(): Status {
        val req = required
        val have = req.count { fileFor(it.sha256) != null }
        val total = req.sumOf { it.size }.coerceAtLeast(1)
        val doneBytes = req.filter { fileFor(it.sha256) != null }.sumOf { it.size } + bytesDone
        return Status(
            ready = have == req.size && lastError == null,
            have = have,
            missing = req.size - have,
            progress = (doneBytes.toDouble() / total).coerceIn(0.0, 1.0),
            storageFreeMb = freeMb(),
            error = lastError,
        )
    }

    /** Idempotent: a running sync keeps going and picks up the new list. */
    fun sync(serverUrl: String, assets: List<Asset>) {
        required = assets
        lastError = null
        val budget = budgetMbProvider() * 1024 * 1024
        if (budget > 0 && assets.distinctBy { it.sha256 }.sumOf { it.size } > budget) {
            lastError = "Program media exceeds the configured cache budget. Shorten the program or increase the cache budget."
            report()
            return
        }
        if (job?.isActive == true) return
        job = scope.launch {
            while (isActive) {
                val next = required.firstOrNull { fileFor(it.sha256) == null } ?: break
                try {
                    ensureSpace(next.size)
                    download(serverUrl, next)
                } catch (e: Exception) {
                    Log.w(TAG, "download ${next.id} failed: ${e.message}")
                    lastError = e.message ?: "download failed"
                    report()
                    return@launch
                }
                report()
            }
            report()
        }
        report()
    }

    private fun report() {
        onStatus?.invoke(status())
    }

    private fun ensureSpace(needed: Long) {
        val reserve = 300L * 1024 * 1024
        val budget = budgetMbProvider() * 1024 * 1024
        val keep = required.map { it.sha256 }.toSet()
        fun used() = dir.listFiles()?.sumOf { it.length() } ?: 0L
        fun fits(): Boolean {
            val freeOk = StatFs(dir.absolutePath).availableBytes - needed > reserve
            val budgetOk = budget <= 0 || used() + needed <= budget
            return freeOk && budgetOk
        }
        if (fits()) return
        val evictable = (dir.listFiles() ?: emptyArray())
            .filter { it.nameWithoutExtension !in keep }
            .sortedBy { it.lastModified() }
        for (f in evictable) {
            f.delete()
            if (fits()) return
        }
        throw IllegalStateException("insufficient storage for program media")
    }

    private fun download(serverUrl: String, a: Asset) {
        val url = if (a.url.startsWith("http")) a.url else serverUrl + a.url
        val tmp = File(dir, "${a.sha256}.part")
        val md = MessageDigest.getInstance("SHA-256")
        http.newCall(Request.Builder().url(url).build()).execute().use { resp ->
            if (!resp.isSuccessful) throw IllegalStateException("HTTP ${resp.code} for ${a.id}")
            val body = resp.body ?: throw IllegalStateException("empty body")
            tmp.outputStream().use { out ->
                body.byteStream().use { input ->
                    val buf = ByteArray(1 shl 16)
                    var lastReport = 0L
                    bytesDone = 0
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        md.update(buf, 0, n)
                        bytesDone += n
                        val now = System.currentTimeMillis()
                        if (now - lastReport > 1000) {
                            lastReport = now
                            report()
                        }
                    }
                }
            }
        }
        bytesDone = 0
        val hex = md.digest().joinToString("") { "%02x".format(it) }
        if (a.sha256.isNotEmpty() && hex != a.sha256) {
            tmp.delete()
            throw IllegalStateException("checksum mismatch for ${a.id}")
        }
        if (!tmp.renameTo(File(dir, "${a.sha256}.mp4"))) throw IllegalStateException("rename failed")
    }

    companion object {
        private const val TAG = "MediaCache"
    }
}
