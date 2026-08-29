# Playwright locator discipline

`tab.playwright` is a deliberately limited Playwright-like surface. Call only members present in the effective API manifest. `playwright.evaluate(...)` and `locator.evaluate(...)` execute JavaScript in the page context; use them when page-side computation or interaction is required.

`getByRole(..., { name })` accepts a plain string or `RegExp`, including a `RegExp` created inside the current
Node REPL VM. Prefer the matcher form that directly reflects the accessible-name fact proven by the latest snapshot.

## Snapshot is the locator source of truth

- Keep and reuse the latest relevant `tab.playwright.domSnapshot()` until navigation or a UI change makes it stale.
- Construct locators only from role, accessible name, text, placeholder, `data-*`, `href`, or other attributes that actually appear in that snapshot.
- Never guess a label, accessible name, placeholder, selector, URL pattern, or element type. A guessed locator is not an exploratory probe.
- A rotating search suggestion is not a stable placeholder contract. If the snapshot shows one unnamed `textbox`, prefer `getByRole("textbox")` plus `count()` instead of inventing `getByPlaceholder("Search")`.
- Do not dump `body` text or loop over a broad locator to discover the page. Use one bounded snapshot, then narrow to the relevant section or candidate.
- If the latest snapshot already contains the target, use its facts directly. Do not call `evaluate()` to rediscover related elements, enumerate inputs, dump HTML, walk the DOM, or probe a guessed selector.
- A snapshot-proven heading or visible text does not need a `link` or `button` role to be clicked. Do not replace a snapshot-proven `heading` with a guessed `link` role.
- When the user has authorized navigation and the actual heading/text target resolves uniquely, click that target directly. A DOM click can bubble to a JavaScript handler on an ancestor card even when the target itself has no interactive ARIA role.

## Evaluate page scripts

`playwright.evaluate(...)` and locator `evaluate(...)` run the supplied expression or function in the page context and may read or change page state. Use the high-level locator and action methods when they express the intent more clearly; use evaluate for page-side logic that needs direct JavaScript access.

## Required interaction recipe

Before click, fill, press, select, check, or another state-changing locator action:

1. Reuse the latest relevant snapshot, or take a fresh snapshot when its locator facts are stale or incomplete.
2. Build the most stable locator supported by those facts.
3. If uniqueness is not self-evident, call `count()` once and retain the result.
4. Continue only when the locator resolves to exactly one intended element.
5. Perform the action once, then collect only the targeted state or fresh snapshot needed for the next decision. Use at most one state-changing action per observation cycle.

If `count() === 0`, do not perform the action and do not wait on that locator. Take a fresh snapshot and rebuild it. If the count is greater than one, scope to a stable container or stronger attribute; do not use `first()`, `last()`, or `nth()` as an ambiguity shortcut.

## Locator preference

Prefer durable facts in this order:

1. stable test id or `data-*` attribute;
2. stable exact `href` or similarly durable attribute;
3. scoped semantic role plus a snapshot-proven accessible name;
4. scoped visible text;
5. scoped CSS selector copied from known DOM facts;
6. scoped DOM/CUA fallback when the Playwright locator surface cannot identify one stable target.

Generic names such as `Search`, `Menu`, `Close`, or repeated result titles are ambiguous by default. Scope them before acting.

## Timeout and recovery

Routine locator, URL/load-state wait, and evaluate operations use Codex's short failure budget: 3000ms by default and at most 3000ms even when a larger timeout is requested. Download event waiting may use up to 120000ms. Explicit `tab.playwright.waitForTimeout(ms)` is a separate fixed delay and should remain exceptional.

After every successful `tab.goto(url)`, explicitly call `await tab.playwright.waitForLoadState({ state: "domcontentloaded" })` before the first title, URL, or DOM observation. Keep this step in the model-visible trajectory even when `goto()` has already settled the backend navigation; it confirms the expected load state without changing the 3000ms runtime cap.

`waitForLoadState({ state: "networkidle" })` is not supported by this Codex-compatible runtime. Wait for `load`/`domcontentloaded` or a concrete page state instead.

`expectNavigation(action)` follows the current Codex client: it starts a load-state waiter before the action, but an
already-loaded page can satisfy that waiter. Pass `{ url: expectedUrl }` when the action must prove a new navigation.

An unchanged source-tab URL does not prove the click failed. Judge an action by whether its expected effect appeared,
not by whether `browser.tabs.list()` is non-empty. An existing source tab or unrelated controlled tab is not an action
effect. Match the intended result by a verified source-page state or tab URL/title.

When an action may open a popup/new tab and the source tab does not show the expected effect, read
`browser.tabs.list()` and `browser.user.openTabs()` unconditionally in the same observation cell:

```js
const [controlledTabs, userTabs] = await Promise.all([
  browser.tabs.list(),
  browser.user.openTabs(),
]);
({ controlledTabs, userTabs });
```

Return `{ controlledTabs, userTabs }` as that cell's final result so the model makes one decision from both lists. Do
not return the controlled list first or decide whether to query user tabs from its contents. In the next cell, activate
or claim the page matching the expected URL/title. If the source page and combined tab observation lack the expected
effect, take a fresh snapshot and choose a new evidence-backed plan instead of replaying the prior click.

After a timeout, strict-mode failure, or selector parse failure:

- do not retry the same locator;
- take a fresh `domSnapshot()`;
- confirm that the target still exists;
- rebuild from a tighter scope or a more stable snapshot-proven attribute.

If two attempts fail for the same target, stop increasing role/text complexity and deliberately switch to the strongest stable attribute or a scoped DOM/CUA path.
