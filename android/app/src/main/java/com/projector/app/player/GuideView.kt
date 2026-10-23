package com.projector.app.player

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.view.View

/** Native projection guides; drawn in physical output space on top of the video. */
class GuideView(context: Context) : View(context) {
    var pattern: String? = null
        set(v) { field = v; visibility = if (v == null || v == "image") GONE else VISIBLE; invalidate() }
    var corners: List<FloatArray> = listOf(floatArrayOf(0f, 0f), floatArrayOf(1f, 0f), floatArrayOf(1f, 1f), floatArrayOf(0f, 1f))
        set(v) { field = v; invalidate() }
    var label: String = ""
    var overlay: Boolean = false
        set(v) { field = v; invalidate() }

    private val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE; strokeWidth = 2f; style = Paint.Style.STROKE }
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.WHITE; textAlign = Paint.Align.CENTER }

    init {
        visibility = GONE
    }

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        if (!overlay) canvas.drawColor(Color.BLACK)
        when (pattern) {
            "grid" -> drawGrid(canvas, w, h)
            "outline" -> drawOutline(canvas, w, h, crosshairs = false)
            "crosshair" -> drawOutline(canvas, w, h, crosshairs = true)
            "colorbars" -> drawBars(canvas, w, h)
            "gray" -> drawGray(canvas, w, h)
            "white" -> canvas.drawColor(Color.WHITE)
            "black" -> Unit
            "identify" -> drawIdentify(canvas, w, h)
        }
    }

    private fun drawGrid(c: Canvas, w: Float, h: Float) {
        line.color = Color.WHITE
        line.strokeWidth = 1.5f
        val cols = 16
        val rows = 9
        for (i in 0..cols) c.drawLine(i * w / cols, 0f, i * w / cols, h, line)
        for (j in 0..rows) c.drawLine(0f, j * h / rows, w, j * h / rows, line)
        line.color = Color.rgb(255, 122, 26)
        line.strokeWidth = 3f
        c.drawLine(w / 2, 0f, w / 2, h, line)
        c.drawLine(0f, h / 2, w, h / 2, line)
        c.drawCircle(w / 2, h / 2, h / 4, line)
        c.drawRect(2f, 2f, w - 2f, h - 2f, line)
    }

    private fun drawOutline(c: Canvas, w: Float, h: Float, crosshairs: Boolean) {
        val pts = corners.map { floatArrayOf(it[0] * w, it[1] * h) }
        val path = Path().apply {
            moveTo(pts[0][0], pts[0][1])
            for (i in 1 until 4) lineTo(pts[i][0], pts[i][1])
            close()
        }
        fill.color = Color.argb(60, 255, 122, 26)
        c.drawPath(path, fill)
        line.color = Color.WHITE
        line.strokeWidth = 4f
        c.drawPath(path, line)
        line.strokeWidth = 1.5f
        c.drawLine(pts[0][0], pts[0][1], pts[2][0], pts[2][1], line)
        c.drawLine(pts[1][0], pts[1][1], pts[3][0], pts[3][1], line)
        text.textSize = h / 22
        val names = listOf("TL", "TR", "BR", "BL")
        pts.forEachIndexed { i, p ->
            if (crosshairs) {
                line.color = Color.rgb(122, 215, 255)
                line.strokeWidth = 3f
                val s = h / 12
                c.drawLine(p[0] - s, p[1], p[0] + s, p[1], line)
                c.drawLine(p[0], p[1] - s, p[0], p[1] + s, line)
                c.drawCircle(p[0], p[1], s / 3, line)
            }
            val cx = pts.map { it[0] }.average().toFloat()
            val cy = pts.map { it[1] }.average().toFloat()
            c.drawText(names[i], p[0] + (cx - p[0]) * 0.12f, p[1] + (cy - p[1]) * 0.12f + text.textSize / 3, text)
        }
    }

    private fun drawBars(c: Canvas, w: Float, h: Float) {
        val colors = intArrayOf(
            Color.rgb(192, 192, 192), Color.rgb(192, 192, 0), Color.rgb(0, 192, 192), Color.rgb(0, 192, 0),
            Color.rgb(192, 0, 192), Color.rgb(192, 0, 0), Color.rgb(0, 0, 192),
        )
        val bw = w / colors.size
        colors.forEachIndexed { i, col ->
            fill.color = col
            c.drawRect(i * bw, 0f, (i + 1) * bw, h * 0.75f, fill)
        }
        val bottom = intArrayOf(Color.WHITE, Color.BLACK, Color.rgb(255, 0, 0), Color.rgb(0, 255, 0), Color.rgb(0, 0, 255))
        val bw2 = w / bottom.size
        bottom.forEachIndexed { i, col ->
            fill.color = col
            c.drawRect(i * bw2, h * 0.75f, (i + 1) * bw2, h, fill)
        }
    }

    private fun drawGray(c: Canvas, w: Float, h: Float) {
        val steps = 11
        val sw = w / steps
        for (i in 0 until steps) {
            val v = (i * 255f / (steps - 1)).toInt()
            fill.color = Color.rgb(v, v, v)
            c.drawRect(i * sw, 0f, (i + 1) * sw, h, fill)
        }
    }

    private fun drawIdentify(c: Canvas, w: Float, h: Float) {
        c.drawColor(Color.rgb(255, 122, 26))
        text.color = Color.BLACK
        text.textSize = h / 6
        c.drawText(label, w / 2, h / 2 + text.textSize / 3, text)
        text.color = Color.WHITE
    }
}
