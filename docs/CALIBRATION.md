# Calibration

Each player stores output geometry separately from the creative Program. This lets the same show move from a test wall to a window without rewriting cues.

Choose a player under **Calibrate** and press **Recalibrate**. The player shows a marker board while the controller captures a photo. If the target boundary is detected, Projector applies a first-pass four-corner fit. Refine it with large corner arrow controls or exact percentages while a persistent outline overlays live video.

Calibration profiles preserve:

- four-corner GPU homography
- polygon mask and feathering
- horizontal/rear-projection mirroring
- output orientation and dimensions
- brightness, gamma, and RGB gain
- assigned surface

A projector mounted on its side selects portrait variants. Upside-down mounting uses device rotation/calibration and never makes the content itself upside down. Turn off projector-side keystone when possible so there is one geometric correction stage.

Calibration runs in the player shader and does not re-encode video. Optical alignment, projector focus, overscan, refresh rate, and rear-projection material still require physical verification.
