package com.projector.app

import com.projector.app.playback.EventQueue
import com.projector.app.playback.PlayEvent
import com.projector.app.playback.RectKey
import com.projector.app.playback.TransitionSpec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class EventQueueTest {
    private fun ev(id: String, t: Long, end: Long, rect: List<RectKey> = emptyList()) =
        PlayEvent(id, t, end, "a", "sha", "/a.mp4", end - t, false, rect, TransitionSpec("cut", 0, "linear", "right"))

    @Test
    fun activeAndNext() {
        val q = EventQueue()
        q.add(listOf(ev("b", 1000, 2000), ev("a", 0, 1000), ev("c", 1500, 3000)))
        assertEquals("a", q.activeAt(500)?.id)
        assertEquals("b", q.nextAfter(500)?.id)
        assertEquals("c", q.activeAt(1600)?.id)
        assertNull(q.activeAt(3500))
    }

    @Test
    fun clearAfterDropsAndTruncates() {
        val q = EventQueue()
        q.add(listOf(ev("a", 0, 5000), ev("b", 5000, 9000)))
        q.clearAfter(3000)
        assertEquals(1, q.snapshot().size)
        assertEquals(3000, q.snapshot()[0].end)
    }

    @Test
    fun duplicateIdsReplace() {
        val q = EventQueue()
        q.add(listOf(ev("a", 0, 1000)))
        q.add(listOf(ev("a", 0, 2000)))
        assertEquals(1, q.snapshot().size)
        assertEquals(2000, q.snapshot()[0].end)
    }

    @Test
    fun pruneRemovesOld() {
        val q = EventQueue()
        q.add(listOf(ev("a", 0, 1000), ev("b", 1000, 20_000)))
        q.prune(10_000)
        assertEquals(listOf("b"), q.snapshot().map { it.id })
    }

    @Test
    fun rectInterpolates() {
        val e = ev("a", 0, 10_000, listOf(RectKey(0, 0f, 0f, 1f, 1f, 1f), RectKey(1000, 1f, 0f, 1f, 1f, 0f)))
        val r = e.rectAt(500)
        assertEquals(0.5f, r.x, 1e-6f)
        assertEquals(0.5f, r.opacity, 1e-6f)
        assertEquals(1f, e.rectAt(5000).x, 1e-6f)
    }
}
