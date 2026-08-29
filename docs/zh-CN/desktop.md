# 桌面版

IllusionAgent 桌面版基于 Electron 封装 Web 端，内置 Python 与 Node.js 运行时，提供 Windows（NSIS 安装器）、macOS（dmg）与 Linux（AppImage）发行。

## 📦 下载与安装

### Windows

1. 下载 `IllusionAgent-Setup-<版本>.exe`。
2. 运行安装程序（可按需选择安装目录）。
3. 安装器自动创建开始菜单快捷方式与桌面快捷方式，点击 `IllusionAgent` 启动即可。

安装器在安装时以标准方式注册应用身份（AppUserModelID）——系统通知与任务栏图标来自安装器创建的快捷方式，应用运行时不做任何注册。配置写入 `%USERPROFILE%\.illusion\`。

### macOS

1. 下载 `IllusionAgent-<版本>-arm64.dmg`（Apple Silicon，Intel Mac 暂未提供）。
2. 打开 dmg，将 `IllusionAgent.app` 拖到"应用程序"。
3. 首次运行需绕过 Gatekeeper（见下节）。

配置写入 `~/.illusion/`。

### Linux

1. 下载 `IllusionAgent-<版本>-x86_64.AppImage`（x64，arm64 暂未提供）。
2. 添加可执行权限：`chmod +x IllusionAgent-<版本>-x86_64.AppImage`。
3. 双击或命令行运行。

配置写入 `~/.illusion/`。

## 🔓 macOS 绕过 Gatekeeper

由于 `.app` 未签名，macOS Gatekeeper 会阻止首次运行。两种方式任选其一：

**方式一：右键打开（推荐，图形界面）**

1. 在 Finder 中找到 `IllusionAgent.app`。
2. 按住 **Control** 键单击该 app（或右键单击）。
3. 选择"打开"。
4. 在弹窗中再次点击"打开"。

**方式二：终端命令（一次性放行）**

```bash
xattr -dr com.apple.quarantine /Applications/IllusionAgent.app
```

执行后该 app 不再被 Gatekeeper 拦截。仅对当前用户有效，不影响系统其他 app。

## 🪟 Windows 安装说明

- **标准安装**：NSIS 安装器，开始菜单/桌面快捷方式（携带 AppUserModelID）由安装器在安装与卸载时维护。
- **卸载**：通过"设置 → 应用"或安装器生成的卸载程序卸载；如需清理配置，删除 `%USERPROFILE%\.illusion\`。
- **SmartScreen 警告**：未签名 exe 首次运行时可能被拦截，按"更多信息"→"仍要运行"放行。
- **迁移**：配置在用户主目录，换机重装即可独立保留/导入配置。

## 🐧 Linux 说明

- **安装**：提供 `.deb`（Debian/Ubuntu 系 `sudo apt install ./IllusionAgent-<版本>-amd64.deb`）与 `.AppImage`（加可执行权限即可运行）两种产物。
- **AppImage 桌面集成**：如需在应用菜单显示，可使用 [AppImageLauncher](https://github.com/TheAssassin/AppImageLauncher) 或手动创建 `.desktop` 文件。
- **FUSE 依赖**：AppImage 运行需要 FUSE 支持。极少数最小化系统可能需安装：
  - Ubuntu/Debian：`sudo apt install libfuse2`
  - Fedora：`sudo dnf install fuse`
- **托盘/通知依赖**：Linux 托盘走 StatusNotifier/AppIndicator，通知走 freedesktop 通知守护进程；GNOME 桌面需启用 AppIndicator 扩展（KDE 默认支持）。
- **卸载**：deb 用 `sudo dpkg -r illusionagent`；AppImage 直接删除文件即可。配置在 `~/.illusion/`。

## 🐍 内置 Python / Node.js 运行时

桌面版内置一份独立的 Python 与 Node.js 运行时，位于应用资源目录内，**不污染系统 PATH**。

### 检测逻辑

| 运行时 | 优先级 | 说明 |
|---|---|---|
| 用户自有 Python / Node | 优先 | `PATH` 中可解析到符合版本要求的 `python` / `node` 时，使用用户环境 |
| 内置 Python / Node | 兜底 | 用户环境缺失或版本不达标时，使用内置运行时 |

### 暴露给 LLM 工具调用

- **用户已有环境**：仅用内置 Python 启动后端，不向用户暴露内置运行时。
- **用户无环境**：内置 Python / Node 的 bin 目录被加到后端进程的 `PATH` 前面，LLM 的工具调用（如 bash 工具执行 `python xxx.py` / `node xxx.js`）能直接使用内置运行时。

## 📌 托盘行为

| 操作 | 行为 |
|---|---|
| 点击窗口关闭按钮（×） | 隐藏窗口到系统托盘，应用继续运行 |
| 托盘图标单击 | 显示/隐藏主窗口 |
| 托盘菜单 → 退出 | 真正退出：关闭守护进程、释放端口、退出应用 |
| macOS Cmd+Q | 同"退出" |
| 重复启动 | 聚焦到现有窗口，不启动新实例 |

## 🔄 更新

### 自动更新（推荐）

桌面版在 Windows 与 Linux 内置自动更新（基于 electron-updater + GitHub Releases，点击式：发现新版本仅在顶栏亮出图标，点击图标才开始下载，不打扰使用）：

1. 应用每次启动后自动检查新版本；长期不关程序时每 12 小时复查一次兜底。
2. 发现新版本时顶栏最小化按钮附近出现**闪烁的下载图标**（安装按钮不闪烁）；点击图标开始下载，下载中显示进度环。
3. 下载完成后图标变为**圆圈对勾的安装就绪态**，与下载图标明显区分：点击图标才退出应用并进入**显式安装**（显示完整安装进度，完成后自动重启应用）；正常退出应用不会触发安装。
4. 托盘隐藏期间下载完成会有系统通知提醒。

更新包经 SHA512 校验保证完整性；更新在现有安装上原地执行，配置目录 `~/.illusion/` 跨版本保留。

> - macOS 因应用未签名，Squirrel.Mac 不支持自动更新，**需手动更新**（下载新 dmg 覆盖 `/Applications`）；签名后此限制解除。
> - Linux 仅 AppImage 支持自动更新；deb 暂不支持，请手动重新安装。
> - 自动更新依赖 GitHub Releases 的网络可达性；检查或下载失败时顶栏图标不出现，应用会自动退避重试数次，仍失败则等待下次周期复查，也可从 [Releases 页面](https://github.com/YunTaiHua/illusion-agent/releases) 手动下载新版安装包覆盖安装。

### 手动更新

在现有安装上重新运行新版安装包（Windows 为 `IllusionAgent-Setup-<版本>.exe`，macOS 为挂载新 dmg 覆盖 `/Applications`，Linux 为替换 AppImage 文件）即可原地更新。

## ⚠️ 注意事项

- **未签名**：三端均无代码签名，Windows 触发 SmartScreen，macOS 触发 Gatekeeper，Linux 无影响。Windows/Linux 的自动更新不受此影响：更新以静默方式在应用内触发，不会出现额外的系统警告；macOS 因未签名无法自动更新（见上文）。
- **首次启动较慢**：内置运行时首次初始化需数秒。
