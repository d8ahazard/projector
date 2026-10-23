package com.projector.app.render

import android.graphics.Bitmap
import android.graphics.SurfaceTexture
import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import android.opengl.GLUtils
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

/** Immutable snapshot of everything the shader needs, swapped atomically each frame. */
data class RenderState(
    val visible: Boolean = false,
    val hInv: FloatArray = floatArrayOf(1f, 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f),
    val mirrorH: Boolean = false,
    val mirrorV: Boolean = false,
    val gain: FloatArray = floatArrayOf(1f, 1f, 1f),
    val gamma: Float = 1f,
    val brightness: Float = 1f,
    val alpha: FloatArray = floatArrayOf(0f, 0f),
    val rect0: FloatArray = floatArrayOf(0f, 0f, 1f, 1f),
    val rect1: FloatArray = floatArrayOf(0f, 0f, 1f, 1f),
    val deckFlipH: BooleanArray = booleanArrayOf(false, false),
    val wipeOn: Boolean = false,
    val wipeTarget: Int = 1,
    val wipeProgress: Float = 0f,
    val wipeDir: FloatArray = floatArrayOf(1f, 0f),
)

class VideoRenderer(private val onSurfacesReady: (List<SurfaceTexture>) -> Unit) : GLSurfaceView.Renderer {
    val state = AtomicReference(RenderState())
    private val pendingMask = AtomicReference<Bitmap?>(null)

    private var program = 0
    private val oesTex = IntArray(2)
    private val maskTex = IntArray(1)
    private val surfaces = mutableListOf<SurfaceTexture>()
    private val frameAvailable = arrayOf(AtomicBoolean(false), AtomicBoolean(false))
    private val texMatrix = arrayOf(FloatArray(16), FloatArray(16))
    private lateinit var quad: FloatBuffer
    private val loc = HashMap<String, Int>()

    fun setMask(bitmap: Bitmap) {
        pendingMask.set(bitmap)
    }

    override fun onSurfaceCreated(gl: GL10?, config: EGLConfig?) {
        program = buildProgram(Shaders.VERTEX, Shaders.FRAGMENT)
        for (name in listOf("aPos", "uTex0", "uTex1", "uTexM0", "uTexM1", "uMask", "uHinv", "uMirror", "uRect0", "uRect1",
            "uAlpha", "uDeckFlipH", "uGain", "uGamma", "uBright", "uWipe", "uWipeDir")) {
            loc[name] = if (name.startsWith("a")) GLES20.glGetAttribLocation(program, name) else GLES20.glGetUniformLocation(program, name)
        }
        quad = ByteBuffer.allocateDirect(8 * 4).order(ByteOrder.nativeOrder()).asFloatBuffer().apply {
            put(floatArrayOf(-1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f)); position(0)
        }

        GLES20.glGenTextures(2, oesTex, 0)
        surfaces.forEach { it.release() }
        surfaces.clear()
        for (i in 0 until 2) {
            GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, oesTex[i])
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
            GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
            val st = SurfaceTexture(oesTex[i])
            val flag = frameAvailable[i]
            st.setOnFrameAvailableListener { flag.set(true) }
            surfaces.add(st)
            android.opengl.Matrix.setIdentityM(texMatrix[i], 0)
        }

