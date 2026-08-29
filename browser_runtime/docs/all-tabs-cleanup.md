# All-Tabs Cleanup Guidance

If the user asks to close every visible in-app browser tab in the current conversation, close controlled tabs found through
`browser.tabs.list()` and then claim and close released or user-owned tabs from `browser.user.openTabs()`.
Neither list alone represents all tabs owned by the current conversation. Tabs from other conversations are isolated and
must not be enumerated or closed.
