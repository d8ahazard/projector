package com.projector.app.sync

/**
 * NTP-style offset estimation between a local monotonic clock and the server's wall clock.
 * Keeps a sliding window of samples and uses the median offset of the lowest-RTT third,
 * which rejects samples delayed by Wi-Fi queuing.
 */
class ClockSync(private val window: Int = 40) {
    private data class Sample(val offset: Double, val rtt: Double)

    private val samples = ArrayDeque<Sample>()

    @Volatile
    var offsetMs: Double = 0.0
        private set

    @Volatile
    var rttMs: Double = 0.0
        private set

    val sampleCount: Int get() = synchronized(samples) { samples.size }

    val isSynced: Boolean get() = sampleCount >= 5

    /** t0/t1 are local send/receive times, serverTs is the server's clock when it replied. */
    fun addSample(t0: Double, serverTs: Double, t1: Double) {
        val rtt = t1 - t0
        if (rtt < 0 || rtt > 5000) return
        val offset = serverTs - (t0 + rtt / 2.0)
        synchronized(samples) {
            samples.addLast(Sample(offset, rtt))
            while (samples.size > window) samples.removeFirst()
            val best = samples.sortedBy { it.rtt }.take(maxOf(1, samples.size / 3))
            val offs = best.map { it.offset }.sorted()
            offsetMs = if (offs.size % 2 == 1) offs[offs.size / 2] else (offs[offs.size / 2 - 1] + offs[offs.size / 2]) / 2.0
            rttMs = best.first().rtt
        }
    }

    fun reset() {
        synchronized(samples) { samples.clear() }
    }

    fun serverNow(localMs: Double): Double = localMs + offsetMs

    fun toLocal(serverMs: Double): Double = serverMs - offsetMs
}