        GLES20.glGenTextures(1, maskTex, 0)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, maskTex[0])
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)
        val white = Bitmap.createBitmap(1, 1, Bitmap.Config.ARGB_8888).apply { eraseColor(-1) }
        GLUtils.texImage2D(GLES20.GL_TEXTURE_2D, 0, white, 0)
        white.recycle()

        val list = surfaces.toList()
        Handler(Looper.getMainLooper()).post { onSurfacesReady(list) }
    }

    override fun onSurfaceChanged(gl: GL10?, width: Int, height: Int) {
        GLES20.glViewport(0, 0, width, height)
    }

    override fun onDrawFrame(gl: GL10?) {
        for (i in 0 until 2) {
            if (frameAvailable[i].getAndSet(false)) {
                try {
                    surfaces[i].updateTexImage()
                    surfaces[i].getTransformMatrix(texMatrix[i])
                } catch (e: Exception) {
                    Log.w("VideoRenderer", "updateTexImage failed: ${e.message}")
                }
            }
        }
        pendingMask.getAndSet(null)?.let { bmp ->
            GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, maskTex[0])
            GLUtils.texImage2D(GLES20.GL_TEXTURE_2D, 0, bmp, 0)
            bmp.recycle()
        }

        GLES20.glClearColor(0f, 0f, 0f, 1f)
        GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT)
        val s = state.get()
        if (!s.visible) return

        GLES20.glUseProgram(program)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, oesTex[0])
        GLES20.glUniform1i(loc["uTex0"]!!, 0)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE1)
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, oesTex[1])
        GLES20.glUniform1i(loc["uTex1"]!!, 1)
        GLES20.glActiveTexture(GLES20.GL_TEXTURE2)
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, maskTex[0])
        GLES20.glUniform1i(loc["uMask"]!!, 2)

        GLES20.glUniformMatrix4fv(loc["uTexM0"]!!, 1, false, texMatrix[0], 0)
        GLES20.glUniformMatrix4fv(loc["uTexM1"]!!, 1, false, texMatrix[1], 0)
        GLES20.glUniformMatrix3fv(loc["uHinv"]!!, 1, false, s.hInv, 0)
        GLES20.glUniform2f(loc["uMirror"]!!, if (s.mirrorH) 1f else 0f, if (s.mirrorV) 1f else 0f)
        GLES20.glUniform4fv(loc["uRect0"]!!, 1, s.rect0, 0)
        GLES20.glUniform4fv(loc["uRect1"]!!, 1, s.rect1, 0)
        GLES20.glUniform2f(loc["uAlpha"]!!, s.alpha[0], s.alpha[1])
        GLES20.glUniform2f(loc["uDeckFlipH"]!!, if (s.deckFlipH[0]) 1f else 0f, if (s.deckFlipH[1]) 1f else 0f)
        GLES20.glUniform3fv(loc["uGain"]!!, 1, s.gain, 0)
        GLES20.glUniform1f(loc["uGamma"]!!, s.gamma)
        GLES20.glUniform1f(loc["uBright"]!!, s.brightness)
        GLES20.glUniform4f(loc["uWipe"]!!, if (s.wipeOn) 1f else 0f, s.wipeTarget.toFloat(), s.wipeProgress, 0.08f)
        GLES20.glUniform2fv(loc["uWipeDir"]!!, 1, s.wipeDir, 0)

        val aPos = loc["aPos"]!!
        GLES20.glEnableVertexAttribArray(aPos)
        GLES20.glVertexAttribPointer(aPos, 2, GLES20.GL_FLOAT, false, 0, quad)
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
        GLES20.glDisableVertexAttribArray(aPos)
    }

    private fun compile(type: Int, src: String): Int {
        val sh = GLES20.glCreateShader(type)
        GLES20.glShaderSource(sh, src)
        GLES20.glCompileShader(sh)
        val ok = IntArray(1)
        GLES20.glGetShaderiv(sh, GLES20.GL_COMPILE_STATUS, ok, 0)
        if (ok[0] == 0) throw RuntimeException("shader compile failed: " + GLES20.glGetShaderInfoLog(sh))
        return sh
    }

    private fun buildProgram(vs: String, fs: String): Int {
        val p = GLES20.glCreateProgram()
        GLES20.glAttachShader(p, compile(GLES20.GL_VERTEX_SHADER, vs))
        GLES20.glAttachShader(p, compile(GLES20.GL_FRAGMENT_SHADER, fs))
        GLES20.glLinkProgram(p)
        val ok = IntArray(1)
        GLES20.glGetProgramiv(p, GLES20.GL_LINK_STATUS, ok, 0)
        if (ok[0] == 0) throw RuntimeException("program link failed: " + GLES20.glGetProgramInfoLog(p))
        return p
    }
}
