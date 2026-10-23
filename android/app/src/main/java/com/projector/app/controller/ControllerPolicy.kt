package com.projector.app.controller

import java.net.URI

/** Controller navigation is confined to the saved server; native actions still need a local tap. */
object ControllerPolicy {
    fun sameOrigin(base: String, target: String): Boolean = try {
        val a = URI(base); val b = URI(target)
        fun port(u: URI) = if (u.port >= 0) u.port else if (u.scheme == "https") 443 else 80
        a.scheme in listOf("http", "https") && a.scheme == b.scheme &&
            a.host != null && a.host.equals(b.host, ignoreCase = true) && port(a) == port(b) &&
            a.userInfo == null && b.userInfo == null
    } catch (_: Exception) { false }

    fun calibrationUrl(base: String, target: String): String? = try {
        val resolved = URI(base.trimEnd('/') + "/").resolve(target).normalize()
        if (sameOrigin(base, resolved.toString()) && resolved.rawPath.matches(
                Regex("/api/calibration/(color/)?[a-zA-Z0-9_-]+/photo")) && resolved.query == null && resolved.fragment == null)
            resolved.toString() else null
    } catch (_: Exception) { null }
}
