package com.projector.app.playback

object PlaybackCadence {
    // Keep display-frame scheduling near a start even on single-decoder devices.
    fun delayMs(now: Long, nextStart: Long?, active: Boolean, loaded: Boolean): Long =
        if (active || loaded || nextStart != null && nextStart - now <= 3000L) 0L else 250L
}
