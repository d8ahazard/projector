"""mDNS advertisement so players can find the server without typing an IP.

Only works when the server runs directly on the host or in Docker with host
networking on Linux; Docker Desktop on Windows/macOS can't multicast from a
container, which is why players also accept a manually entered address.
"""

import logging
import socket

from .config import PORT, lan_ip

log = logging.getLogger(__name__)
_zc = None
_info = None


def start() -> None:
    global _zc, _info
    try:
        from zeroconf import ServiceInfo, Zeroconf

        ip = lan_ip()
        _info = ServiceInfo(
            "_projector._tcp.local.",
            "Projector Server._projector._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=PORT,
            properties={"path": "/"},
        )
        _zc = Zeroconf()
        _zc.register_service(_info)
        log.info("mDNS: advertising _projector._tcp on %s:%s", ip, PORT)
    except Exception as e:  # mDNS is best-effort
        log.warning("mDNS advertisement unavailable: %s", e)
        _zc = None


def stop() -> None:
    if _zc and _info:
        try:
            _zc.unregister_service(_info)
            _zc.close()
        except Exception:
            pass
