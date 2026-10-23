package com.projector.app.playback

data class RectKey(val t: Long, val x: Float, val y: Float, val w: Float, val h: Float, val opacity: Float)

data class TransitionSpec(
    val type: String,
    val durationMs: Long,
    val easing: String,
    val direction: String,
)

/** One scheduled clip on this device. Times are server wall-clock ms. */
data class PlayEvent(
    val id: String,
    val t: Long,
    var end: Long,
    val assetId: String,
    val sha256: String,
    val url: String,
    val durationMs: Long,
    val loop: Boolean,
    val rect: List<RectKey>,
    val transition: TransitionSpec,
    val audio: Boolean = true,
    val flipH: Boolean = false,
) {
    /** Video rect in surface-normalized coords at [elapsedMs] into the event. */
    fun rectAt(elapsedMs: Long): RectKey {
        if (rect.isEmpty()) return RectKey(0, 0f, 0f, 1f, 1f, 1f)
        if (rect.size == 1 || elapsedMs <= rect.first().t) return rect.first()
        if (elapsedMs >= rect.last().t) return rect.last()
        for (i in 0 until rect.size - 1) {
            val a = rect[i]
            val b = rect[i + 1]
            if (elapsedMs in a.t..b.t) {
                val f = if (b.t == a.t) 1f else (elapsedMs - a.t).toFloat() / (b.t - a.t)
                return RectKey(
                    elapsedMs,
                    a.x + (b.x - a.x) * f,
                    a.y + (b.y - a.y) * f,
                    a.w + (b.w - a.w) * f,
                    a.h + (b.h - a.h) * f,
                    a.opacity + (b.opacity - a.opacity) * f,
                )
            }
        }
        return rect.last()
    }
}

/** Sorted, de-duplicated list of upcoming events with server "clear" semantics. */
class EventQueue {
    private val events = mutableListOf<PlayEvent>()

    @Synchronized
    fun add(newEvents: List<PlayEvent>) {
        for (e in newEvents) {
            events.removeAll { it.id == e.id }
            events.add(e)
        }
        events.sortBy { it.t }
    }

    /** Drops events starting at/after [after]; events running past it are cut short. */
    @Synchronized
    fun clearAfter(after: Long) {
        events.removeAll { it.t >= after }
        for (e in events) if (e.end > after) e.end = after
    }

    @Synchronized
    fun prune(now: Long, keepMs: Long = 5000) {
        events.removeAll { it.end < now - keepMs }
    }

    /** The event that should be on screen at [now] (latest start wins). */
    @Synchronized
    fun activeAt(now: Long): PlayEvent? = events.lastOrNull { it.t <= now && it.end > now }

    @Synchronized
    fun nextAfter(now: Long): PlayEvent? = events.firstOrNull { it.t > now }

    @Synchronized
    fun snapshot(): List<PlayEvent> = events.toList()

    @Synchronized
    fun clear() = events.clear()
}

object Easing {
    fun apply(name: String, p: Float): Float {
        val x = p.coerceIn(0f, 1f)
        return when (name) {
            "linear" -> x
            "ease-in" -> x * x
            "ease-out" -> 1f - (1f - x) * (1f - x)
            else -> if (x < 0.5f) 2f * x * x else 1f - 2f * (1f - x) * (1f - x)
        }
    }
}
