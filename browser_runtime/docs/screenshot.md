# Screenshots

This is lookup-only guidance. Do not use it for ordinary navigation, reading, search, or form interaction when a DOM snapshot answers the question.

Capture a screenshot only when the user explicitly requests one, visual layout/rendering/image content must be judged, or the required target is absent from the DOM snapshot. Do not request a snapshot and screenshot together by default.

`await tab.screenshot(opts?)` returns PNG bytes as `Uint8Array` internally. Those bytes are not a model-visible screenshot and must never be returned as the JS result.

Every screenshot call must pass the bytes to `nodeRepl.emitImage` in the same JS cell so the tool returns a standard image content block:

```js
nodeRepl.emitImage(await tab.screenshot());
```

Never use `await tab.screenshot()` as the final expression.

Supported screenshot options:

- `{ fullPage: true }` captures the whole page.
- `{ clip: { x, y, width, height } }` captures a viewport region.

If a screenshot times out, do not immediately issue the same screenshot again. The underlying Chromium
capture may still be completing; wait before retrying, or reopen the tab if the explicit in-flight error
does not clear.
