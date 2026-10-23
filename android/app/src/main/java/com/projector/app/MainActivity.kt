package com.projector.app

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import com.projector.app.controller.ControllerActivity
import com.projector.app.player.PlayerActivity
import com.projector.app.player.Appliance

/** Routes to the player or controller depending on the stored role. */
class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        val target = if (Appliance.enabled(this) || prefs.role == Prefs.ROLE_PLAYER) PlayerActivity::class.java else ControllerActivity::class.java
        startActivity(Intent(this, target).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }
}
