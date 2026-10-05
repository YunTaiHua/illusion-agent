# Screenshots

- `browser_screenshot` returns a JPEG image directly into the conversation; the same frame is shown in the right-side browser panel and in the tool card.
- Default capture is the current viewport; `full_page=true` captures the scrollable page.
- **Policy**: screenshot only when vision matters — visual verification the user asked for, layout/styling checks, or aiming coordinate clicks at canvas/custom widgets. Otherwise `browser_snapshot` is cheaper and more precise.
- Do not request a snapshot and a screenshot around the same step by default.
- For transient states (toasts, loading), capture before → act → wait → capture after, back to back.
- Screenshots carry no file path; the returned images are the evidence. Do not invent artifact paths in reports.
