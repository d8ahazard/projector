# Android players and phone controller

The same APK supports two roles.

## TV-stick player

Install the APK on an Android TV / Google TV device and point it at the server's LAN URL. Projector discovers servers over mDNS when available. Each stick receives a persistent identity and surface assignment.

The optional dedicated-player setup can:

- expose Projector as a Home activity
- keep the player awake and return Home/Back to projection
- enable boot recovery and signed in-app updates
- disable HDMI-CEC behavior that sleeps the stick when the TV input changes
- snapshot and reversibly disable a small, explicit entertainment-app list
- retain Android Settings, networking, Bluetooth remote support, codecs, WebView, package installation, and firmware updates

A stock device requires one initial Wireless debugging pairing. The controller's **Add a stick** wizard explains the steps. Root and bootloader unlocking are unnecessary.

## Phone controller

Install the APK on a phone and select Controller. The phone hosts no player WebSocket and receives no server-controlled Home, boot, overlay, package-install, or maintenance permissions. It opens the responsive web controller and adds camera capture, QR setup, and a phone-initiated app update command.

## Updates

The server publishes a signed APK and release manifest. A player verifies the package name, higher version code, length, SHA-256, and signing certificate before installation. Keep the signing key safe: Android will reject an update signed by a different key.

Some firmware requires on-screen install confirmation. Test updates on one player before updating an entire display.
