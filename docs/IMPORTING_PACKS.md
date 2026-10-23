# Importing projection packs

Open **Library → Halloween packs**, enter a friendly name, and choose either the package folder or its ZIP. Projector accepts MP4 video packages, stores originals unchanged under its work directory, then runs the same catalogue scan used for read-only source mounts.

## What Projector infers

Folder and filename rules suggest:

- collection and scene name
- character and action
- horizontal or vertical orientation
- window, wall, doorway, TV, prop, or hologram target
- black background, enclosure, or shadow appearance
- buffer files that should stay out of normal shuffles

Representative frames produce a visible-action thumbnail and contact sheet. Optional local motion analysis detects cuts, active intervals, entry/exit edges, and travel direction. Uncertain guesses remain reviewable in **Scene catalogue**.

Useful package structures look like this:

```text
Ghost Pack/
  Floating Wraith/
    Window - Horizontal/
      Wraith_Entrance_Win_H.mp4
    Window - Vertical/
      Wraith_Entrance_Win_V.mp4
  Buffers/
    Window_Black_Buffer.mp4
```

Exact publisher naming is not required. Clear collection, character, target, and orientation words improve automatic grouping.

## Shuffle behavior

Select one or more package cards and choose **Shuffle selected packs**. Projector prepares one canonical clip per distinct action plus a matching orientation sibling where available. The resulting continuous Program:

- cycles a character's clips before reuse
- rotates coherent collection families
- keeps several projectors active when compatible material exists
- adds short randomized gaps and per-surface safety buffers
- resolves concrete variants after calibration
- uses a new saved random seed on each start

Open the Program to adjust recycle ratio, gaps, concurrency, family groupings, movement chaining, transitions, and surface scope.

## Existing server folders

Set `PROJECTOR_SOURCE_ROOT` to a folder containing your packages. That root is indexed in place and can be mounted read-only. Uploaded folders and ZIPs use the writable `source-packs` folder under `PROJECTOR_WORK_DIR`.

Re-importing a package skips same-path files with the same size. Files with matching names are not collapsed until content hashing confirms they are identical.

## Rights and privacy

Only import media you are licensed to use. Source videos, thumbnails, catalogue metadata, and generated renditions remain local. Commercial media must not be committed to Git or attached to public releases.
