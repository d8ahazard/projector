package com.projector.app.controller

import org.junit.Assert.*
import org.junit.Test

class ControllerPolicyTest {
    @Test fun originRequiresExactHostAndPort() {
        val base = "http://192.168.1.50:8000"
        assertTrue(ControllerPolicy.sameOrigin(base, "$base/calibrate"))
        assertFalse(ControllerPolicy.sameOrigin(base, "http://192.168.1.50:8000.evil.test/"))
        assertFalse(ControllerPolicy.sameOrigin(base, "http://192.168.1.50:8001/"))
        assertFalse(ControllerPolicy.sameOrigin(base, "http://192.168.1.50:8000@evil.test/"))
        assertFalse(ControllerPolicy.sameOrigin(base, "file:///etc/passwd"))
    }
    @Test fun cameraUploadIsLimitedToCalibrationOnTheSavedServer() {
        val base = "http://192.168.1.50:8000"
        assertEquals("$base/api/calibration/abc123/photo", ControllerPolicy.calibrationUrl(base, "/api/calibration/abc123/photo"))
        assertEquals("$base/api/calibration/color/abc123/photo", ControllerPolicy.calibrationUrl(base, "/api/calibration/color/abc123/photo"))
        assertNull(ControllerPolicy.calibrationUrl(base, "http://elsewhere.test/api/calibration/abc123/photo"))
        assertNull(ControllerPolicy.calibrationUrl(base, "/api/devices/x/command"))
        assertNull(ControllerPolicy.calibrationUrl(base, "/api/calibration/%2e%2e/photo"))
    }
}
