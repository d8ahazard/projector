package com.projector.app.render

import kotlin.math.abs

/** 3x3 homographies stored row-major in a DoubleArray(9). */
object Homography {
    private val UNIT = arrayOf(doubleArrayOf(0.0, 0.0), doubleArrayOf(1.0, 0.0), doubleArrayOf(1.0, 1.0), doubleArrayOf(0.0, 1.0))

    /** Maps the unit square (TL, TR, BR, BL) to the 4 given points. */
    fun fromUnitSquare(dst: Array<DoubleArray>): DoubleArray = solve(UNIT, dst)

    fun solve(src: Array<DoubleArray>, dst: Array<DoubleArray>): DoubleArray {
        require(src.size == 4 && dst.size == 4)
        val a = Array(8) { DoubleArray(9) }
        for (i in 0 until 4) {
            val (x, y) = src[i][0] to src[i][1]
            val (u, v) = dst[i][0] to dst[i][1]
            a[2 * i] = doubleArrayOf(x, y, 1.0, 0.0, 0.0, 0.0, -x * u, -y * u, u)
            a[2 * i + 1] = doubleArrayOf(0.0, 0.0, 0.0, x, y, 1.0, -x * v, -y * v, v)
        }
        // Gaussian elimination with partial pivoting on the 8x8 augmented system.
        for (col in 0 until 8) {
            var pivot = col
            for (r in col + 1 until 8) if (abs(a[r][col]) > abs(a[pivot][col])) pivot = r
            val tmp = a[col]; a[col] = a[pivot]; a[pivot] = tmp
            val p = a[col][col]
            if (abs(p) < 1e-12) throw IllegalArgumentException("degenerate corner pin")
            for (c in col until 9) a[col][c] /= p
            for (r in 0 until 8) {
                if (r == col) continue
                val f = a[r][col]
                if (f == 0.0) continue
                for (c in col until 9) a[r][c] -= f * a[col][c]
            }
        }
        return DoubleArray(9) { if (it < 8) a[it][8] else 1.0 }
    }

    fun invert(m: DoubleArray): DoubleArray {
        val (a, b, c) = Triple(m[0], m[1], m[2])
        val (d, e, f) = Triple(m[3], m[4], m[5])
        val (g, h, i) = Triple(m[6], m[7], m[8])
        val det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
        if (abs(det) < 1e-15) throw IllegalArgumentException("singular homography")
        val inv = doubleArrayOf(
            e * i - f * h, c * h - b * i, b * f - c * e,
            f * g - d * i, a * i - c * g, c * d - a * f,
            d * h - e * g, b * g - a * h, a * e - b * d,
        )
        return DoubleArray(9) { inv[it] / det }
    }

    fun apply(m: DoubleArray, x: Double, y: Double): DoubleArray {
        val w = m[6] * x + m[7] * y + m[8]
        return doubleArrayOf((m[0] * x + m[1] * y + m[2]) / w, (m[3] * x + m[4] * y + m[5]) / w)
    }

    /** Column-major float copy for glUniformMatrix3fv. */
    fun toGlColumnMajor(m: DoubleArray): FloatArray = floatArrayOf(
        m[0].toFloat(), m[3].toFloat(), m[6].toFloat(),
        m[1].toFloat(), m[4].toFloat(), m[7].toFloat(),
        m[2].toFloat(), m[5].toFloat(), m[8].toFloat(),
    )
}
