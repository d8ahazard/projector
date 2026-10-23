package com.projector.app.sync

import kotlin.math.abs

/**
 * Keeps a decoder on the schedule: small drift is absorbed by nudging playback
 * speed, large drift triggers a seek. Pure logic so it can be unit-tested.
 */
class DriftController(
    private val nudgeThresholdMs: Long = 40,
    private val seekThresholdMs: Long = 250,
    private val nudge: Float = 0.03f,
) {
    sealed class Action {
        data class Speed(val speed: Float) : Action()
        data class Seek(val positionMs: Long) : Action()
    }

    /** Signed drift (actual - expected), wrapping for looping clips. */
    fun drift(actualMs: Long, expectedMs: Long, durationMs: Long, loop: Boolean): Long {
        var d = actualMs - expectedMs
        if (loop && durationMs > 0) {
            d %= durationMs
            if (d > durationMs / 2) d -= durationMs
            if (d < -durationMs / 2) d += durationMs
        }
        return d
    }

    fun decide(driftMs: Long, expectedMs: Long, seekLeadMs: Long = 60): Action {
        val a = abs(driftMs)
        return when {
            a > seekThresholdMs -> Action.Seek(expectedMs + seekLeadMs)
            a > nudgeThresholdMs -> Action.Speed(if (driftMs > 0) 1f - nudge else 1f + nudge)
            else -> Action.Speed(1f)
        }
    }

    companion object {
        /** Where the clip should be at [nowServer] for an event starting at [startServer]. */
        fun expectedPosition(nowServer: Long, startServer: Long, durationMs: Long, loop: Boolean): Long {
            val elapsed = (nowServer - startServer).coerceAtLeast(0)
            return if (loop && durationMs > 0) elapsed % durationMs else elapsed.coerceAtMost(maxOf(durationMs, 0))
        }
    }
}
