package com.projector.app.player

import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import com.projector.app.Prefs

/** One-time ADB setup. DUMP permission limits this receiver to shell/system callers. */
class ProvisionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!context.packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK)) return
        val enabled = intent.getBooleanExtra("enabled", false)
        val prefs = Prefs(context)
        if (enabled) {
            val url = intent.getStringExtra("server_url") ?: return
            val uri = android.net.Uri.parse(url)
            if (uri.scheme !in listOf("http", "https") || uri.host.isNullOrEmpty()) return
            prefs.serverUrl = url
            prefs.role = Prefs.ROLE_PLAYER
        }
        context.packageManager.setComponentEnabledSetting(
            ComponentName(context, "com.projector.app.ApplianceHome"),
            if (enabled) PackageManager.COMPONENT_ENABLED_STATE_ENABLED else PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
            PackageManager.DONT_KILL_APP
        )
        if (!enabled || intent.hasExtra("maintenance_adb")) {
            MaintenanceAccess.optIn(context, enabled && intent.getBooleanExtra("maintenance_adb", false))
        }
        if (enabled) MaintenanceAccess.enable(context)
        resultCode = android.app.Activity.RESULT_OK
        resultData = prefs.deviceId
    }
}
