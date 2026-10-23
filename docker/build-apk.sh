#!/usr/bin/env bash
# Builds a release APK signed with a persistent keystore so updates install
# over previous versions. Output: /out/projector.apk
set -euo pipefail

KEYSTORE=/keystore/release.jks
mkdir -p /keystore /out
if [ ! -f "$KEYSTORE" ]; then
  echo "Generating signing keystore at $KEYSTORE (keep this file to allow app updates)"
  keytool -genkeypair -v -keystore "$KEYSTORE" -alias projector -keyalg RSA -keysize 2048 \
    -validity 10000 -storepass projector -keypass projector -dname "CN=Projector, O=Home" > /dev/null
fi

export PROJECTOR_KEYSTORE="$KEYSTORE"
export PROJECTOR_VERSION_CODE=$(( $(date +%s) / 60 ))

gradle --no-daemon -q assembleRelease
cp app/build/outputs/apk/release/app-release.apk /out/projector.apk
echo "APK written to /out/projector.apk (versionCode $PROJECTOR_VERSION_CODE)"
