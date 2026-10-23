package com.projector.app.player

import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.view.Gravity
import android.widget.Button
import android.widget.TextView

/** TV-sized native controls with explicit D-pad focus, independent of phone UI styling. */
class TvControls(private val context: Context) {
    val ink = Color.rgb(244, 246, 250)
    val muted = Color.rgb(164, 175, 193)
    val accent = Color.rgb(255, 160, 88)
    fun dp(value: Int) = (value * context.resources.displayMetrics.density).toInt()

    fun shape(fill: Int, radius: Int = 14, stroke: Int = Color.TRANSPARENT) = GradientDrawable().apply {
        setColor(fill); cornerRadius = dp(radius).toFloat(); setStroke(dp(1), stroke)
    }

    fun label(value: String, size: Float, color: Int = ink, bold: Boolean = false) = TextView(context).apply {
        text = value; textSize = size; setTextColor(color)
        typeface = Typeface.create("sans-serif", if (bold) Typeface.BOLD else Typeface.NORMAL)
        includeFontPadding = false
        gravity = Gravity.CENTER_VERTICAL
    }

    fun button(value: String, primary: Boolean = false, action: () -> Unit) = Button(context).apply {
        text = value; textSize = 15f; isAllCaps = false
        typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
        minWidth = 0; minHeight = dp(46); minimumHeight = dp(46)
        setPadding(dp(18), dp(10), dp(18), dp(10))
        backgroundTintList = null
        background = StateListDrawable().apply {
            addState(intArrayOf(android.R.attr.state_focused), shape(if (primary) Color.rgb(255, 190, 133) else Color.rgb(41, 52, 72), 14, Color.WHITE))
            addState(intArrayOf(android.R.attr.state_pressed), shape(Color.rgb(218, 133, 74)))
            addState(intArrayOf(), shape(if (primary) accent else Color.rgb(27, 35, 49), 14, if (primary) accent else Color.rgb(51, 63, 83)))
        }
        setTextColor(if (primary) Color.rgb(25, 20, 17) else ink)
        stateListAnimator = null
        setOnFocusChangeListener { _, focused ->
            animate().scaleX(if (focused) 1.015f else 1f).scaleY(if (focused) 1.015f else 1f).setDuration(120).start()
        }
        setOnClickListener { action() }
    }

    fun inputBackground() = StateListDrawable().apply {
        addState(intArrayOf(android.R.attr.state_focused), shape(Color.rgb(15, 22, 34), 12, accent))
        addState(intArrayOf(), shape(Color.rgb(10, 16, 26), 12, Color.rgb(51, 63, 83)))
    }
}
