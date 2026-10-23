package com.projector.app.player

import android.content.Intent
import android.graphics.BitmapFactory
import android.graphics.Color
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.opengl.GLSurfaceView
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.ScrollView
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.OnBackPressedCallback
import com.projector.app.MainActivity
import com.projector.app.Prefs
import com.projector.app.media.MediaCache
import com.projector.app.management.AppUpdater
import android.net.Uri
import android.provider.Settings
import com.projector.app.net.ServerConnection
import com.projector.app.playback.Engine
import com.projector.app.playback.PlayerConfig
import com.projector.app.render.Homography
import com.projector.app.render.VideoRenderer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONArray
import org.json.JSONObject

/**
 * Headless player: black screen, the server drives everything. The only UI is a
 * pairing panel shown on first run (or via the remote's Menu/Back key).
 */
class PlayerActivity : AppCompatActivity() {
    private lateinit var prefs: Prefs
    private lateinit var glView: GLSurfaceView
    private lateinit var renderer: VideoRenderer
    private lateinit var guideView: GuideView
    private lateinit var imageGuide: ImageView
    private lateinit var root: FrameLayout
    private var pairing: View? = null

    private var conn: ServerConnection? = null
    private var engine: Engine? = null
    private lateinit var cache: MediaCache
    private var discovery: Discovery? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val http = OkHttpClient()
    private val main = Handler(Looper.getMainLooper())
    private var surfaces: List<android.graphics.SurfaceTexture>? = null
    private var guideTimeout: Runnable? = null
    private var deviceName = ""
    private var lastMaskUrl = ""
    private var guideRevision = 0
    private val updater by lazy { AppUpdater(this) }

