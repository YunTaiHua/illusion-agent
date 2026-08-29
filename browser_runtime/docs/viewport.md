# Browser Capability: viewport

Use an explicit viewport only for responsive or device-size testing. Otherwise keep the normal IAB viewport.

```js
await tab.setViewportSize({ width: 1280, height: 720 });
nodeRepl.write(JSON.stringify(tab.viewportSize()));
```

`setViewportSize()` automatically opens the IAB responsive canvas. Its width and height are CSS pixels,
and responsive mode uses DPR 1 so a viewport screenshot has matching PNG pixel dimensions. Exiting
responsive mode in the UI clears the override and restores the host's natural DPR.
