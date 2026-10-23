package com.projector.app

import com.projector.app.render.Homography
import org.junit.Assert.assertArrayEquals
import org.junit.Test

class HomographyTest {
    private val corners = arrayOf(
        doubleArrayOf(0.12, 0.2), doubleArrayOf(0.85, 0.1), doubleArrayOf(0.8, 0.95), doubleArrayOf(0.2, 0.8),
    )

    @Test
    fun mapsUnitSquareToCorners() {
        val h = Homography.fromUnitSquare(corners)
        val unit = arrayOf(doubleArrayOf(0.0, 0.0), doubleArrayOf(1.0, 0.0), doubleArrayOf(1.0, 1.0), doubleArrayOf(0.0, 1.0))
        for (i in 0 until 4) {
            assertArrayEquals(corners[i], Homography.apply(h, unit[i][0], unit[i][1]), 1e-9)
        }
    }

    @Test
    fun inverseRoundTrips() {
        val h = Homography.fromUnitSquare(corners)
        val inv = Homography.invert(h)
        for (p in listOf(doubleArrayOf(0.3, 0.7), doubleArrayOf(0.5, 0.5), doubleArrayOf(0.9, 0.1))) {
            val q = Homography.apply(h, p[0], p[1])
            assertArrayEquals(p, Homography.apply(inv, q[0], q[1]), 1e-9)
        }
    }

    @Test
    fun columnMajorLayout() {
        val m = doubleArrayOf(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0)
        assertArrayEquals(floatArrayOf(1f, 4f, 7f, 2f, 5f, 8f, 3f, 6f, 9f), Homography.toGlColumnMajor(m), 0f)
    }

    @Test(expected = IllegalArgumentException::class)
    fun degenerateThrows() {
        Homography.fromUnitSquare(Array(4) { doubleArrayOf(0.5, 0.5) })
    }
}
