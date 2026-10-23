# Architecture

Projector has three runtime components:

1. **Server** — FastAPI, SQLite, catalogue processing, schedule compiler, WebSocket sync, release hosting.
2. **Controller** — responsive React UI in a browser or the Android phone shell. The phone adds camera capture and self-initiated updates.
3. **Player** — Android Media3 playback with local cache, shared-clock scheduling, and GPU output geometry.

The source catalogue stores metadata for originals without copying them. Preparation creates concrete `MediaAsset` records only for selected actions. Saved cues reference stable asset IDs. Calibration-aware compilation can exchange an anchor asset for its horizontal or vertical sibling before scheduling.

The server sends a bounded future horizon rather than streaming pixels. Players estimate server clock offset, download missing files, acknowledge cache readiness, and execute the same absolute timestamps. A temporary network interruption does not stop already-cached slots.

Generation projects, approvals, takes, and renditions are persistent records. GPU-heavy stages run serially and are separate from the show runner.
