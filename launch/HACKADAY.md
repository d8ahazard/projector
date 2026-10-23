# Hackaday.io project draft

## Title

Projector: synchronized Halloween windows with cheap Android TV sticks

## One-line description

A local-first controller that catalogs projection-video packs and choreographs characters across any number of calibrated windows, walls, and props.

## Story

I wanted the house to feel like one scene instead of two TVs independently looping Halloween clips. A ghost should be able to leave one window, reappear in the next, vanish for a while, and come back somewhere unexpected. The hardware is deliberately ordinary: one inexpensive Android TV stick and one projector per surface, plus a Windows/Linux server on the same LAN.

Projector indexes existing projection packs in place or imports a folder/ZIP. It identifies collections, characters, orientations, background styles, buffers, and likely motion. The scheduler builds hours-long shows from short actions while keeping related families together and several projectors active. Each player caches a rolling media window and follows an absolute-time schedule, so the server is not trying to stream multiple live video feeds.

Calibration is also centralized. The phone shows a marker board, captures the surface, and lets you nudge each corner while the Android player applies the homography in its GPU shader. The same creative Program can move from a wall test to side-mounted window projectors because concrete portrait/landscape variants are chosen after calibration.

There is an optional local Creature Studio for concept → approved character → approved starting images → short animation with sound → published encounter. Generation is outside the runtime playback path.

The code, Docker setup, Windows service scripts, Android player/controller, scheduler, and documentation are on GitHub. Commercial projection videos are not included; users import media they are licensed to use.

## Suggested project logs

1. Why cached absolute-time playback beats LAN video streaming
2. Turning folders of H/V variants into a useful scene catalogue
3. GPU corner pinning and side-mounted projectors
4. Movement analysis and multi-window handoffs
5. Making a stock Google TV stick behave like an appliance without root

## Media

- `docs/images/live-mobile.png`
- `docs/images/packs-mobile.png`
- `docs/images/calibration-mobile.png`
- `docs/images/generated-window-crossing.jpg`
- a short phone video of the two real windows running concurrently
