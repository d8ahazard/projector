package com.projector.app.controller

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.projector.app.Prefs
import com.projector.app.management.AppUpdater
import com.projector.app.player.Discovery
import com.projector.app.player.TvControls
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.UUID

/** A controller only: no player socket, boot launch, remote command receiver, or appliance grants. */
class ControllerActivity : AppCompatActivity() {
    private lateinit var prefs: Prefs
    private lateinit var root: FrameLayout
    private lateinit var ui: TvControls
    private lateinit var updater: AppUpdater
    private var web: WebView? = null
    private var discovery: Discovery? = null
    private var pendingCallback: String? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var scanForDevice = false
    private val http = OkHttpClient.Builder().followRedirects(false).build()

    private val scan = registerForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@registerForActivityResult
        val uri = Uri.parse(text)
        if (uri.scheme !in listOf("http", "https") || uri.host.isNullOrBlank() || uri.userInfo != null) {
            message("This is not a Projector server or player QR code.")
        } else {
            val base = "${uri.scheme}://${uri.host}${if (uri.port > 0) ":${uri.port}" else ""}"
            val path = uri.encodedPath.orEmpty().let { if (it.matches(Regex("/devices/[a-zA-Z0-9_-]+(/setup)?"))) it else "/" }
            if (ControllerPolicy.sameOrigin(prefs.serverUrl, base)) showWeb(path)
            else AlertDialog.Builder(this).setTitle("Connect to this server?")
                .setMessage(base).setNegativeButton("Cancel", null)
                .setPositiveButton("Connect") { _, _ -> connect(base, path) }.show()
        }
    }
    private val capture = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        val cb = pendingCallback ?: return@registerForActivityResult
        pendingCallback = null
        val json = if (res.resultCode == Activity.RESULT_OK) res.data?.getStringExtra(CalibrateActivity.EXTRA_RESULT)
            ?: JSONObject().put("__error", "no result").toString()
        else JSONObject().put("__error", "cancelled").toString()
        deliverCaptureResult(cb, json)
    }
    private val chooser = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(res.resultCode, res.data))
        fileCallback = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        prefs.role = Prefs.ROLE_CONTROLLER
        ui = TvControls(this)
        updater = AppUpdater(this)
        AppUpdater.recover(this)
        window.statusBarColor = Color.rgb(9, 13, 20)
        window.navigationBarColor = Color.rgb(16, 24, 36)
        root = FrameLayout(this).apply { setBackgroundColor(Color.rgb(9, 13, 20)) }
        setContentView(root)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (web?.canGoBack() == true) web?.goBack() else finish() }
        })
        if (prefs.serverUrl.isEmpty()) showPairing() else showWeb()
    }
    override fun onDestroy() {
        discovery?.stop(); web?.destroy(); fileCallback?.onReceiveValue(null)
        super.onDestroy()
    }
    private fun message(value: String) = Toast.makeText(this, value, Toast.LENGTH_LONG).show()
    private fun connect(input: String, path: String = "/") {
        val url = Prefs.normalizeUrl(input)
        if (!ControllerPolicy.sameOrigin(url, url)) { message("Enter a valid server address"); return }
        prefs.serverUrl = url
        showWeb(path)
    }
    private fun scanCode(device: Boolean) {
        scanForDevice = device
        scan.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            .setPrompt(if (device) "Scan the QR code on your Projector stick" else "Scan your Projector server QR code")
            .setBeepEnabled(false).setOrientationLocked(false))
    }
    private fun clearScreen() {
        discovery?.stop(); discovery = null
        web?.let { it.removeJavascriptInterface("ProjectorNative"); it.destroy() }; web = null
        root.removeAllViews()
    }
    private fun showPairing() {
        clearScreen()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(ui.dp(24), ui.dp(40), ui.dp(24), ui.dp(30))
        }
        fun add(view: android.view.View, top: Int = 12) {
            col.addView(view, LinearLayout.LayoutParams(-1, -2).apply { topMargin = ui.dp(top) })
        }
        add(ui.label("PROJECTOR  /  CONTROLLER", 11f, ui.accent, true), 0)
        add(ui.label("Your windows.\nYour imagination.", 34f, bold = true), 26)
        add(ui.label("Connect to your home server to build shows, manage players and calibrate with your camera.", 15f, ui.muted), 14)
        add(ui.button("Scan server QR", true) { scanCode(false) }, 30)
        add(ui.label("OR ENTER YOUR SERVER", 11f, ui.muted, true), 26)
        val input = EditText(this).apply {
            hint = "192.168.1.50:8000"; setText(prefs.serverUrl.removePrefix("http://"))
            setTextColor(ui.ink); setHintTextColor(ui.muted); textSize = 17f; isSingleLine = true
            inputType = android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI
            setPadding(ui.dp(16), ui.dp(14), ui.dp(16), ui.dp(14)); background = ui.inputBackground()
        }
        add(input)
        add(ui.button("Connect to server") { connect(input.text.toString()) })
        val found = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        add(found)
        add(ui.label("This phone is your controller. Camera use and app updates start here, with you.", 12f, ui.muted), 28)
        root.addView(ScrollView(this).apply { addView(col) }, ViewGroup.LayoutParams(-1, -1))
        val seen = mutableSetOf<String>()
        discovery = Discovery(this) { url ->
            if (seen.add(url)) found.addView(ui.button("Nearby server · ${Uri.parse(url).host}") { connect(url) })
        }.also { it.start() }
    }
    private fun phoneMenu() {
        AlertDialog.Builder(this).setTitle("Your controller")
            .setItems(arrayOf("Scan a player QR", "Add a new stick", "Calibrate projectors", "Check app update", "Change server")) { _, which ->
                when (which) {
                    0 -> scanCode(true)
                    1 -> showWeb("/setup")
                    2 -> showWeb("/calibrate")
                    3 -> checkUpdate()
                    4 -> showPairing()
                }
            }.setNegativeButton("Close", null).show()
    }
    private fun checkUpdate() {
        if (updater.busy) { message("An update is already in progress"); return }
        message("Checking your server for an update…")
        lifecycleScope.launch {
            try {
                val release = withContext(Dispatchers.IO) {
                    http.newCall(Request.Builder().url(prefs.serverUrl + "/api/app-release").build()).execute().use {
                        check(it.isSuccessful) { "Cannot reach the update server" }
                        JSONObject(it.body?.string() ?: "{}")
                    }
                }
                check(release.optBoolean("available")) { "No app release is available yet" }
                val installed = AppUpdater.version(packageManager.getPackageInfo(packageName, 0))
                if (release.getLong("version_code") <= installed) {
                    AlertDialog.Builder(this@ControllerActivity).setTitle("You're up to date")
                        .setMessage("Projector ${release.optString("version_name")} · build $installed\n\nUpdates install only when you choose them on this phone.")
                        .setPositiveButton("Done", null).show()
                } else AlertDialog.Builder(this@ControllerActivity).setTitle("Update your controller?")
                    .setMessage("Install Projector ${release.optString("version_name")} from your server. Your connection and settings stay in place. Android will ask you to confirm installation.")
                    .setNegativeButton("Later", null).setPositiveButton("Update") { _, _ ->
                        lifecycleScope.launch { updater.update(UUID.randomUUID().toString(), release) { _, text -> message(text) } }
                    }.show()
            } catch (e: Exception) { message(e.message ?: "Update check failed") }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun showWeb(path: String = "/") {
        clearScreen()
        val layout = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val bar = LinearLayout(this).apply {
            gravity = Gravity.CENTER_VERTICAL; setPadding(ui.dp(18), ui.dp(4), ui.dp(12), ui.dp(4))
        }
        bar.addView(ui.label("Projector", 20f, bold = true), LinearLayout.LayoutParams(0, ui.dp(48), 1f))
        bar.addView(ui.label("CONTROLLER", 9f, ui.accent, true).apply { setPadding(0, 0, ui.dp(14), 0) })
        bar.addView(ui.button("•••") { phoneMenu() }.apply { contentDescription = "Controller options" }, LinearLayout.LayoutParams(ui.dp(52), ui.dp(42)))
        layout.addView(bar)
        val w = WebView(this)
        web = w
        w.settings.javaScriptEnabled = true
        w.settings.domStorageEnabled = true
        w.settings.allowFileAccess = false
        w.settings.allowContentAccess = false
        w.settings.mediaPlaybackRequiresUserGesture = true
        w.setBackgroundColor(Color.rgb(9, 13, 20))
        w.addJavascriptInterface(Bridge(), "ProjectorNative")
        w.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (ControllerPolicy.sameOrigin(prefs.serverUrl, request.url.toString())) return false
                if (request.hasGesture() && request.url.scheme in listOf("http", "https")) {
                    try { startActivity(Intent(Intent.ACTION_VIEW, request.url)) } catch (_: Exception) { message("No browser available") }
                }
                return true
            }
            override fun onPageFinished(view: WebView, url: String) {
                if (ControllerPolicy.sameOrigin(prefs.serverUrl, url)) view.evaluateJavascript("document.body.classList.add('native-controller')", null)
            }
            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) showConnectionError()
            }
        }
        w.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCallback?.onReceiveValue(null); fileCallback = callback
                return try { chooser.launch(params.createIntent()); true } catch (_: Exception) { fileCallback = null; false }
            }
        }
        layout.addView(w, LinearLayout.LayoutParams(-1, 0, 1f))
        root.addView(layout, ViewGroup.LayoutParams(-1, -1))
        w.loadUrl(prefs.serverUrl + path)
    }
    private fun showConnectionError() {
        // Keep failure UI native: server text cannot inject a new HTML origin into the bridge.
        clearScreen()
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; gravity = Gravity.CENTER_VERTICAL
            setPadding(ui.dp(28), ui.dp(28), ui.dp(28), ui.dp(28))
        }
        col.addView(ui.label("Waiting for your server.", 28f, bold = true))
        col.addView(ui.label("Check your home Wi-Fi and server, then reconnect.", 15f, ui.muted))
        col.addView(ui.button("Try again", true) { showWeb() })
        col.addView(ui.button("Change server") { showPairing() })
        root.addView(col, ViewGroup.LayoutParams(-1, -1))
    }
    private fun deliverCaptureResult(name: String, json: String) {
        web?.evaluateJavascript("window[${JSONObject.quote(name)}] && window[${JSONObject.quote(name)}](${JSONObject.quote(json)})", null)
    }
    inner class Bridge {
        @JavascriptInterface fun capture(url: String, fieldsJson: String, lockExposure: Boolean, callback: String) {
            runOnUiThread {
                if (!callback.matches(Regex("__nativeCb_[a-zA-Z0-9]+"))) return@runOnUiThread
                val abs = ControllerPolicy.calibrationUrl(prefs.serverUrl, url)
                if (abs == null || !ControllerPolicy.sameOrigin(prefs.serverUrl, web?.url ?: "") || fieldsJson.length > 4096) {
                    deliverCaptureResult(callback, JSONObject().put("__error", "Invalid calibration request").toString()); return@runOnUiThread
                }
                if (pendingCallback != null) { deliverCaptureResult(callback, JSONObject().put("__error", "Camera request already open").toString()); return@runOnUiThread }
                pendingCallback = callback
                AlertDialog.Builder(this@ControllerActivity).setTitle("Open calibration camera?")
                    .setMessage("Frame the projected pattern, then tap Capture. Only that photo is sent to your saved server.")
                    .setNegativeButton("Cancel") { _, _ -> pendingCallback = null; deliverCaptureResult(callback, JSONObject().put("__error", "cancelled").toString()) }
                    .setOnCancelListener { pendingCallback = null; deliverCaptureResult(callback, JSONObject().put("__error", "cancelled").toString()) }
                    .setPositiveButton("Open camera") { _, _ ->
                        capture.launch(Intent(this@ControllerActivity, CalibrateActivity::class.java)
                            .putExtra(CalibrateActivity.EXTRA_URL, abs).putExtra(CalibrateActivity.EXTRA_FIELDS, fieldsJson)
                            .putExtra(CalibrateActivity.EXTRA_LOCK, lockExposure))
                    }.show()
            }
        }
    }
}
