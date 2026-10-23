package com.projector.app

import com.projector.app.sync.DriftController
import com.projector.app.sync.DriftController.Action
import org.junit.Assert.assertEquals
import org.junit.Test

class DriftControllerTest {
    private val dc = DriftController()

    @Test
    fun smallDriftKeepsNormalSpeed() {
        assertEquals(Action.Speed(1f), dc.decide(20, 5000))
        assertEquals(Action.Speed(1f), dc.decide(-39, 5000))
    }

    @Test
    fun mediumDriftNudgesSpeed() {
        assertEquals(Action.Speed(0.97f), dc.decide(100, 5000))
        assertEquals(Action.Speed(1.03f), dc.decide(-100, 5000))
    }

    @Test
    fun largeDriftSeeks() {
        assertEquals(Action.Seek(5150), dc.decide(400, 5000, 150))
        assertEquals(Action.Seek(5060), dc.decide(-300, 5000))
    }

    @Test
    fun driftWrapsForLoops() {
        // Actual just after loop restart, expected just before the end: really 100 ms ahead.
        assertEquals(100, dc.drift(50, 9950, 10_000, loop = true))
        assertEquals(-100, dc.drift(9950, 50, 10_000, loop = true))
        assertEquals(-9900, dc.drift(50, 9950, 10_000, loop = false))
    }

    @Test
    fun expectedPosition() {
        assertEquals(0, DriftController.expectedPosition(900, 1000, 5000, false))
        assertEquals(2000, DriftController.expectedPosition(3000, 1000, 5000, false))
        assertEquals(5000, DriftController.expectedPosition(99_000, 1000, 5000, false))
        assertEquals(2000, DriftController.expectedPosition(13_000, 1000, 5000, true))
    }
}
