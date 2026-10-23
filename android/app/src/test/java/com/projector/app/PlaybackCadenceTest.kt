package com.projector.app

import com.projector.app.playback.PlaybackCadence
import org.junit.Assert.assertEquals
import org.junit.Test

class PlaybackCadenceTest {
    @Test fun emptyAndDistantSchedulesStayIdle() {
        assertEquals(250L, PlaybackCadence.delayMs(1000, null, false, false))
        assertEquals(250L, PlaybackCadence.delayMs(1000, 10000, false, false))
    }
    @Test fun singleDecoderWakesBeforeUpcomingStart() {
        assertEquals(0L, PlaybackCadence.delayMs(7000, 10000, false, false))
        assertEquals(0L, PlaybackCadence.delayMs(9999, 10000, false, false))
    }
    @Test fun activeOrPreloadedVideoKeepsFrameScheduling() {
        assertEquals(0L, PlaybackCadence.delayMs(1000, null, true, false))
        assertEquals(0L, PlaybackCadence.delayMs(1000, null, false, true))
    }
}
