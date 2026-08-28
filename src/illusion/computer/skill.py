"""Computer Use skill 定义模块。

以 plugin 方式注入内置 skill（名称 ``computer:computer-use``）。内容参考
Trae 官方 computer use 文档（mcp_Computer_Use 工具描述）与 cua-driver
实际工具语义编写：工具名/参数基于白名单内实际暴露的 MCP 工具
（前缀 ``mcp__computer_cua__``），强调"快照→操作→验证"工作流与
element_token 优先、set_value/type_text 选型、后台优先投递等关键规则。
"""

from __future__ import annotations

from illusion.computer.constants import SKILL_NAME
from illusion.skills.types import SkillDefinition

SKILL_CONTENT = """\
---
name: computer-use
description: >-
  操作本机桌面应用（Computer Use）：列出/启动应用，获取应用 UI 状态（无障碍树与截图），
  然后执行点击/滚动/拖拽/输入/按键/设值/菜单等操作，并基于最新状态验证结果。
  当用户要求你在本机真实应用中操作、驱动或自动化某个 GUI 任务时使用本 skill。
---

# Computer Use

通过 MCP 工具（前缀 `mcp__computer_cua__`）操作本机桌面应用，如人类使用键盘鼠标一样完成 GUI 任务。

## 核心不变式（最重要）

**每个动作必须被"观察"包围**：操作前先调用 `get_window_state` 获取最新 UI 树与截图，
操作后再获取一次状态验证结果。窗口移动/缩放/内容变化后，旧坐标与旧元素标识立即失效。

## 工具速览（共 18 个）

### 应用管理
| 工具 | 功能 |
| --- | --- |
| `list_apps` | 列出运行中与已安装的应用（含 pid / app_id） |
| `launch_app` | 按 app 启动应用，返回 pid |
| `kill_app` | 按 pid 强制结束进程 |

### 状态观察
| 工具 | 功能 |
| --- | --- |
| `get_window_state` | 获取指定应用窗口的 UI 树与截图（每个元素带 element_token / element_index） |
| `get_desktop_state` | 捕获整个屏幕（桌面级，无窗口上下文时使用） |
| `list_windows` | 列出目标应用的所有顶层窗口 |
| `bring_to_front` | 把目标窗口带到前台 |

### 鼠标操作
| 工具 | 功能 |
| --- | --- |
| `click` / `double_click` / `right_click` | 单击 / 双击 / 右键（优先 element_token，坐标仅 fallback） |
| `scroll` | 滚动（优先 element_token，可指定方向与页数） |
| `drag` | 按窗口截图像素坐标拖拽（from_x,from_y → to_x,to_y） |
| `move_cursor` | 移动光标到 (x, y) |

### 键盘操作
| 工具 | 功能 |
| --- | --- |
| `type_text` | 输入文本（优先 element_token 走 UIA；或先 click 输入框获得焦点再输入） |
| `press_key` | 单键（enter / tab / escape / 方向键 / 字母数字等） |
| `hotkey` | 组合键（如 ctrl+c、alt+tab） |

### 值设置 / 菜单
| 工具 | 功能 |
| --- | --- |
| `set_value` | 直接设置可编辑元素的值（编辑框首选，比 type_text 可靠） |
| `invoke_menu` | 沿应用菜单路径逐级调用菜单项 |

## 关键规则

1. **元素寻址用 `element_token` 或 `snapshot_id`**：`get_window_state` 结果末尾
   会附带 `snapshot_id=<值>`（如 `snapshot_id=s00000003`），UI 树中每个元素用
   `[N]` 下标标记。执行元素操作（click/type_text/set_value/press_key/scroll 等）时：
   - **推荐**：`element_token` = `"<snapshot_id>:<N>"`（如 `"s00000003:65"`），
     调用 `{"pid": ..., "element_token": "<snapshot_id>:<N>"}`（token 自带窗口信息）；
   - 或 `{"pid": ..., "window_id": ..., "element_index": N, "snapshot_id": "<snapshot_id>"}`。
   **禁止**：只传 `element_index`（bare index 会失败）；**禁止**把 `target`
   与 legacy `pid`/`window_id` 混用（会报 "target cannot be combined with legacy..."）。
   元素标识仅在最新快照内有效，快照被更新后 token 失效需重新快照。
2. **坐标操作是窗口局部像素**：`x`/`y` 使用 `get_window_state` 返回截图的
   窗口局部坐标（PNG 左上角为原点），需 `pid` + `window_id` 上下文；仅当目标
   是画布/视频/WebGL 等非 UIA 元素时才用坐标。
3. **每次操作后刷新状态**：操作后重新调用 `get_window_state(pid, window_id)`
   再继续下一步，旧快照会因 superseded 而失效。
4. **输入首选 `set_value`**：对文本框等可编辑元素，`set_value` 更可靠（UIA ValuePattern）；
   必须走按键输入时用 `type_text`，且先 `click` 输入框（或传 element_token）使其获得焦点。
5. **特殊键走 `press_key` / `hotkey`**：回车、方向键、Tab 等不是文本，用 `press_key`；
   组合键（如 ctrl+c）用 `hotkey`。
6. **后台优先**：默认 `delivery_mode="background"` 不抢焦点；仅当工具返回
   `background_unavailable` 错误时才用 `delivery_mode="foreground"` 重试，
   不要预先猜测。
7. **desktop 范围**：无 pid/window 上下文时可用 `scope="desktop"` 或
   `target={kind:"desktop", display_id:"primary"}` 操作屏幕绝对坐标。
8. **`bring_to_front` 会抢焦点**：仅用于特殊场景（如窗口被其他窗口完全遮挡且
   必须前置时）显式调用，不作为常规流程的一部分。

## 工作流程

**查看 → 操作 → 验证**，循环直至完成：

1. `list_apps` 找到目标应用（记录 pid），必要时 `launch_app` 启动；
   用 `list_windows(pid)`（或 `launch_app` 返回的 `windows` 数组）取得 `window_id`。
2. `get_window_state(pid, window_id)` 获取 UI 树与截图（每次操作前调用），
   **记录结果末尾的 `snapshot_id`**，并在 UI 树中找到目标元素的 `[N]` 下标。
3. 用 `element_token`（`"<snapshot_id>:<N>"`）执行操作
   （`click` / `set_value` / `type_text` / `press_key` / `invoke_menu` 等；
   `invoke_menu` 需 `pid` + `window_id` + `path`）。
4. `get_window_state(pid, window_id)` 验证结果；必要时用 `get_desktop_state`
   查看全屏确认。
"""


def build_computer_use_skill() -> SkillDefinition:
    """构建 Computer Use 内置 skill 定义（source="plugin"，随开关注册）。"""
    return SkillDefinition(
        name=SKILL_NAME,
        description="操作本机桌面应用（Computer Use）：列出/启动应用，获取 UI 状态并执行点击/输入/按键/设值等操作，基于最新状态验证结果。",
        content=SKILL_CONTENT,
        source="plugin",
        path="<bundled:computer>",
        skill_root="<bundled:computer>",
    )
