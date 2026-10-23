package com.projector.app.player

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.projector.app.Prefs

/**
 * Starts the player after boot. Android 10+ only allows this when the app holds
 * "display over other apps"; grant it once with:
 *   adb shell appops set com.projector.app SYSTEM_ALERT_WINDOW allow
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val prefs = Prefs(context)
        if (!Appliance.enabled(context) && prefs.role != Prefs.ROLE_PLAYER) return
        try { MaintenanceAccess.enable(context) } catch (e: Exception) {
            android.util.Log.w("ProjectorBoot", "Maintenance ADB could not be enabled", e)
        }
        val launch = Intent(context, PlayerActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        try {
            context.startActivity(launch)
            android.util.Log.i("ProjectorBoot", "Requested player start after ${intent.action}")
        } catch (e: Exception) {
            android.util.Log.e("ProjectorBoot", "Player start blocked after ${intent.action}", e)
        }
    }
}
