package com.projector.app.management

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import com.projector.app.Prefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

class AppUpdater(private val activity: Activity) {
    private val prefs = Prefs(activity)
    private val client = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(10, TimeUnit.SECONDS).readTimeout(60, TimeUnit.SECONDS).build()
    var busy = false
        private set

    companion object {
        fun recover(context: Context) {
            val prefs = Prefs(context)
            val saved = status(context)
            val state = saved.optString("state")
            if (state == "downloading" || (state == "installing" || state == "awaiting_confirmation") && context.packageManager.packageInstaller.mySessions.isEmpty()) {
                prefs.updateTarget = 0
                record(context, saved.optString("id"), "interrupted", "Update was interrupted; press Update app to retry")
            }
        }
        @Suppress("DEPRECATION")
        fun version(info: PackageInfo): Long = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()

        fun record(context: Context, id: String, state: String, message: String) {
            Prefs(context).managementStatus = JSONObject().put("id", id).put("state", state).put("message", message)
                .put("at", System.currentTimeMillis()).toString()
        }

        fun status(context: Context): JSONObject {
            val prefs = Prefs(context)
            val saved = try { JSONObject(prefs.managementStatus) } catch (_: Exception) { JSONObject() }
            val installed = version(context.packageManager.getPackageInfo(context.packageName, 0))
            if (prefs.updateTarget > 0 && installed >= prefs.updateTarget) {
                saved.put("state", "installed").put("message", "Update installed")
                prefs.managementStatus = saved.toString(); prefs.updateTarget = 0
            }
            return saved.put("version_code", installed)
                .put("install_permission", context.packageManager.canRequestPackageInstalls())
                .put("boot_permission", Settings.canDrawOverlays(context))
        }
    }

    @Suppress("DEPRECATION")
    private fun signers(info: PackageInfo): Set<String> {
        val signatures = if (Build.VERSION.SDK_INT >= 28) info.signingInfo?.apkContentsSigners else info.signatures
        return signatures?.map { sig -> MessageDigest.getInstance("SHA-256").digest(sig.toByteArray()).joinToString("") { "%02x".format(it) } }?.toSet() ?: emptySet()
    }

    suspend fun update(id: String, release: JSONObject, progress: (String, String) -> Unit) {
        if (busy || activity.packageManager.packageInstaller.mySessions.isNotEmpty()) {
            progress("busy", "An update is already running; finish or cancel its installer prompt first"); return
        }
        if (!activity.packageManager.canRequestPackageInstalls()) {
            record(activity, id, "permission_required", "Allow Projector to install updates on this device, then press Update again")
            progress("permission_required", "Approve the install permission on this device, then retry")
            activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${activity.packageName}")))
            return
        }
        busy = true
        var sessionId: Int? = null
        try {
            val size = release.getLong("size")
            require(size in 1..100_000_000) { "APK size is outside the allowed limit" }
            val sha = release.getString("sha256")
            require(sha.matches(Regex("[a-f0-9]{64}"))) { "Invalid APK digest" }
            require(release.getString("url") == "/download/app.apk?sha256=$sha") { "Unexpected update URL" }
            require(release.getString("package") == activity.packageName) { "Wrong application" }
            record(activity, id, "downloading", "Downloading signed app update")
            progress("downloading", "Downloading signed app update")
            val file = withContext(Dispatchers.IO) {
                val target = File(activity.cacheDir, "projector-update.apk")
                val digest = MessageDigest.getInstance("SHA-256")
                client.newCall(Request.Builder().url(prefs.serverUrl + release.getString("url")).build()).execute().use { response ->
                    check(response.isSuccessful) { "APK download failed: HTTP ${response.code}" }
                    val body = response.body ?: error("Empty APK download")
                    var count = 0L
                    body.byteStream().use { input -> target.outputStream().use { output ->
                        val buffer = ByteArray(65536)
                        while (true) {
                            val n = input.read(buffer); if (n < 0) break
                            count += n; check(count <= size) { "APK exceeds advertised size" }
                            digest.update(buffer, 0, n); output.write(buffer, 0, n)
                        }
                    } }
                    check(count == size) { "Incomplete APK download" }
                }
                check(digest.digest().joinToString("") { "%02x".format(it) } == sha) { "APK checksum mismatch" }
                target
            }
            val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
            val current = activity.packageManager.getPackageInfo(activity.packageName, flags)
            val candidate = activity.packageManager.getPackageArchiveInfo(file.absolutePath, flags) ?: error("Invalid APK")
            UpdatePolicy.validate(activity.packageName, candidate.packageName, version(current), version(candidate),
                release.getLong("version_code"), signers(current), signers(candidate))
            val installer = activity.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                setAppPackageName(activity.packageName); setSize(size)
                // Android decides eligibility for unattended self-update. Always handle its confirmation fallback.
                if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(if (prefs.role == Prefs.ROLE_CONTROLLER)
                    PackageInstaller.SessionParams.USER_ACTION_REQUIRED else PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
            val sid = installer.createSession(params); sessionId = sid
            withContext(Dispatchers.IO) {
                installer.openSession(sid).use { session ->
                    session.openWrite("base.apk", 0, size).use { output ->
                        file.inputStream().use { it.copyTo(output) }; session.fsync(output)
                    }
                }
            }
            prefs.updateTarget = version(candidate)
            record(activity, id, "installing", "Installing update; Android may request confirmation on this device")
            progress("installing", "Installing update; Android may request confirmation on this device")
            val callback = Intent(activity, InstallResultReceiver::class.java).putExtra("command_id", id)
            val mutable = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
            val pending = PendingIntent.getBroadcast(activity, sid, callback, PendingIntent.FLAG_UPDATE_CURRENT or mutable)
            installer.openSession(sid).use { it.commit(pending.intentSender) }
        } catch (e: Exception) {
            sessionId?.let { try { activity.packageManager.packageInstaller.abandonSession(it) } catch (_: Exception) {} }
            prefs.updateTarget = 0
            record(activity, id, "failed", e.message ?: "Update failed")
            progress("failed", e.message ?: "Update failed")
        } finally { busy = false }
    }
}

class InstallResultReceiver : BroadcastReceiver() {
    @Suppress("DEPRECATION")
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra("command_id") ?: return
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirmation = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
                try {
                    check(confirmation != null) { "Installer did not provide a confirmation screen" }
                    AppUpdater.record(context, id, "awaiting_confirmation", "Confirm installation on this device")
                    context.startActivity(confirmation.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                } catch (e: Exception) { AppUpdater.record(context, id, "failed", e.message ?: "Cannot open installer") }
            }
            PackageInstaller.STATUS_SUCCESS -> AppUpdater.record(context, id, "installed", "Update installed")
            else -> {
                Prefs(context).updateTarget = 0
                AppUpdater.record(context, id, "failed", intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "Installation cancelled or failed")
            }
        }
    }
}
