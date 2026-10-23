# Reddit launch drafts

## Recommended communities

- r/Halloween
- r/homeautomation
- r/projectors
- r/selfhosted
- r/AndroidTV

Read each community's current rules before posting. Use one focused post per community and avoid dropping identical promotional text everywhere.

## r/Halloween title

I built an open-source system that makes Halloween characters move between multiple projected windows

## r/Halloween body

I wanted my two window projectors to act like one haunted scene instead of independently looping videos, so I built **Projector**.

It runs one cheap Android TV stick per projector. A local server catalogs the projection packs you already own, caches the needed clips on each stick, and schedules characters across the windows on a shared clock. Shows can keep several windows active, pause unpredictably, rotate coherent collections, and continue movement from one surface to the next.

The phone controller handles corner calibration, rotation, masking, show building, schedules, live status, and app updates. It also has an optional local AI workflow for creating original characters and short encounters, but normal playback never depends on generation or Internet streaming.

The code and setup docs are here: **https://github.com/d8ahazard/projector**

Commercial projection videos are not included. You import folders/ZIPs for media you already own.

I would especially like feedback from people with more than two windows: what choreography controls would you want?

## r/selfhosted title

Projector: local-first synchronized video playback across Android TV sticks and projectors

## r/selfhosted body

I built a FastAPI/React/Android system for synchronized projection displays. The server compiles a show into concrete per-device assets, preflights storage, pushes a rolling schedule, and lets each Media3 player execute against a shared clock from local cache. It supports arbitrary surface counts, GPU corner pinning/masks, package imports, continuous shuffle schedulers, WebSocket health, signed LAN APK updates, and optional local ComfyUI generation.

Docker and native Windows-service setups are included: **https://github.com/d8ahazard/projector**

The security model is a trusted private LAN; it is not intended to be exposed directly to the Internet. Media and model weights are not included.

## r/projectors title

Open-source multi-projector window calibration and synchronized playback on Android TV sticks

## Posting notes

- Lead with a real 15–30 second video, not a UI screenshot.
- Put the GitHub link in the body when rules allow it; otherwise add it in a comment only if permitted.
- State clearly that commercial media is not redistributed.
- Mention actual projector models, throw distance, material, and ambient-light conditions once measured.
- Answer technical questions with measured results, including dropped frames and refresh-rate limitations.
