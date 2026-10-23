# Security

Projector is a trusted-LAN application. Its controller can start playback, change calibration, publish signed player updates, and optionally provision a selected Android TV device through ADB. It does not provide Internet-grade authentication.

- Keep the server and players on a private network you control.
- Do not port-forward TCP 8000 or the Android debugging ports.
- Use a VPN with access controls if you need remote administration.
- Keep `data/`, Android signing keys, `.env` files, device snapshots, and ADB keys out of Git.
- Disable Wireless debugging after provisioning if you do not need maintenance recovery.
- Review imported ZIPs and media from unknown sources before playback.

To report a vulnerability, open a private GitHub security advisory on the repository. Do not include signing keys, device serials, LAN addresses, or licensed media in a public issue.