    private val maxDecoders: Int by lazy { detectMaxDecoders() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        AppUpdater.recover(this)
        val prior = AppUpdater.status(this)
        if (prior.optString("state") == "restarting") AppUpdater.record(this, prior.optString("id"), "restarted", "Player restarted")
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        enterImmersive()

        root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        renderer = VideoRenderer { st -> surfaces = st; engine?.attachSurfaces(st) }
        glView = GLSurfaceView(this).apply {
            setEGLContextClientVersion(2)
            preserveEGLContextOnPause = true
            setRenderer(renderer)
            renderMode = GLSurfaceView.RENDERMODE_WHEN_DIRTY
        }
        guideView = GuideView(this)
        imageGuide = ImageView(this).apply {
            scaleType = ImageView.ScaleType.FIT_XY
            setBackgroundColor(Color.BLACK)
            visibility = View.GONE
        }
        val mp = ViewGroup.LayoutParams.MATCH_PARENT
        root.addView(glView, mp, mp)
        root.addView(guideView, mp, mp)
        root.addView(imageGuide, mp, mp)
        setContentView(root)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (Appliance.enabled(this@PlayerActivity)) hidePairing() else showPairing()
            }
        })

        cache = MediaCache(this) { prefs.cacheBudgetMb }
        cache.onStatus = { st -> main.post { sendMediaStatus(st) } }

        if (prefs.serverUrl.isEmpty()) showPairing() else startPlayer()
    }

    private fun enterImmersive() {
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN)
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) enterImmersive()
    }

    override fun onResume() {
        super.onResume()
        glView.onResume()
        glView.requestRender()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        hidePairing()
    }

    override fun onPause() {
        glView.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        stopPlayer()
        discovery?.stop()
        scope.cancel()
        main.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    // ------------------------------------------------------------------ player

    private fun startPlayer() {
        hidePairing()
        stopPlayer()
        val url = prefs.serverUrl
        val c = ServerConnection(url, ::hello, ::heartbeat, object : ServerConnection.Listener {
            override fun onConnected() {
                // BOOT_COMPLETED may arrive before Wi-Fi. Retry once the LAN is reachable.
                try {
                    if (MaintenanceAccess.allowed(this@PlayerActivity) && !MaintenanceAccess.isEnabled(this@PlayerActivity)) {
                        MaintenanceAccess.enable(this@PlayerActivity)
                    }
                } catch (e: Exception) {
                    android.util.Log.w("ProjectorMaintenance", "Wireless ADB recovery could not be requested", e)
                }
            }
            override fun onDisconnected() {}
            override fun onMessage(type: String, msg: JSONObject) = handle(type, msg)
        })
        conn = c
        val e = Engine(this, renderer, cache, { c.serverNow() }, { c.clock.isSynced }, maxDecoders >= 2) { glView.requestRender() }
        engine = e
        surfaces?.let { e.attachSurfaces(it) }
        c.start()
    }

    private fun stopPlayer() {
        conn?.stop()
        conn = null
        engine?.stop()
        engine = null
    }

    private fun hello(): JSONObject {
        val dm = resources.displayMetrics
        val version = try { packageManager.getPackageInfo(packageName, 0).versionName } catch (e: Exception) { "" }
        return JSONObject()
            .put("device_id", prefs.deviceId)
            .put("name", prefs.deviceName)
            .put("model", "${Build.MANUFACTURER} ${Build.MODEL} (Android ${Build.VERSION.RELEASE})")
            .put("app_version", version)
            .put(
                "capabilities", JSONObject()
                    .put("dual_decoder", maxDecoders >= 2)
                    .put("max_decoders", maxDecoders)
                    .put("width", dm.widthPixels)
                    .put("height", dm.heightPixels)
                    .put("storage_free_mb", cache.freeMb())
                    .put("cache_budget_mb", prefs.cacheBudgetMb)
                    .put("h264_720p60", supports720p60())
                    .put("display_refresh_hz", windowManager.defaultDisplay.refreshRate)
                    .put("playback_verified", false)
                    .put("management_protocol", 1)
                    .put("maintenance_adb", MaintenanceAccess.allowed(this))
                    .put("tv_menu", true)
                    .put("version_code", AppUpdater.version(packageManager.getPackageInfo(packageName, 0)))
            )
    }

    private fun heartbeat(): JSONObject {
        val e = engine
        return JSONObject()
            .put("drift_ms", e?.lastDriftMs ?: 0)
            .put("event_id", e?.currentEventId ?: JSONObject.NULL)
            .put("asset_id", e?.currentAssetId ?: JSONObject.NULL)
            .put("storage_free_mb", cache.freeMb())
            .put("management", AppUpdater.status(this)
                .put("appliance_mode", Appliance.enabled(this))
                .put("default_home", Appliance.isHome(this))
                .put("playback_state", if (e?.currentEventId != null) "playing" else "standby")
                .put("loaded_decoders", e?.loadedDecoders ?: 0)
                .put("clip_loads", e?.loadCount ?: 0)
                .put("buffer_events", e?.bufferEvents ?: 0)
                .put("buffer_ms", e?.bufferMs ?: 0)
                .put("dropped_frames", e?.droppedFrames ?: 0)
                .put("playback_errors", e?.playbackErrors ?: 0)
                .put("continuous_rendering", false))
            .also { it.getJSONObject("management")
                .put("maintenance_adb", MaintenanceAccess.allowed(this))
                .put("wireless_adb_enabled", MaintenanceAccess.isEnabled(this)) }
    }

    private fun sendMediaStatus(st: MediaCache.Status) {
        val obj = JSONObject()
            .put("type", "media_status")
            .put("ready", st.ready)
            .put("missing", st.missing)
            .put("progress", st.progress)
            .put("storage_free_mb", st.storageFreeMb)
        st.error?.let { obj.put("error", it) }
        conn?.send(obj)
    }

    private fun handle(type: String, msg: JSONObject) {
        when (type) {
            "command" -> handleCommand(msg)
            "welcome", "config" -> applyConfig(msg.getJSONObject("config"))
            "media_sync" -> {
                val arr = msg.optJSONArray("assets") ?: JSONArray()
                val list = (0 until arr.length()).map {
                    val a = arr.getJSONObject(it)
                    MediaCache.Asset(a.getString("id"), a.getString("sha256"), a.getString("url"), a.optLong("size"))
                }
                cache.sync(prefs.serverUrl, list)
            }
            "schedule" -> {
                val arr = msg.optJSONArray("events") ?: JSONArray()
                val events = (0 until arr.length()).map { ServerConnection.parseEvent(arr.getJSONObject(it)) }
                engine?.queue?.add(events)
            }
            "clear" -> {
                val after = msg.optLong("after", 0)
                if (after <= 0) {
                    engine?.clearAll()
                    showGuide(null, JSONObject())
                } else {
                    engine?.onClear(after)
                    val revision = guideRevision
                    main.postDelayed({ if (revision == guideRevision) showGuide(null, JSONObject()) },
                        maxOf(0L, after - (conn?.serverNow() ?: after)))
                }
            }
            "guide" -> showGuide(if (msg.isNull("pattern")) null else msg.optString("pattern"), msg.optJSONObject("params") ?: JSONObject())
        }
    }

    private fun handleCommand(msg: JSONObject) {
        val id = msg.optString("id")
        if (id.isEmpty() || id == prefs.lastCommandId) return
        if (updater.busy) {
            conn?.send(JSONObject().put("type", "command_result").put("id", id).put("state", "busy").put("message", "Update download in progress"))
            return
        }
        prefs.lastCommandId = id
        fun result(state: String, message: String) {
            AppUpdater.record(this, id, state, message)
            conn?.send(JSONObject().put("type", "command_result").put("id", id).put("state", state).put("message", message))
        }
        try {
            when (msg.optString("action")) {
                "restart" -> {
                    result("restarting", "Restarting player and reconnecting")
                    main.postDelayed({ recreate() }, 300)
                }
                "update" -> scope.launch { updater.update(id, msg.getJSONObject("release"), ::result) }
                "player_settings" -> {
                    showPairing()
                    result("settings_open", "Player settings opened on the display")
                }
                "maintenance_adb" -> {
                    check(MaintenanceAccess.enable(this)) { "Maintenance permission has not been provisioned" }
                    result("maintenance_requested", "Wireless ADB requested; Android may require trusted-network approval")
                }
                "boot_settings", "update_settings" -> {
                    val action = if (msg.optString("action") == "boot_settings") Settings.ACTION_MANAGE_OVERLAY_PERMISSION else Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES
                    startActivity(Intent(action, Uri.parse("package:$packageName")))
                    result("permission_required", "Approve the permission on the stick, then return to Projector")
                }
                else -> result("failed", "Unknown command")
            }
        } catch (e: Exception) { result("failed", e.message ?: "Command failed") }
    }

    private fun applyConfig(cfg: JSONObject) {
        deviceName = cfg.optString("name", deviceName)
        if (cfg.has("cache_budget_mb")) prefs.cacheBudgetMb = cfg.optLong("cache_budget_mb", prefs.cacheBudgetMb)
        val cornersArr = cfg.optJSONArray("corners")
        val corners = if (cornersArr != null && cornersArr.length() == 4) {
            Array(4) { i -> cornersArr.getJSONArray(i).let { doubleArrayOf(it.getDouble(0), it.getDouble(1)) } }
        } else {
            arrayOf(doubleArrayOf(0.0, 0.0), doubleArrayOf(1.0, 0.0), doubleArrayOf(1.0, 1.0), doubleArrayOf(0.0, 1.0))
        }
        val hInv = try {
            Homography.toGlColumnMajor(Homography.invert(Homography.fromUnitSquare(corners)))
        } catch (e: IllegalArgumentException) {
            floatArrayOf(1f, 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f)
        }
        guideView.corners = corners.map { floatArrayOf(it[0].toFloat(), it[1].toFloat()) }
        val gainArr = cfg.optJSONArray("gain")
        val gain = if (gainArr != null && gainArr.length() == 3) FloatArray(3) { gainArr.getDouble(it).toFloat() } else floatArrayOf(1f, 1f, 1f)
        engine?.config = PlayerConfig(
            hInv = hInv,
            mirrorH = cfg.optBoolean("mirror_h"),
            mirrorV = cfg.optBoolean("mirror_v"),
            gain = gain,
            gamma = cfg.optDouble("gamma", 1.0).toFloat(),
            brightness = cfg.optDouble("brightness", 1.0).toFloat(),
            volume = cfg.optDouble("volume", 1.0).toFloat(),
            audio = cfg.optBoolean("audio", true),
        )
        val maskUrl = cfg.optString("mask_url")
        if (maskUrl.isNotEmpty() && maskUrl != lastMaskUrl) {
            lastMaskUrl = maskUrl
            loadBitmap(prefs.serverUrl + maskUrl) { bmp -> renderer.setMask(bmp) }
        }
    }

    private fun loadBitmap(url: String, onLoaded: (android.graphics.Bitmap) -> Unit) {
        scope.launch {
            val bmp = withContext(Dispatchers.IO) {
                try {
                    http.newCall(Request.Builder().url(url).build()).execute().use { r ->
                        if (!r.isSuccessful) null else r.body?.byteStream()?.use { BitmapFactory.decodeStream(it) }
                    }
                } catch (e: Exception) {
                    null
                }
            }
            bmp?.let(onLoaded)
        }
    }

    private fun showGuide(pattern: String?, params: JSONObject) {
        val revision = ++guideRevision
        guideTimeout?.let { main.removeCallbacks(it) }
        guideTimeout = null
        if (pattern == "image") {
            val url = params.optString("url")
            loadBitmap(if (url.startsWith("http")) url else prefs.serverUrl + url) { bmp ->
                if (revision == guideRevision) {
                    imageGuide.setImageBitmap(bmp)
                    imageGuide.visibility = View.VISIBLE
                } else bmp.recycle()
            }
        } else {
            imageGuide.visibility = View.GONE
            imageGuide.setImageDrawable(null)
        }
        guideView.label = deviceName
        val defaultOverlay = pattern == "outline" || pattern == "crosshair" || pattern == "grid" || pattern == "identify"
        val overlay = pattern != null && params.optBoolean("overlay", defaultOverlay)
        guideView.overlay = overlay
        guideView.pattern = pattern
        engine?.hidden = pattern != null && !overlay
        val seconds = params.optDouble("seconds", 0.0)
        if (pattern != null && seconds > 0) {
            val r = Runnable { showGuide(null, JSONObject()) }
            guideTimeout = r
            main.postDelayed(r, (maxOf(3.0, seconds) * 1000).toLong())
        }
    }

    private fun detectMaxDecoders(): Int {
        var best = 1
        try {
            for (info in MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos) {
                if (info.isEncoder || !info.supportedTypes.any { it.equals("video/avc", true) }) continue
                val hw = if (Build.VERSION.SDK_INT >= 29) info.isHardwareAccelerated
                else !info.name.startsWith("OMX.google.") && !info.name.startsWith("c2.android.")
                if (!hw) continue
                val caps: MediaCodecInfo.CodecCapabilities = info.getCapabilitiesForType("video/avc")
                best = maxOf(best, caps.maxSupportedInstances)
            }
        } catch (_: Exception) {
        }
        return best
    }

    private fun supports720p60(): Boolean = try {
        MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos.any { info ->
            !info.isEncoder && info.supportedTypes.any { it.equals("video/avc", true) } &&
                (if (Build.VERSION.SDK_INT >= 29) info.isHardwareAccelerated else !info.name.startsWith("OMX.google.") && !info.name.startsWith("c2.android.")) &&
                info.getCapabilitiesForType("video/avc").videoCapabilities.areSizeAndRateSupported(1280, 720, 60.0)
        }
    } catch (_: Exception) { false }

    // ------------------------------------------------------------------ pairing

    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (Appliance.enabled(this) && pairing == null && event.keyCode in listOf(
                KeyEvent.KEYCODE_BACK, KeyEvent.KEYCODE_MENU, KeyEvent.KEYCODE_SETTINGS)) {
            if (event.action == KeyEvent.ACTION_UP && event.eventTime - event.downTime >= 1500) showPairing()
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (pairing == null && (keyCode == KeyEvent.KEYCODE_MENU || keyCode == KeyEvent.KEYCODE_SETTINGS ||
                keyCode == KeyEvent.KEYCODE_BACK)) {
            showPairing()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    private fun showPairing() {
        if (pairing != null) return
        val ui = TvControls(this)
        val linked = prefs.serverUrl.isNotEmpty()
        val appliance = Appliance.enabled(this)
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            background = ui.shape(Color.rgb(16, 23, 35), 24, Color.rgb(43, 55, 74))
            setPadding(dp(28), dp(24), dp(28), dp(22))
        }
        fun add(view: View, top: Int = 0) {
            panel.addView(view, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(top) })
        }
        val header = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL }
        header.addView(ui.label("PROJECTOR  /  ${deviceName.ifBlank { "PLAYER" }.uppercase()}", 12f, ui.accent, true), LinearLayout.LayoutParams(0, -2, 1f))
        val online = conn?.connected == true
        header.addView(ui.label(if (online) "●  CONNECTED" else if (linked) "●  RECONNECTING" else "●  SETUP", 11f,
            if (online) Color.rgb(116, 228, 182) else ui.accent, true).apply {
            background = ui.shape(Color.rgb(25, 38, 46), 20)
            setPadding(dp(12), dp(8), dp(12), dp(8))
        })
        add(header)
        add(ui.label(if (linked) "Ready for the next scene." else "Give your window a little life.", 29f, bold = true), 18)
        add(ui.label(if (linked) "Shows, timing and updates are controlled from your server."
            else "Connect to your local server to bring your scenes to this screen.", 14f, ui.muted), 9)
        add(ui.label("SERVER ADDRESS", 11f, ui.muted, true), 24)
        val found = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val input = EditText(this).apply {
            hint = "192.168.1.50:8000"
            setText(prefs.serverUrl.removePrefix("http://"))
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setTextColor(ui.ink); setHintTextColor(ui.muted); textSize = 17f
            isSingleLine = true
            setPadding(dp(16), dp(12), dp(16), dp(12))
            backgroundTintList = null; background = ui.inputBackground()
        }
        add(input, 8)
        val connect = ui.button(if (linked) "Save connection" else "Connect to server", !linked) {
            val url = Prefs.normalizeUrl(input.text.toString())
            try {
                val parsed = Uri.parse(url)
                require(parsed.scheme in listOf("http", "https") && !parsed.host.isNullOrBlank())
                require(okhttp3.Request.Builder().url(url).build().url.host.isNotBlank())
                    prefs.serverUrl = url
                    startPlayer()
            } catch (_: IllegalArgumentException) {
                input.error = "Enter a valid server address"; input.requestFocus()
            }
        }
        val actions = LinearLayout(this).apply { clipChildren = false; clipToPadding = false }
        val resume = if (linked) ui.button("Resume projection", true) { hidePairing() } else null
        resume?.let { actions.addView(it, LinearLayout.LayoutParams(0, dp(48), 1f).apply { rightMargin = dp(12) }) }
        actions.addView(connect, LinearLayout.LayoutParams(0, dp(48), 1f))
        add(actions, 16)
        if (appliance) {
            add(ui.button("Device settings") { startActivity(Intent(Settings.ACTION_SETTINGS)) }, 12)
        } else {
            val secondary = LinearLayout(this)
            secondary.addView(ui.button("Use as controller") {
                prefs.role = Prefs.ROLE_CONTROLLER
                startActivity(Intent(this, MainActivity::class.java)); finish()
            }, LinearLayout.LayoutParams(0, dp(46), 1f).apply { rightMargin = dp(12) })
            secondary.addView(ui.button("Exit") { finishAffinity() }, LinearLayout.LayoutParams(0, dp(46), 1f))
            add(secondary, 12)
        }
        val status = ui.label(if (linked) "Hold Back or Menu to return here. Standby stays black and silent."
            else "Looking for a server on your network…", 12f, ui.muted)
        add(status, 18)
        add(found, 8)
        add(ui.label("DEVICE ${prefs.deviceId.uppercase()}  ·  LOCAL CONNECTION", 10f, ui.muted), 12)
        if (linked) {
            val qrRow = LinearLayout(this).apply { gravity = Gravity.CENTER_VERTICAL; isFocusable = true; contentDescription = "Player setup QR code" }
            val link = prefs.serverUrl + "/devices/" + prefs.deviceId + "/setup"
            val matrix = com.google.zxing.qrcode.QRCodeWriter().encode(link, com.google.zxing.BarcodeFormat.QR_CODE, 240, 240)
            val bitmap = android.graphics.Bitmap.createBitmap(240, 240, android.graphics.Bitmap.Config.ARGB_8888)
            for (y in 0 until 240) for (x in 0 until 240) bitmap.setPixel(x, y, if (matrix[x, y]) Color.BLACK else Color.WHITE)
            qrRow.addView(android.widget.ImageView(this).apply { setImageBitmap(bitmap); contentDescription = "Player setup QR code" }, LinearLayout.LayoutParams(dp(112), dp(112)))
            qrRow.addView(ui.label("Scan with your controller\nName this player, choose a surface\nand prepare for calibration.", 13f, ui.muted).apply { setPadding(dp(18), 0, 0, 0) }, LinearLayout.LayoutParams(0, -2, 1f))
            add(qrRow, 20)
        }

        val wrap = FrameLayout(this).apply {
            background = android.graphics.drawable.GradientDrawable(android.graphics.drawable.GradientDrawable.Orientation.TL_BR,
                intArrayOf(Color.rgb(20, 29, 44), Color.rgb(4, 8, 15)))
            setPadding(dp(32), dp(24), dp(32), dp(24))
        }
        val scroll = ScrollView(this).apply { isFillViewport = false; clipToPadding = false; addView(panel); isVerticalScrollBarEnabled = false }
        val width = minOf(dp(680), resources.displayMetrics.widthPixels - dp(64))
        wrap.addView(scroll, FrameLayout.LayoutParams(width, ViewGroup.LayoutParams.MATCH_PARENT, Gravity.CENTER))
        root.addView(wrap, ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        pairing = wrap
        (resume ?: connect).requestFocus()

        if (linked) return
        val seen = mutableSetOf<String>()
        discovery = Discovery(this) { url ->
            if (seen.add(url)) {
                status.text = "Available on your network"
                found.addView(ui.button("Connect to $url") { prefs.serverUrl = url; startPlayer() })
                if (prefs.serverUrl.isEmpty()) {
                    prefs.serverUrl = url
                    startPlayer()
                }
            }
        }.also { it.start() }
    }

    private fun hidePairing() {
        discovery?.stop()
        discovery = null
        pairing?.let { root.removeView(it) }
        pairing = null
        enterImmersive()
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
}
