package com.projector.app

import com.projector.app.sync.ClockSync
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class ClockSyncTest {
    @Test
    fun symmetricRttGivesExactOffset() {
        val c = ClockSync()
        // Server is 1000 ms ahead; 20 ms each way.
        repeat(10) { i ->
            val t0 = i * 100.0
            c.addSample(t0, t0 + 20 + 1000, t0 + 40)
        }
        assertEquals(1000.0, c.offsetMs, 0.001)
        assertEquals(40.0, c.rttMs, 0.001)
        assertTrue(c.isSynced)
    }

    @Test
    fun rejectsQueuedSamplesByUsingLowestRtt() {
        val c = ClockSync()
        val rnd = Random(42)
        val trueOffset = -523.0
        repeat(40) { i ->
            val t0 = i * 50.0
            val up = 5.0 + if (rnd.nextDouble() < 0.4) rnd.nextDouble() * 300 else 0.0
            val down = 5.0 + if (rnd.nextDouble() < 0.4) rnd.nextDouble() * 300 else 0.0
            c.addSample(t0, t0 + up + trueOffset, t0 + up + down)
        }
        assertEquals(trueOffset, c.offsetMs, 3.0)
    }

    @Test
    fun ignoresNegativeOrHugeRtt() {
        val c = ClockSync()
        c.addSample(100.0, 0.0, 50.0)
        c.addSample(0.0, 0.0, 10_000.0)
        assertEquals(0, c.sampleCount)
    }

    @Test
    fun convertsBetweenClocks() {
        val c = ClockSync()
        c.addSample(0.0, 510.0, 20.0)
        assertEquals(1500.0, c.serverNow(1000.0), 0.001)
        assertEquals(1000.0, c.toLocal(1500.0), 0.001)
    }
}
