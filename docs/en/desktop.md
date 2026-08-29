# Desktop Edition

IllusionAgent desktop edition wraps the web frontend in Electron, bundles Python and Node.js runtimes, and distributes installers for Windows (NSIS), dmg for macOS, and AppImage for Linux.

## 📦 Download & Install

### Windows

1. Download `IllusionAgent-Setup-<version>.exe`.
2. Run the installer (choose an installation directory if desired).
3. The installer creates the Start Menu / desktop shortcuts automatically. Launch `IllusionAgent` from them.

The installer registers the application identity (AppUserModelID) as part of the standard install — system notifications and the taskbar icon come from the shortcut created by the installer, with no runtime registration in-app. Config is written to `%USERPROFILE%\.illusion\`.

### macOS

1. Download `IllusionAgent-<version>-arm64.dmg` (Apple Silicon; Intel Mac not provided).
2. Open the dmg and drag `IllusionAgent.app` to Applications.
3. First launch requires bypassing Gatekeeper (see below).

Config is written to `~/.illusion/`.

### Linux

1. Download `IllusionAgent-<version>-x86_64.AppImage` (x64; arm64 not provided).
2. Make it executable: `chmod +x IllusionAgent-<version>-x86_64.AppImage`.
3. Double-click or run from a terminal.

Config is written to `~/.illusion/`.

## 🔓 Bypassing macOS Gatekeeper

Because the `.app` is unsigned, Gatekeeper blocks the first launch. Either method works:

**Method 1: Right-click open (recommended, GUI)**

1. Locate `IllusionAgent.app` in Finder.
2. Control-click (or right-click) the app.
3. Choose "Open".
4. Click "Open" again in the dialog.

**Method 2: Terminal command (one-time)**

```bash
xattr -dr com.apple.quarantine /Applications/IllusionAgent.app
```

After running, Gatekeeper no longer blocks this app. Applies to the current user only; does not affect other apps.

## 🪟 Windows Installer Notes

* **Standard install**: NSIS installer; Start Menu / desktop shortcuts (carrying the AppUserModelID) are maintained by the installer on install and uninstall.

* **Uninstall**: use Windows Settings → Apps, or the uninstaller created by the installer. To also clear config, delete `%USERPROFILE%\.illusion\`.

* **SmartScreen warning**: unsigned exe may be blocked on first launch — click "More info" → "Run anyway".

* **Migration**: config lives in the home directory, so reinstalling on another machine keeps/imports config independently.

## 🐧 Linux Notes

- **Install**: both `.deb` (Debian/Ubuntu: `sudo apt install ./IllusionAgent-<version>-amd64.deb`) and `.AppImage` (add executable bit to run) are provided.
- **AppImage desktop integration**: to show in the app menu, use [AppImageLauncher](https://github.com/TheAssassin/AppImageLauncher) or create a `.desktop` file manually.
- **FUSE dependency**: AppImage requires FUSE. On minimal systems you may need:
  - Ubuntu/Debian: `sudo apt install libfuse2`
  - Fedora: `sudo dnf install fuse`
- **Tray / notification dependencies**: Linux tray uses StatusNotifier/AppIndicator and notifications go through the freedesktop notification daemon; GNOME needs the AppIndicator extension (KDE supports it out of the box).
- **Uninstall**: `sudo dpkg -r illusionagent` for deb; delete the AppImage file for portable. Config lives in `~/.illusion/`.

## 🐍 Bundled Python / Node.js Runtimes

The desktop edition bundles an independent Python and Node.js runtime inside the app resources directory and **does not pollute the system PATH**.

### Detection Logic

| Runtime                  | Priority | Description                                                                   |
| ------------------------ | -------- | ----------------------------------------------------------------------------- |
| User's own Python / Node | First    | Used when `PATH` resolves a `python` / `node` meeting the version requirement |
| Bundled Python / Node    | Fallback | Used when the user's environment is missing or below the required version     |

### Exposure to LLM Tool Calls

* **User has their own environment**: the bundled Python only starts the backend; the bundled runtimes are not exposed to the user.

* **User has no environment**: the bundled Python / Node bin directories are prepended to the backend process's `PATH`, so LLM tool calls (e.g. bash tool running `python xxx.py` / `node xxx.js`) can use the bundled runtimes directly.

## 📌 Tray Behavior

| Action                        | Behavior                                               |
| ----------------------------- | ------------------------------------------------------ |
| Click window close button (×) | Hide window to system tray; app keeps running          |
| Click tray icon               | Show/hide main window                                  |
| Tray menu → Quit              | Truly exit: stop daemons, release port, quit app       |
| macOS Cmd+Q                   | Same as "Quit"                                         |
| Launch again while running    | Focus the existing window; do not start a new instance |

## 🔄 Updates

### Automatic updates (recommended)

The desktop edition ships with built-in auto-update on Windows and Linux (electron-updater + GitHub Releases, click-first: a title-bar icon appears when a new version is found, and the download starts only after you click it):

1. The app checks for updates after every startup; for long-running sessions it re-checks every 12 hours as a fallback.
2. When a new version is found, an update icon appears next to the minimize button in the title bar; click it to start the download, with a progress ring shown while downloading.
3. Once downloaded, the icon turns into a ready state: click it to restart and install immediately, or simply keep working — the update is installed automatically the next time you quit the app (a system notification reminds you while the window is hidden in the tray).

Update packages are verified via SHA512 checksums. Updates run in place over the existing installation, and the `~/.illusion/` config directory is preserved across versions.

> - macOS cannot auto-update while the app is unsigned (Squirrel.Mac requires a signed app) — **update manually** by downloading the new dmg and overwriting `/Applications`; this limitation goes away once signing is in place.
> - On Linux only AppImage supports auto-update; deb is not supported — reinstall manually.
> - Auto-update requires access to GitHub Releases. If a check or download fails, the title bar icon simply stays hidden while the app retries with a short backoff before falling back to the next periodic re-check; you can also grab the new installer from the [Releases page](https://github.com/YunTaiHua/illusion-agent/releases) and install it over the current one.

### Manual updates

Run the new installer over the current installation (`IllusionAgent-Setup-<version>.exe` on Windows, mount the new dmg and overwrite `/Applications` on macOS, replace the AppImage file on Linux) to update in place.

## ⚠️ Notes

* **Unsigned**: no code signing on any platform — Windows triggers SmartScreen, macOS triggers Gatekeeper, Linux is unaffected. Auto-update on Windows/Linux is unaffected: updates are triggered from within the app (silent install) and produce no extra system warnings; macOS cannot auto-update while unsigned (see above).

* **Slow first launch**: the bundled runtime takes a few seconds to initialize on first run.

