package com.projector.app.playback

import android.content.Context
import android.graphics.SurfaceTexture
import android.net.Uri
import android.os.SystemClock
import android.view.Surface
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.PlaybackParameters
import androidx.media3.common.Player
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.analytics.AnalyticsListener
import java.io.File

/** One hardware decoder bound to one GL texture slot. Main thread only. */
class Deck(val index: Int, context: Context) {
    val player: ExoPlayer = ExoPlayer.Builder(context)
        .setLoadControl(
            DefaultLoadControl.Builder()
                .setBufferDurationsMs(1500, 4000, 250, 500)
                .build()
        )
        .build()
    @Volatile var loadCount = 0L
        private set
    @Volatile var bufferEvents = 0L
        private set
    @Volatile var bufferMs = 0L
        private set
    @Volatile var droppedFrames = 0L
        private set
    @Volatile var playbackErrors = 0L
        private set
    private var bufferStartedAt = 0L
    private var surface: Surface? = null

    var event: PlayEvent? = null
        private set
    var started = false
        private set
    private var speed = 1f

    init {
        player.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                if (!started) return
                if (state == Player.STATE_BUFFERING) beginBuffering() else finishBuffering()
            }

            override fun onPlayerError(error: PlaybackException) {
                playbackErrors++
                finishBuffering()
            }
        })
        player.addAnalyticsListener(object : AnalyticsListener {
            override fun onDroppedVideoFrames(eventTime: AnalyticsListener.EventTime, droppedFrames: Int, elapsedMs: Long) {
                this@Deck.droppedFrames += droppedFrames.toLong()
            }
        })
    }

    private fun beginBuffering() {
        if (bufferStartedAt == 0L) {
            bufferStartedAt = SystemClock.elapsedRealtime()
            bufferEvents++
        }
    }

    private fun finishBuffering() {
        if (bufferStartedAt != 0L) {
            bufferMs += SystemClock.elapsedRealtime() - bufferStartedAt
            bufferStartedAt = 0L
        }
    }

    val totalBufferMs: Long get() = bufferMs + if (bufferStartedAt == 0L) 0L else SystemClock.elapsedRealtime() - bufferStartedAt

    fun attach(st: SurfaceTexture) {
        surface?.release()
        surface = Surface(st)
        player.setVideoSurface(surface)
    }

    fun load(e: PlayEvent, file: File, startPosMs: Long) {
        loadCount++
        event = e
        started = false
        speed = 1f
        player.playbackParameters = PlaybackParameters(1f)
        player.repeatMode = if (e.loop) Player.REPEAT_MODE_ONE else Player.REPEAT_MODE_OFF
        player.setMediaItem(MediaItem.fromUri(Uri.fromFile(file)), startPosMs)
        player.playWhenReady = false
        player.prepare()
    }

    fun play() {
        player.playWhenReady = true
        started = true
        if (player.playbackState == Player.STATE_BUFFERING) beginBuffering()
    }

    fun setSpeed(s: Float) {
        if (s != speed) {
            speed = s
            player.playbackParameters = PlaybackParameters(s)
        }
    }

    fun setVolume(v: Float) {
        player.volume = v.coerceIn(0f, 1f)
    }

    val isPlaying: Boolean get() = started && player.playbackState == Player.STATE_READY && player.isPlaying

    fun release() {
        finishBuffering()
        player.stop()
        player.clearMediaItems()
        event = null
        started = false
    }

    fun destroy() {
        player.release()
        surface?.release()
    }
}
