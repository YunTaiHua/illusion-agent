"""Computer Use 功能常量定义。

集中管理 computer use 内置插件、MCP 服务器、skill 与提示文案的命名常量，
避免魔法字符串散落在各模块中。
"""

# 内置插件名称（以 plugin 方式注入 mcp 工具扩展与 skill 时使用）
PLUGIN_NAME = "computer"

# 插件注入的 MCP 服务器名（在 load_mcp_server_configs 中最终键为 "computer:cua"）
MCP_SERVER_NAME = "cua"

# Computer Use MCP 服务器完整键（"插件名:服务器名" 格式）
COMPUTER_MCP_SERVER = f"{PLUGIN_NAME}:{MCP_SERVER_NAME}"

# 内置 skill 名称（插件命名空间格式，LLM 通过 skill 工具按此名调用）
SKILL_NAME = f"{PLUGIN_NAME}:computer-use"

# 每个 computer use 工具结果末尾追加的提示，告知 LLM 存在 skill 指导。
# 使用英文以匹配 LLM 工具结果的语义（用户可见文案走 i18n）。
# 保持简短以控制上下文开销（该提示在每个 computer 工具结果后追加）。
SKILL_HINT = (
    f"Note: these tools follow the '{SKILL_NAME}' skill — call "
    f"skill(name=\"{SKILL_NAME}\") if not loaded yet (snapshot-before-action, "
    "address elements by element_token=snapshot_id:index, background-first delivery)."
)

# cua-driver MCP 服务器对外暴露的工具白名单（核心 computer use 工具集）。
# cua-driver 自带 50+ 工具（浏览器/录制/生命周期/诊断等），全部暴露会撑爆
# 上下文并干扰模型选择；此处仅暴露桌面交互所需的核心工具。
# 说明：cua-driver 的 UI 状态工具名为 get_window_state（等价 Trae 的
# get_app_state），菜单语义操作用 invoke_menu（等价 perform_action）。
COMPUTER_TOOL_ALLOWLIST: frozenset[str] = frozenset({
    # 应用管理
    "list_apps",
    "launch_app",
    # 状态观察
    "get_window_state",
    "get_desktop_state",
    "list_windows",
    "bring_to_front",
    # 鼠标操作
    "click",
    "double_click",
    "right_click",
    "scroll",
    "drag",
    "move_cursor",
    # 键盘操作
    "type_text",
    "press_key",
    "hotkey",
    # 值设置 / 菜单
    "set_value",
    "invoke_menu",
    # 进程清理
    "kill_app",
})
