package com.projector.app.render

object Shaders {
    const val VERTEX = """
attribute vec2 aPos;
varying vec2 vPos;
void main() {
    // vPos: output-normalized, origin top-left, y down.
    vPos = vec2(aPos.x * 0.5 + 0.5, 0.5 - aPos.y * 0.5);
    gl_Position = vec4(aPos, 0.0, 1.0);
}
"""

    /**
     * Per output pixel: invert the corner pin to find the surface coordinate, apply the
     * mask, mirror the content, sample up to two decoders (each with its own video rect),
     * blend by alpha or a directional wipe, then color-correct.
     */
    const val FRAGMENT = """
#extension GL_OES_EGL_image_external : require
precision highp float;
varying vec2 vPos;
uniform samplerExternalOES uTex0;
uniform samplerExternalOES uTex1;
uniform mat4 uTexM0;
uniform mat4 uTexM1;
uniform sampler2D uMask;
uniform mat3 uHinv;
uniform vec2 uMirror;
uniform vec4 uRect0;
uniform vec4 uRect1;
uniform vec2 uAlpha;
uniform vec2 uDeckFlipH;
uniform vec3 uGain;
uniform float uGamma;
uniform float uBright;
uniform vec4 uWipe;
uniform vec2 uWipeDir;

bool inside(vec2 uv) {
    return uv.x >= 0.0 && uv.y >= 0.0 && uv.x <= 1.0 && uv.y <= 1.0;
}

vec3 deck0(vec2 s) {
    vec2 uv = (s - uRect0.xy) / uRect0.zw;
    if (!inside(uv)) return vec3(0.0);
    if (uDeckFlipH.x > 0.5) uv.x = 1.0 - uv.x;
    vec2 tc = (uTexM0 * vec4(uv.x, 1.0 - uv.y, 0.0, 1.0)).xy;
    return texture2D(uTex0, tc).rgb;
}

vec3 deck1(vec2 s) {
    vec2 uv = (s - uRect1.xy) / uRect1.zw;
    if (!inside(uv)) return vec3(0.0);
    if (uDeckFlipH.y > 0.5) uv.x = 1.0 - uv.x;
    vec2 tc = (uTexM1 * vec4(uv.x, 1.0 - uv.y, 0.0, 1.0)).xy;
    return texture2D(uTex1, tc).rgb;
}

void main() {
    vec3 q = uHinv * vec3(vPos, 1.0);
    if (q.z <= 0.0) { gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0); return; }
    vec2 s = q.xy / q.z;
    if (!inside(s)) { gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0); return; }
    float m = texture2D(uMask, s).r;
    vec2 cs = s;
    if (uMirror.x > 0.5) cs.x = 1.0 - cs.x;
    if (uMirror.y > 0.5) cs.y = 1.0 - cs.y;

    float w0 = uAlpha.x;
    float w1 = uAlpha.y;
    if (uWipe.x > 0.5) {
        float norm = 0.5 * (abs(uWipeDir.x) + abs(uWipeDir.y));
        float d = dot(cs - 0.5, uWipeDir) / max(norm, 0.0001) * 0.5 + 0.5;
        float soft = uWipe.w;
        float edge = uWipe.z * (1.0 + soft);
        float reveal = 1.0 - smoothstep(edge - soft, edge, d);
        if (uWipe.y < 0.5) { w0 *= reveal; w1 *= (1.0 - reveal); }
        else { w1 *= reveal; w0 *= (1.0 - reveal); }
    }
    vec3 c = vec3(0.0);
    if (w0 > 0.001) c += deck0(cs) * w0;
    if (w1 > 0.001) c += deck1(cs) * w1;
    c = clamp(c * uGain, 0.0, 1.0);
    c = pow(c, vec3(uGamma)) * uBright * m;
    gl_FragColor = vec4(c, 1.0);
}
"""
}
