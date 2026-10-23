package com.projector.app

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import java.util.UUID

class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("projector", Context.MODE_PRIVATE)
    private val isTv = context.packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK)

    var serverUrl: String
        get() = sp.getString("server_url", "") ?: ""
        set(v) = sp.edit().putString("server_url", v.trimEnd('/')).apply()

    /** "player" on TV sticks, "controller" on phones unless overridden. */
    var role: String
        get() = sp.getString("role", null) ?: if (isTv) ROLE_PLAYER else ROLE_CONTROLLER
        set(v) = sp.edit().putString("role", v).apply()

    val deviceId: String
        get() {
            var id = sp.getString("device_id", null)
            if (id == null) {
                id = UUID.randomUUID().toString().replace("-", "").take(12)
                sp.edit().putString("device_id", id).apply()
            }
            return id
        }

    val deviceName: String
        get() = sp.getString("device_name", null) ?: "${Build.MANUFACTURER} ${Build.MODEL}".trim()

    var cacheBudgetMb: Long
        get() = sp.getLong("cache_budget_mb", 0L)
        set(v) = sp.edit().putLong("cache_budget_mb", v).apply()

    var managementStatus: String
        get() = sp.getString("management_status", "{}") ?: "{}"
        set(v) = sp.edit().putString("management_status", v).apply()
    var lastCommandId: String
        get() = sp.getString("last_command", "") ?: ""
        set(v) = sp.edit().putString("last_command", v).commit().let { }
    var updateTarget: Long
        get() = sp.getLong("update_target", 0)
        set(v) = sp.edit().putLong("update_target", v).commit().let { }

    companion object {
        const val ROLE_PLAYER = "player"
        const val ROLE_CONTROLLER = "controller"

        fun normalizeUrl(input: String): String {
            var s = input.trim()
            if (s.isEmpty()) return s
            if (!s.startsWith("http://") && !s.startsWith("https://")) s = "http://$s"
            val hostPart = s.substringAfter("://").substringBefore("/")
            if (!hostPart.contains(":")) s = s.replaceFirst(hostPart, "$hostPart:8000")
            return s.trimEnd('/')
        }
    }
}
