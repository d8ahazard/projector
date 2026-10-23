package com.projector.app.net

import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import com.projector.app.playback.PlayEvent
import com.projector.app.playback.RectKey
import com.projector.app.playback.TransitionSpec
import com.projector.app.sync.ClockSync
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Player WebSocket: hello/clock-sync/heartbeat out, config/schedule/guide in.
 * All callbacks are delivered on the main thread. Reconnects forever.
 */
class ServerConnection(
    private val serverUrl: String,
    private val helloProvider: () -> JSONObject,
    private val heartbeatProvider: () -> JSONObject,
    private val listener: Listener,
) {
    interface Listener {
        fun onConnected()
        fun onDisconnected()
        fun onMessage(type: String, msg: JSONObject)
    }

    val clock = ClockSync()
    private val main = Handler(Looper.getMainLooper())
    private val client = OkHttpClient.Builder()
        .pingInterval(15, TimeUnit.SECONDS)
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()
    private var ws: WebSocket? = null
    private var stopped = false
    private var backoffMs = 1000L

    @Volatile
    var connected = false
        private set

    fun start() {
        stopped = false
        connect()
    }

    fun stop() {
        stopped = true
        main.removeCallbacksAndMessages(null)
        ws?.close(1000, "bye")
        ws = null
    }

    fun send(obj: JSONObject) {
        ws?.send(obj.toString())
    }

    private fun localNow(): Double = SystemClock.elapsedRealtimeNanos() / 1_000_000.0

    private fun connect() {
        if (stopped) return
        val wsUrl = serverUrl.replaceFirst("http", "ws") + "/ws/player"
        val req = Request.Builder().url(wsUrl).build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                backoffMs = 1000L
                webSocket.send(helloProvider().put("type", "hello").toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val t1 = localNow()
                val msg = try { JSONObject(text) } catch (e: Exception) { return }
                val type = msg.optString("type")
                if (type == "pong") {
                    clock.addSample(msg.getDouble("t0"), msg.getDouble("ts"), t1)
                    return
                }
                main.post {
                    if (type == "welcome") {
                        connected = true
                        clock.reset()
                        startClockSync()
                        startHeartbeat()
                        listener.onConnected()
                    }
                    listener.onMessage(type, msg)
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = handleDrop()

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "socket failure: ${t.message}")
                handleDrop()
            }
        })
    }

    private fun handleDrop() {
        main.post {
            if (connected) {
                connected = false
                listener.onDisconnected()
            }
            main.removeCallbacks(pingBurst)
            main.removeCallbacks(heartbeat)
            if (!stopped) {
                main.postDelayed({ connect() }, backoffMs)
                backoffMs = (backoffMs * 2).coerceAtMost(10_000L)
            }
        }
    }

    private var burstRemaining = 0

    private val pingBurst = object : Runnable {
        override fun run() {
            if (!connected) return
            send(JSONObject().put("type", "ping").put("t0", localNow()))
            burstRemaining--
            if (burstRemaining > 0) {
                main.postDelayed(this, 60)
            } else {
                // Re-sync every 30 s with a short burst to track oscillator drift.
                burstRemaining = 6
                main.postDelayed(this, 30_000)
            }
        }
    }

    private fun startClockSync() {
        main.removeCallbacks(pingBurst)
        burstRemaining = 20
        main.post(pingBurst)
    }

    private val heartbeat = object : Runnable {
        override fun run() {
            if (!connected) return
            val hb = heartbeatProvider()
                .put("type", "heartbeat")
                .put("offset_ms", clock.offsetMs)
                .put("rtt_ms", clock.rttMs)
            send(hb)
            main.postDelayed(this, 2000)
        }
    }

    private fun startHeartbeat() {
        main.removeCallbacks(heartbeat)
        main.postDelayed(heartbeat, 2000)
    }

    /** Server time "now" according to the synced clock. */
    fun serverNow(): Long = clock.serverNow(localNow()).toLong()

    companion object {
        private const val TAG = "ServerConnection"

        fun parseEvent(o: JSONObject): PlayEvent {
            val rects = mutableListOf<RectKey>()
            val ra = o.optJSONArray("rect") ?: JSONArray()
            for (i in 0 until ra.length()) {
                val r = ra.getJSONObject(i)
                rects.add(
                    RectKey(
                        r.optLong("t"),
                        r.optDouble("x", 0.0).toFloat(),
                        r.optDouble("y", 0.0).toFloat(),
                        r.optDouble("w", 1.0).toFloat(),
                        r.optDouble("h", 1.0).toFloat(),
                        r.optDouble("opacity", 1.0).toFloat(),
                    )
                )
            }
            val tr = o.optJSONObject("transition") ?: JSONObject()
            return PlayEvent(
                id = o.getString("id"),
                t = o.getLong("t"),
                end = o.getLong("end"),
                assetId = o.optString("asset_id"),
                sha256 = o.optString("sha256"),
                url = o.optString("url"),
                durationMs = o.optLong("duration_ms"),
                loop = o.optBoolean("loop"),
                audio = o.optBoolean("audio", true),
                flipH = o.optBoolean("flip_h", false),
                rect = rects,
                transition = TransitionSpec(
                    type = tr.optString("type", "dip"),
                    durationMs = tr.optLong("duration", 800),
                    easing = tr.optString("easing", "ease-in-out"),
                    direction = tr.optString("direction", "right"),
                ),
            )
        }
    }
}
