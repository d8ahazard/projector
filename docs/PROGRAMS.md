# Programs and smart scheduling

A Program is a creative plan independent of physical devices. It targets named surfaces and resolves media variants only when compiled for the current calibrated layout.

## Cues and encounters

Ordinary cues can play together, choose a different clip per surface, sequence across the layout, span the house, chase, ping-pong, or select a random surface. Encounters place explicit clips on named surface tracks for authored entrances, actions, exits, and handoffs.

## Marathon scheduler

Package shuffle creates a continuous marathon scheduler. It maintains shuffled character and clip bags, a configurable recycle ratio, family rotation, per-surface occupancy, and simultaneous activity. A fresh runtime seed makes each run different; the saved seed permits exact restart recovery.

Smart movement mode prefers:

1. the same character continuing through the expected edge
2. an explicit narrative relationship
3. another character from the same family
4. a related stationary or ambient action

Horizontal mirroring is allowed only for clips marked mirror-safe. Vertical flipping is never used for choreography.

## Playback

Before playback, the compiler freezes concrete asset IDs, reports cache requirements, and sends a rolling absolute-time schedule to each online player. Players cache media locally. Live generation and computer vision are not part of playback.
