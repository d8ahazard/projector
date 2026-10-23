package com.projector.app.player

import android.content.Context
import android.content.pm.PackageManager
import android.provider.Settings

/** Explicit shell-granted maintenance opt-in. Never grants access to an unpaired ADB host. */
object MaintenanceAccess {
    private const val PERMISSION = "android.permission.WRITE_SECURE_SETTINGS"
    fun allowed(context: Context): Boolean =
        context.checkSelfPermission(PERMISSION) == PackageManager.PERMISSION_GRANTED &&
            context.getSharedPreferences("projector", Context.MODE_PRIVATE).getBoolean("maintenance_adb", false)

    fun optIn(context: Context, enabled: Boolean) {
        context.getSharedPreferences("projector", Context.MODE_PRIVATE).edit().putBoolean("maintenance_adb", enabled).commit()
    }

    fun enable(context: Context): Boolean {
        if (!allowed(context)) return false
        Settings.Global.putInt(context.contentResolver, "adb_enabled", 1)
        Settings.Global.putInt(context.contentResolver, "adb_wifi_enabled", 1)
        return true
    }

    fun isEnabled(context: Context): Boolean = Settings.Global.getInt(context.contentResolver, "adb_wifi_enabled", 0) == 1
}
