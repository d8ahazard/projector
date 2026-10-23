package com.projector.app.controller

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.hardware.camera2.params.ColorSpaceTransform
import android.hardware.camera2.params.RggbChannelVector
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.camera2.interop.Camera2CameraControl
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.CaptureRequestOptions
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.projector.app.Prefs
import com.projector.app.player.TvControls
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Takes one calibration photo and uploads it. In lock mode the first capture's exposure and
 * white balance are reused for every later capture to the same URL, so photos of different
 * projectors are directly comparable.
 */
@androidx.annotation.OptIn(markerClass = [ExperimentalCamera2Interop::class])
class CalibrateActivity : AppCompatActivity() {
    private lateinit var previewView: PreviewView
    private lateinit var status: TextView
    private lateinit var shutter: Button
    private var imageCapture: ImageCapture? = null
    private var camera: Camera? = null
    private var url = ""
    private var fields = JSONObject()
    private var lock = false

    @Volatile private var latest: Exposure? = null

    private val http = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).readTimeout(90, TimeUnit.SECONDS).writeTimeout(90, TimeUnit.SECONDS).build()

    private val permission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startCamera() else finishWith(JSONObject().put("__error", "camera permission denied"))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        url = intent.getStringExtra(EXTRA_URL) ?: ""
        if (ControllerPolicy.calibrationUrl(Prefs(this).serverUrl, url) == null) {
            finishWith(JSONObject().put("__error", "Invalid calibration destination")); return
        }
        fields = try { JSONObject(intent.getStringExtra(EXTRA_FIELDS) ?: "{}") } catch (_: Exception) {
            finishWith(JSONObject().put("__error", "Invalid calibration fields")); return
        }
        lock = intent.getBooleanExtra(EXTRA_LOCK, false)

        val root = FrameLayout(this)
        val ui = TvControls(this)
        previewView = PreviewView(this).apply { scaleType = PreviewView.ScaleType.FIT_CENTER }
        root.addView(previewView, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        val pad = (16 * resources.displayMetrics.density).toInt()
        status = TextView(this).apply {
            setTextColor(Color.WHITE)
            background = ui.shape(Color.argb(225, 16, 23, 35), 18)
            textSize = 15f
            setPadding(pad, pad, pad, pad)
            text = if (lock) "Frame the whole window. Exposure is locked after the first photo; keep the same spot for each projector."
            else "Frame the whole projected pattern, roughly straight on."
        }
        root.addView(status, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP).apply { setMargins(pad, pad, pad, 0) })
        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            setPadding(pad, pad, pad, pad * 2)
            background = ui.shape(Color.argb(235, 9, 13, 20), 22)
        }
        shutter = ui.button("Capture photo", true) { takePhoto() }.apply { isEnabled = false }
        bar.addView(ui.button("Cancel") { setResult(RESULT_CANCELED); finish() }, LinearLayout.LayoutParams(0, -2, 1f).apply { rightMargin = pad })
        bar.addView(shutter, LinearLayout.LayoutParams(0, -2, 2f))
        if (lock && locked.containsKey(url)) {
            bar.addView(Button(this).apply {
                text = "Reset exposure"
                setOnClickListener {
                    locked.remove(url)
                    camera?.let { applyLock(it) }
                    status.text = "Exposure unlocked; the next photo sets a new lock."
                }
            })
        }
        root.addView(bar, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
        setContentView(root)

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) startCamera()
        else permission.launch(Manifest.permission.CAMERA)
    }

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val provider = future.get()
            val previewBuilder = Preview.Builder()
            Camera2Interop.Extender(previewBuilder).setSessionCaptureCallback(object : CameraCaptureSession.CaptureCallback() {
                override fun onCaptureCompleted(session: CameraCaptureSession, request: CaptureRequest, result: TotalCaptureResult) {
                    latest = Exposure(
                        result.get(CaptureResult.SENSOR_EXPOSURE_TIME),
                        result.get(CaptureResult.SENSOR_SENSITIVITY),
                        result.get(CaptureResult.COLOR_CORRECTION_GAINS),
                        result.get(CaptureResult.COLOR_CORRECTION_TRANSFORM),
                    )
                }
            })
            val preview = previewBuilder.build().also { it.setSurfaceProvider(previewView.surfaceProvider) }
            val capture = ImageCapture.Builder().setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY).build()
            imageCapture = capture
            try {
                provider.unbindAll()
                val cam = provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, capture)
                camera = cam
                applyLock(cam)
                shutter.isEnabled = true
            } catch (e: Exception) {
                finishWith(JSONObject().put("__error", "camera unavailable: ${e.message}"))
            }
        }, ContextCompat.getMainExecutor(this))
    }

    /** Re-applies a previously captured manual exposure/white balance, if any. */
    private fun applyLock(cam: Camera) {
        val ctl = Camera2CameraControl.from(cam.cameraControl)
        val saved = if (lock) locked[url] else null
        if (saved?.exposureNs == null || saved.iso == null) {
            ctl.setCaptureRequestOptions(CaptureRequestOptions.Builder().build())
            return
        }
        val b = CaptureRequestOptions.Builder()
            .setCaptureRequestOption(CaptureRequest.CONTROL_AE_MODE, CaptureRequest.CONTROL_AE_MODE_OFF)
            .setCaptureRequestOption(CaptureRequest.SENSOR_EXPOSURE_TIME, saved.exposureNs)
            .setCaptureRequestOption(CaptureRequest.SENSOR_SENSITIVITY, saved.iso)
        if (saved.gains != null && saved.transform != null) {
            b.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE, CaptureRequest.CONTROL_AWB_MODE_OFF)
                .setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_MODE, CaptureRequest.COLOR_CORRECTION_MODE_TRANSFORM_MATRIX)
                .setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_GAINS, saved.gains)
                .setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_TRANSFORM, saved.transform)
        } else {
            b.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_LOCK, true)
        }
        ctl.setCaptureRequestOptions(b.build())
        status.text = "Exposure locked from the first photo."
    }

    @SuppressLint("SetTextI18n")
    private fun takePhoto() {
        val capture = imageCapture ?: return
        shutter.isEnabled = false
        status.text = "Capturing…"
        if (lock && !locked.containsKey(url)) latest?.let { locked[url] = it }
        val file = File(cacheDir, "calibration.jpg")
        capture.takePicture(
            ImageCapture.OutputFileOptions.Builder(file).build(),
            ContextCompat.getMainExecutor(this),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    status.text = "Uploading and analyzing…"
                    upload(file)
                }

                override fun onError(e: ImageCaptureException) {
                    status.text = "Capture failed: ${e.message}"
                    shutter.isEnabled = true
                }
            },
        )
    }

    private fun upload(file: File) {
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                try {
                    val body = MultipartBody.Builder().setType(MultipartBody.FORM)
                    fields.keys().forEach { k -> body.addFormDataPart(k, fields.getString(k)) }
                    body.addFormDataPart("photo", "photo.jpg", file.asRequestBody("image/jpeg".toMediaType()))
                    http.newCall(Request.Builder().url(url).post(body.build()).build()).execute().use { resp ->
                        val text = resp.body?.string() ?: ""
                        if (resp.isSuccessful) JSONObject(text)
                        else JSONObject().put("__error", try { JSONObject(text).optString("detail", text) } catch (_: Exception) { "HTTP ${resp.code}" })
                    }
                } catch (e: Exception) {
                    JSONObject().put("__error", e.message ?: "upload failed")
                }
            }
            finishWith(result)
        }
    }

    private fun finishWith(json: JSONObject) {
        setResult(RESULT_OK, Intent().putExtra(EXTRA_RESULT, json.toString()))
        finish()
    }

    private data class Exposure(val exposureNs: Long?, val iso: Int?, val gains: RggbChannelVector?, val transform: ColorSpaceTransform?)

    companion object {
        const val EXTRA_URL = "url"
        const val EXTRA_FIELDS = "fields"
        const val EXTRA_LOCK = "lock"
        const val EXTRA_RESULT = "result"

        /** Upload URL (one per color session) -> exposure locked at its first capture. */
        private val locked = mutableMapOf<String, Exposure>()
    }
}
