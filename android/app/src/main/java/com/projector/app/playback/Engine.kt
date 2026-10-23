package com.projector.app.playback

import android.content.Context
import android.graphics.SurfaceTexture
import android.util.Log
import android.view.Choreographer
import com.projector.app.media.MediaCache
import com.projector.app.render.RenderState
import com.projector.app.render.VideoRenderer
import com.projector.app.sync.DriftController

data class PlayerConfig(
    val hInv: FloatArray = floatArrayOf(1f, 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f),
    val mirrorH: Boolean = false,
    val mirrorV: Boolean = false,
    val gain: FloatArray = floatArrayOf(1f, 1f, 1f),
    val gamma: Float = 1f,
    val brightness: Float = 1f,
    val volume: Float = 1f,
    val audio: Boolean = true,
)

/**
 * Runs the schedule on the synced clock, once per display frame:
 * keeps the active event on a decoder, preloads the next one on the spare decoder,
 * computes transition alphas / wipe progress, and corrects drift locally.
 */
class Engine(
    private val context: Context,
    private val renderer: VideoRenderer,
    private val cache: MediaCache,
    private val serverNow: () -> Long,
    private val clockReady: () -> Boolean,
    dualDecoder: Boolean,
    private val requestRender: () -> Unit = {},
) : Choreographer.FrameCallback {
    val queue = EventQueue()
    private val nDecks = if (dualDecoder) 2 else 1
    private val decks = mutableListOf<Deck>()
    private val drift = DriftController()
    private val lastSeek = HashMap<Int, Long>()
    private var lastDriftCheck = 0L
    private var running = false
    private var wasVisible = false
    val loadedDecoders: Int get() = decks.count { it.event != null }
    val loadCount: Long get() = decks.sumOf { it.loadCount }
    val bufferEvents: Long get() = decks.sumOf { it.bufferEvents }
    val bufferMs: Long get() = decks.sumOf { it.totalBufferMs }
    val droppedFrames: Long get() = decks.sumOf { it.droppedFrames }
    val playbackErrors: Long get() = decks.sumOf { it.playbackErrors }

    @Volatile var config = PlayerConfig()
    @Volatile var hidden = false
    @Volatile var lastDriftMs = 0L
        private set
    @Volatile var currentEventId: String? = null
        private set
    @Volatile var currentAssetId: String? = null
        private set

    fun attachSurfaces(surfaces: List<SurfaceTexture>) {
        if (decks.isEmpty()) repeat(nDecks) { decks.add(Deck(it, context)) }
        decks.forEachIndexed { i, d -> d.attach(surfaces[i]) }
        if (!running) {
            running = true
            Choreographer.getInstance().postFrameCallback(this)
        }
    }

    fun stop() {
        running = false
        Choreographer.getInstance().removeFrameCallback(this)
        decks.forEach { it.destroy() }
        decks.clear()
        black()
    }

    fun onClear(after: Long) {
        queue.clearAfter(after)
        decks.forEach { d -> d.event?.let { if (it.t >= after) d.release() } }
    }

    fun clearAll() {
        queue.clear()
        decks.forEach { it.release() }
        black()
    }

    private fun black() {
        currentEventId = null
        currentAssetId = null
        lastDriftMs = 0
        renderer.state.set(RenderState(visible = false))
        wasVisible = false
        requestRender()
    }

    override fun doFrame(frameTimeNanos: Long) {
        if (!running) return
        try {
            tick()
        } catch (e: Exception) {
            Log.e(TAG, "tick failed", e)
        }
        val now = serverNow()
        val delay = PlaybackCadence.delayMs(now, queue.nextAfter(now)?.t, currentEventId != null, loadedDecoders > 0)
        Choreographer.getInstance().postFrameCallbackDelayed(this, delay)
    }

    private fun effective(t: TransitionSpec): TransitionSpec {
        if (nDecks == 1 && (t.type == "crossfade" || t.type == "wipe")) {
            return t.copy(type = "dip", durationMs = maxOf(t.durationMs, 400))
        }
        return t
    }

    private fun ratio(num: Long, den: Long): Float = if (den <= 0) 1f else (num.toFloat() / den).coerceIn(0f, 1f)

    private fun tick() {
        if (!clockReady() || decks.isEmpty()) {
            if (wasVisible) black()
            return
        }
        val now = serverNow()
        queue.prune(now)
        val active = queue.activeAt(now)
        val next = queue.nextAfter(now)

        // 1. Keep the active event on a decoder (late joins and single-decoder sticks load here).
        var cur = active?.let { a -> decks.firstOrNull { it.event?.id == a.id } }
        if (active != null && cur == null) {
            val file = cache.fileFor(active.sha256)
            if (file != null) {
                val d = decks.firstOrNull { it.event == null }
                    ?: decks.firstOrNull { it.event?.id != next?.id && (it.event?.end ?: 0L) <= now }
                    ?: decks.firstOrNull { it.event?.id != next?.id }
                    ?: decks[0]
                val late = now - active.t
                val pos = if (late > 100) DriftController.expectedPosition(now + 250, active.t, active.durationMs, active.loop) else 0L
                if (d.event != null) d.release()
                d.load(active, file, pos)
                cache.touch(active.sha256)
                cur = d
            }
        }
        if (cur != null && active != null && !cur.started && now >= active.t - 15) cur.play()

        // 2. Preload the next event on the spare decoder so the switch is gapless.
        if (nDecks > 1 && next != null && next.t - now < PREPARE_MS && decks.none { it.event?.id == next.id }) {
            val spare = decks.firstOrNull { it !== cur && (it.event == null || it.event!!.end <= now) }
            val file = cache.fileFor(next.sha256)
            if (spare != null && file != null) {
                if (spare.event != null) spare.release()
                spare.load(next, file, 0)
                cache.touch(next.sha256)
            }
        }

        // 3. Alphas, rects and wipe for the active deck and (during overlap) the outgoing one.
        val alpha = floatArrayOf(0f, 0f)
        val rects = arrayOf(floatArrayOf(0f, 0f, 1f, 1f), floatArrayOf(0f, 0f, 1f, 1f))
        val deckFlipH = booleanArrayOf(false, false)
        var wipeOn = false
        var wipeTarget = 1
        var wipeProgress = 0f
        var wipeDir = floatArrayOf(1f, 0f)
        var outgoing: Deck? = null

        if (cur != null && active != null) {
            val tr = effective(active.transition)
            val el = now - active.t
            val d = tr.durationMs
            val inA = when (tr.type) {
                "cut", "wipe" -> 1f
                "dip" -> Easing.apply(tr.easing, ratio(el, d / 2))
                else -> Easing.apply(tr.easing, ratio(el, d))
            }
            val succ = next?.takeIf { it.t <= active.end + 50 }
            val outA = if (succ == null) {
                ratio(active.end - now, END_FADE_MS)
            } else {
                val st = effective(succ.transition)
                if (st.type == "dip") ratio(succ.t - now, st.durationMs / 2) else 1f
            }
            val r = active.rectAt(el)
            alpha[cur.index] = minOf(inA, outA) * r.opacity
            rects[cur.index] = floatArrayOf(r.x, r.y, r.w, r.h)
            deckFlipH[cur.index] = active.flipH

            if (tr.type == "wipe" && el < d) {
                wipeOn = true
                wipeTarget = cur.index
                wipeProgress = Easing.apply(tr.easing, ratio(el, d))
                wipeDir = when (tr.direction) {
                    "left" -> floatArrayOf(-1f, 0f)
                    "up" -> floatArrayOf(0f, -1f)
                    "down" -> floatArrayOf(0f, 1f)
                    else -> floatArrayOf(1f, 0f)
                }
            }

            outgoing = decks.firstOrNull { it !== cur && it.started && it.event != null && it.event!!.id != next?.id }
            outgoing?.let { o ->
                val oe = o.event!!
                val oRect = oe.rectAt(now - oe.t)
                rects[o.index] = floatArrayOf(oRect.x, oRect.y, oRect.w, oRect.h)
                deckFlipH[o.index] = oe.flipH
                alpha[o.index] = when {
                    el >= d -> 0f
                    tr.type == "crossfade" -> (1f - Easing.apply(tr.easing, ratio(el, d))) * oRect.opacity
                    tr.type == "wipe" -> oRect.opacity
                    else -> 0f
                }
            }
        }

        // 4. Release decoders that are no longer needed.
        for (dk in decks) {
            val e = dk.event ?: continue
            if (e.id == active?.id || e.id == next?.id) continue
            if (dk === outgoing && alpha[dk.index] > 0f) continue
            dk.release()
        }

        val cfg = config
        decks.forEach { it.setVolume(if (cfg.audio && it.event?.audio != false) cfg.volume * alpha[it.index] else 0f) }

        // 5. Local drift correction against the schedule.
        if (now - lastDriftCheck >= DRIFT_INTERVAL_MS) {
            lastDriftCheck = now
            for (dk in decks) {
                val e = dk.event ?: continue
                if (!dk.isPlaying) continue
                val expected = DriftController.expectedPosition(now, e.t, e.durationMs, e.loop)
                if (!e.loop && expected >= e.durationMs - 150) continue
                val dr = drift.drift(dk.player.currentPosition, expected, e.durationMs, e.loop)
                if (dk === cur) lastDriftMs = dr
                if (now - (lastSeek[dk.index] ?: 0L) < SEEK_COOLDOWN_MS) continue
                when (val action = drift.decide(dr, expected, SEEK_LEAD_MS)) {
                    is DriftController.Action.Seek -> {
                        dk.player.seekTo(action.positionMs.coerceAtMost(maxOf(e.durationMs - 1, 0)))
                        dk.setSpeed(1f)
                        lastSeek[dk.index] = now
                    }
                    is DriftController.Action.Speed -> dk.setSpeed(action.speed)
                }
            }
        }

        currentEventId = active?.id
        currentAssetId = active?.assetId
        renderer.state.set(
            RenderState(
                visible = !hidden && (alpha[0] > 0f || alpha[1] > 0f),
                hInv = cfg.hInv,
                mirrorH = cfg.mirrorH,
                mirrorV = cfg.mirrorV,
                gain = cfg.gain,
                gamma = cfg.gamma,
                brightness = cfg.brightness,
                alpha = alpha,
                rect0 = rects[0],
                rect1 = rects[1],
                deckFlipH = deckFlipH,
                wipeOn = wipeOn,
                wipeTarget = wipeTarget,
                wipeProgress = wipeProgress,
                wipeDir = wipeDir,
            )
        )
        val visible = renderer.state.get().visible
        if (visible || wasVisible) requestRender()
        wasVisible = visible
    }

    companion object {
        private const val TAG = "Engine"
        const val PREPARE_MS = 3000L
        const val END_FADE_MS = 300L
        const val DRIFT_INTERVAL_MS = 500L
        const val SEEK_COOLDOWN_MS = 1500L
        const val SEEK_LEAD_MS = 150L
    }
}
