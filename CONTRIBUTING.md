# Contributing

Bug reports and focused pull requests are welcome. Please keep commercial media, device identifiers, database files, signing keys, and local network details out of issues and commits.

Before opening a pull request:

```bash
cd server && uv run pytest
cd ../web && npm ci && npm run build
cd ../android && gradle testDebugUnitTest
```

For scheduler changes, include fixtures covering at least two, three, and four surfaces. For media changes, verify duration, cadence, orientation, and audio synchronization. For Android playback changes, report the device, Android version, output resolution and refresh rate without publishing its serial number.

Keep migrations backward-compatible and back up SQLite before altering existing tables. Preserve concrete asset IDs in saved programs.
