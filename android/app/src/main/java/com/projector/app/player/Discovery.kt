package com.projector.app.player

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper

/** Finds the server via mDNS (_projector._tcp). Best-effort; manual entry is the fallback. */
class Discovery(context: Context, private val onFound: (String) -> Unit) {
    private val nsd = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    private var lock: WifiManager.MulticastLock? = null
    private val main = Handler(Looper.getMainLooper())
    private var active = false

    private val listener = object : NsdManager.DiscoveryListener {
        override fun onDiscoveryStarted(serviceType: String) {}
        override fun onDiscoveryStopped(serviceType: String) {}
        override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {}
        override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
        override fun onServiceLost(serviceInfo: NsdServiceInfo) {}

        override fun onServiceFound(serviceInfo: NsdServiceInfo) {
            @Suppress("DEPRECATION")
            nsd.resolveService(serviceInfo, object : NsdManager.ResolveListener {
                override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {}
                override fun onServiceResolved(info: NsdServiceInfo) {
                    @Suppress("DEPRECATION")
                    val host = info.host?.hostAddress ?: return
                    main.post { onFound("http://$host:${info.port}") }
                }
            })
        }
    }

    fun start() {
        if (active) return
        active = true
        lock = wifi.createMulticastLock("projector-mdns").apply { setReferenceCounted(false); acquire() }
        try {
            nsd.discoverServices("_projector._tcp", NsdManager.PROTOCOL_DNS_SD, listener)
        } catch (_: Exception) {
        }
    }

    fun stop() {
        if (!active) return
        active = false
        try {
            nsd.stopServiceDiscovery(listener)
        } catch (_: Exception) {
        }
        lock?.release()
    }
}
