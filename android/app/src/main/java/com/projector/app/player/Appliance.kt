package com.projector.app.player

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager

/** Enabled only by explicit provisioning; never changes the phone/controller role. */
object Appliance {
    fun enabled(context: Context): Boolean = context.packageManager.getComponentEnabledSetting(
        ComponentName(context, "com.projector.app.ApplianceHome")
    ) == PackageManager.COMPONENT_ENABLED_STATE_ENABLED

    fun isHome(context: Context): Boolean = context.packageManager.resolveActivity(
        Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME), PackageManager.MATCH_DEFAULT_ONLY
    )?.activityInfo?.packageName == context.packageName
}
