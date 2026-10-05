# Viewport

- Default viewport: 1280x720 (agent sessions).
- Bounds: width 320-3840, height 320-2160 (`browser_resize` clamps silently).
- The right-panel toolbar also resizes the viewport; the user's manual resize and agent `browser_resize` share one source of truth.
- Resize only when the task needs it (responsive checks, element visibility). Never resize to escape a failed state; restore the previous size when a responsive check completes.
