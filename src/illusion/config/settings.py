"""
Settings 模型和加载逻辑模块
===========================

本模块提供 IllusionAgent 的设置管理功能，包括：
- Settings: 主设置模型（env_N 分组格式）
- EnvConfig: 环境/提供商组配置
- 各种设置加载和保存函数

设置解析优先级（从高到低）：
    1. CLI 参数
    2. 配置文件（~/.illusion/settings.json）
    3. 默认值

类说明：
    - Settings: 主设置模型，使用 env_N 分组管理多个环境配置
    - EnvConfig: 单个环境的配置（api_format, base_url, api_key, model_N 等）
    - PermissionSettings: 权限模式配置
    - MemorySettings: 记忆系统配置
    - TitleSettings: 会话自动标题配置
    - SandboxSettings: 沙箱运行时配置

使用示例：
    >>> from illusion.config.settings import load_settings, Settings
    >>> settings = load_settings()
    >>> print(f"当前模型: {settings.active_model_name}")
"""

from __future__ import annotations

import json  # 导入 json 模块用于配置文件读写
from dataclasses import dataclass  # 导入 dataclass 用于创建不可变数据结构
from pathlib import Path  # 导入 Path 用于路径处理
from typing import Any  # 导入 Any 类型用于泛型

from pydantic import BaseModel, Field, field_validator  # 导入 pydantic 模型基类和验证器

from illusion.mcp.types import McpServerConfig, _normalize_server_config_type  # 导入 MCP 服务器配置
from illusion.permissions.modes import PermissionMode  # 导入权限模式
from illusion.utils.atomic_write import atomic_write_text  # 导入原子写入工具


class PathRuleConfig(BaseModel):
    """路径权限规则配置
    
    使用 glob 模式定义路径级别的权限规则。
    
    Attributes:
        pattern: glob 模式字符串
        allow: 是否允许访问，默认为 True
    """

    pattern: str  # glob 模式，用于匹配路径
    allow: bool = True  # 默认为允许访问


class PermissionSettings(BaseModel):
    """权限模式配置
    
    配置系统的权限控制和行为限制。
    
    Attributes:
        mode: 权限模式
        allowed_tools: 允许的工具列表
        denied_tools: 拒绝的工具列表
        allowed_shell_commands: 命令级白名单（前缀匹配，命中直接放行，即使高危；bash/powershell 通用）
        path_rules: 路径规则列表
        denied_commands: 拒绝的命令列表
        auto_review: full_auto 模式下高危操作与沙箱拦截（如工作区外读写）
            是否由 LLM 自动审核放行
            （关闭时走现有人工确认流程；开启时由审核模型自行裁决，yolo/plan/default 不受影响）
        review_model: LLM 审核使用的模型（env_N.model_M 格式），None 继承当前会话模型
    """

    mode: PermissionMode = PermissionMode.DEFAULT  # 权限模式，默认为默认模式
    allowed_tools: list[str] = Field(default_factory=list)  # 允许的工具列表
    denied_tools: list[str] = Field(default_factory=list)  # 拒绝的工具列表
    allowed_shell_commands: list[str] = Field(default_factory=list)  # 命令级白名单（前缀匹配，命中直接放行，即使高危；bash/powershell 通用）
    path_rules: list[PathRuleConfig] = Field(default_factory=list)  # 路径权限规则
    denied_commands: list[str] = Field(default_factory=list)  # 拒绝的命令列表
    # 默认关闭 LLM 自动审核：full_auto 模式沿用现有人工确认流程
    auto_review: bool = False
    review_model: str | None = None  # 审核模型（env_N.model_M），None 继承当前


class MemorySettings(BaseModel):
    """记忆系统配置

    配置 AI 记忆系统的行为和限制。

    Attributes:
        enabled: 是否启用记忆功能
        auto_extract: 是否允许后台 LLM 自动提取/整合记忆（默认关闭，仅手动记录；
            开启后每轮结束后台子代理自动分析对话并保存记忆）
        extract_model: 提取子代理使用的模型（env_N.model_M 格式），None 继承当前
        dream_model: 整合子代理使用的模型（env_N.model_M 格式），None 继承当前
        directory: 自定义记忆目录（绝对路径或 ~/ 开头），None 使用默认目录
        max_files: 最大注入的相关记忆文件数
        max_entrypoint_lines: 入口文件 MEMORY.md 最大行数
        max_entrypoint_bytes: 入口文件 MEMORY.md 最大字节数
        extract_interval: 后台提取触发间隔（轮数）
        dream_min_hours: Auto Dream 整合最小间隔（小时）
        dream_min_sessions: Auto Dream 整合最小会话数
    """

    enabled: bool = True  # 默认启用记忆功能
    # 默认关闭后台自动提取/整合：自动提取开销不小（每轮一次子代理 LLM 调用），
    # 用户未显式开启时仅手动记录（用户要求时才由主对话 LLM 直接写记忆文件）
    auto_extract: bool = False
    extract_model: str | None = None  # 提取子代理模型（env_N.model_M），None 继承当前
    dream_model: str | None = None  # 整合子代理模型（env_N.model_M），None 继承当前
    directory: str | None = None  # 自定义记忆目录（None 使用默认）
    max_files: int = 5  # 默认最多注入 5 个相关记忆文件
    max_entrypoint_lines: int = 200  # 默认入口文件最多 200 行
    max_entrypoint_bytes: int = 25_000  # 默认入口文件最多 25000 字节
    extract_interval: int = 1  # 默认每 1 轮触发后台提取
    dream_min_hours: int = 24  # Auto Dream 最小间隔 24 小时
    dream_min_sessions: int = 5  # Auto Dream 最小会话数 5


class TitleSettings(BaseModel):
    """会话自动标题配置

    与记忆提取/整合类似，回合结束后在后台运行一个轻量子代理，
    根据对话内容为会话生成一个简洁标题，写入 meta.json 的 title 字段。
    后台执行不阻塞主对话。

    Attributes:
        enabled: 是否启用自动标题（默认关闭）
        model: 标题生成子代理使用的模型（env_N.model_M 格式），
            None 继承当前会话模型
    """

    enabled: bool = False  # 默认关闭自动标题
    model: str | None = None  # 标题生成模型（env_N.model_M），None 继承当前


class GoalSettings(BaseModel):
    """Goal 子系统配置

    Attributes:
        enabled: 是否启用 goal 功能（关闭时不注册 goal 工具/命令）
        default_max_goal_rounds: 创建 goal 时的默认轮次上限（默认 256）
        blocked_after_consecutive_rounds: 模型自报 blocked 前的最小轮次门槛
            （默认 3）
        verification_enabled: 完成声明是否经对抗性验证子代理复核
        verification_max_attempts: 验证拒绝累计上限，达到后自动置为 blocked
            （默认 10）
    """

    enabled: bool = True
    default_max_goal_rounds: int = 256
    blocked_after_consecutive_rounds: int = 3
    verification_enabled: bool = True
    verification_max_attempts: int = 10


class SandboxNetworkSettings(BaseModel):
    """沙箱网络限制配置
    
    传递给沙箱运行时的操作系统级网络限制配置。
    
    Attributes:
        allowed_domains: 允许访问的域名列表（支持 *.example.com 通配符）
        denied_domains: 拒绝访问的域名列表
        allow_unix_sockets: macOS: 允许的 Unix socket 路径
        allow_all_unix_sockets: 允许所有 Unix socket（禁用 seccomp 阻断）
        allow_local_binding: 允许绑定本地端口
        http_proxy_port: 使用外部 HTTP 代理端口
        socks_proxy_port: 使用外部 SOCKS 代理端口
    """

    allowed_domains: list[str] = Field(default_factory=list)  # 允许的域名
    denied_domains: list[str] = Field(default_factory=list)  # 拒绝的域名
    allow_unix_sockets: list[str] = Field(default_factory=list)  # macOS: 允许的 Unix socket 路径
    allow_all_unix_sockets: bool = False  # 允许所有 Unix socket
    allow_local_binding: bool = False  # 允许绑定本地端口
    http_proxy_port: int | None = None  # 外部 HTTP 代理端口
    socks_proxy_port: int | None = None  # 外部 SOCKS 代理端口

    @field_validator("allowed_domains", "denied_domains")
    @classmethod
    def validate_domain_patterns(cls, v: list[str]) -> list[str]:
        """验证域名模式格式，防止过于宽泛的通配符"""
        for pattern in v:
            if pattern == "localhost":
                continue
            if "://" in pattern or "/" in pattern or ":" in pattern:
                raise ValueError(f"域名不能包含 ://、/ 或 :，收到: {pattern}")
            if pattern == "*":
                raise ValueError("不允许使用 * 通配符")
            if pattern.startswith("*."):
                suffix = pattern[2:]
                parts = suffix.split(".")
                if len(parts) < 2:
                    raise ValueError(f"通配符域名需要至少 2 个点分段: {pattern}")
            elif "*" not in pattern:
                if "." not in pattern:
                    raise ValueError(f"域名必须包含至少一个点: {pattern}")
                if pattern.startswith(".") or pattern.endswith("."):
                    raise ValueError(f"域名不能以点开头或结尾: {pattern}")
        return v


class SandboxFilesystemSettings(BaseModel):
    """沙箱文件系统限制配置
    
    传递给沙箱运行时的操作系统级文件系统限制配置。
    
    Attributes:
        allow_read: 允许读取的路径列表（可豁免 deny_read 区域）
        deny_read: 拒绝读取的路径列表
        allow_write: 允许写入的路径列表
        deny_write: 拒绝写入的路径列表
    """

    allow_read: list[str] = Field(default_factory=list)  # 允许读取的路径（可豁免 deny_read）
    deny_read: list[str] = Field(default_factory=list)  # 拒绝读取的路径
    allow_write: list[str] = Field(default_factory=lambda: ["."])  # 默认允许写入当前目录
    deny_write: list[str] = Field(default_factory=list)  # 拒绝写入的路径


class SandboxRipgrepSettings(BaseModel):
    """沙箱内置 ripgrep 配置

    自定义沙箱内使用的 ripgrep 命令与参数。

    Attributes:
        command: ripgrep 可执行命令路径
        args: 追加的参数列表
    """

    command: str = "rg"  # ripgrep 命令
    args: list[str] = Field(default_factory=list)  # 追加参数


class SandboxSettings(BaseModel):
    """沙箱运行时集成配置

    配置与沙箱运行时的集成选项。

    Attributes:
        enabled_platforms: 启用的平台列表
        excluded_commands: 排除沙箱的命令模式列表
        network: 网络限制配置
        filesystem: 文件系统限制配置
        ignore_violations: 命令模式 → 忽略的路径列表
        enable_weaker_nested_sandbox: Docker 环境跳过 --proc /proc
        enable_weaker_network_isolation: macOS 允许访问 trustd（降低网络隔离）
        mandatory_deny_search_depth: 搜索危险文件的最大目录深度
        allow_git_config: 允许写入 .git/config
        ripgrep: 沙箱内置 ripgrep 配置
    """

    enabled_platforms: list[str] = Field(default_factory=list)  # 启用的平台
    excluded_commands: list[str] = Field(default_factory=list)  # 排除沙箱的命令模式
    network: SandboxNetworkSettings = Field(default_factory=SandboxNetworkSettings)  # 网络配置
    filesystem: SandboxFilesystemSettings = Field(
        default_factory=SandboxFilesystemSettings
    )  # 文件系统配置
    ignore_violations: dict[str, list[str]] = Field(default_factory=dict)  # 命令 → 忽略路径
    enable_weaker_nested_sandbox: bool = False  # Docker: 跳过 --proc /proc
    enable_weaker_network_isolation: bool = False  # macOS: 允许 trustd（降低网络隔离）
    mandatory_deny_search_depth: int = Field(default=3, ge=1, le=10)  # 搜索深度
    allow_git_config: bool = False  # 允许写入 .git/config
    ripgrep: SandboxRipgrepSettings | None = None  # 内置 ripgrep 配置


@dataclass(frozen=True)
class ResolvedAuth:
    """规范化的认证材料

    用于构造 API 客户端的标准化认证信息。

    Attributes:
        auth_kind: 认证类型
        value: 认证值
        source: 认证来源
        state: 状态（默认为 "configured"）
    """

    auth_kind: str  # 认证类型（api_key、oauth 等）
    value: str  # 认证值
    source: str  # 来源描述
    state: str = "configured"  # 配置状态


class EnvConfig(BaseModel):
    """环境/提供商组配置"""

    api_format: str  # "anthropic" / "openai"
    base_url: str | None = None
    api_key: str = ""
    auth_token: str = ""  # Bearer Token 认证（用于 LongCat 等使用 Authorization: Bearer 的提供商）

    model_config = {"extra": "allow"}  # 允许 model_N 动态字段

    def get_model(self, model_key: str) -> str | None:
        """获取指定的模型名称，如 model_1, model_2"""
        return getattr(self, model_key, None)

    def list_models(self) -> dict[str, str]:
        """列出所有 model_N 字段"""
        result = {}
        extras = self.model_extra or {}
        for key, value in extras.items():
            if key.startswith("model_") and isinstance(value, str):
                result[key] = value
        return result


class NotificationSettings(BaseModel):
    """通知设置。

    Attributes:
        enabled: toast 总开关（web 桌面提醒 + 透传系统级通知），
            关闭后后端不再下发任何 toast 事件
        sound: 提示音效开关。两个开关相互独立写入，但音效仅在
            toast 总开关开启时生效（Settings.toast_sound_enabled 联动判定）
    """

    enabled: bool = True
    sound: bool = True


class ComputerUseSettings(BaseModel):
    """Computer Use 配置

    控制是否注入 computer use 相关 MCP 工具与 skill（默认关闭）。

    Attributes:
        enabled: 是否启用 computer use（关闭时不注入任何相关工具/skill）
    """

    enabled: bool = False


class Settings(BaseModel):
    """IllusionAgent 主设置模型（env_N 分组格式）"""

    model_config = {"extra": "allow"}  # 允许 env_N 动态字段

    # 活跃模型引用（格式：env_N.model_N）
    model: str = "env_1.model_1"

    # 全局配置
    context_window: int = 200_000

    # 保留的非模型字段
    max_tokens: int = 16384
    max_turns: int = 200
    permission: PermissionSettings = Field(default_factory=PermissionSettings)
    hooks: dict[str, Any] = Field(default_factory=dict)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    title: TitleSettings = Field(default_factory=TitleSettings)
    goal: GoalSettings = Field(default_factory=GoalSettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    computer_use: ComputerUseSettings = Field(default_factory=ComputerUseSettings)
    enabled_plugins: dict[str, bool] = Field(default_factory=dict)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)
    ui_language: str = ""  # 空字符串表示未设置，由 _ensure_language 引导选择
    show_thinking: bool = True
    effort: str = "medium"
    working_directory: str | None = None  # 固定工作目录
    theme: str = "light"

    @property
    def toast_sound_enabled(self) -> bool:
        """音效开关的实际生效值。

        音效与 toast 是两个独立配置项，但音效只在 toast 总开关有效时
        才处理：toast 关闭时无论 sound 取值如何都不发声。
        """
        return self.notifications.enabled and self.notifications.sound

    @field_validator("mcp_servers", mode="before")
    @classmethod
    def _default_mcp_type_to_stdio(cls, value: Any) -> Any:
        """为缺少 type 字段的 MCP 服务器配置补全 ``type: "stdio"``。"""
        if not isinstance(value, dict):
            return value
        return {name: _normalize_server_config_type(cfg) for name, cfg in value.items()}

    # --- env_N 配置辅助方法 ---

    def get_env(self, env_key: str) -> EnvConfig | None:
        """获取指定的环境配置"""
        # 首先检查直接属性
        value = getattr(self, env_key, None)
        if isinstance(value, dict):
            return EnvConfig.model_validate(value)
        if isinstance(value, EnvConfig):
            return value

        # 然后检查 model_extra
        extras = self.model_extra or {}
        value = extras.get(env_key)
        if isinstance(value, dict):
            return EnvConfig.model_validate(value)
        if isinstance(value, EnvConfig):
            return value

        return None

    def list_envs(self) -> dict[str, EnvConfig]:
        """列出所有 env_N 配置"""
        result = {}
        extras = self.model_extra or {}
        for key, value in extras.items():
            if key.startswith("env_"):
                if isinstance(value, EnvConfig):
                    result[key] = value
                elif isinstance(value, dict):
                    result[key] = EnvConfig.model_validate(value)
        return result

    @property
    def _active_env_key(self) -> str:
        """解析 model 字段，返回 env key"""
        if "." in self.model:
            return self.model.split(".", 1)[0]
        return "env_1"

    @property
    def _active_model_key(self) -> str:
        """解析 model 字段，返回 model key"""
        if "." in self.model:
            return self.model.split(".", 1)[1]
        return "model_1"

    @property
    def _active_env(self) -> EnvConfig:
        """返回当前活跃的环境配置"""
        env = self.get_env(self._active_env_key)
        if env is None:
            envs = self.list_envs()
            if envs:
                return next(iter(envs.values()))
            return EnvConfig(api_format="anthropic")
        return env

    @property
    def _active_model_name(self) -> str:
        """返回当前活跃的模型名称"""
        env = self._active_env
        model_name = env.get_model(self._active_model_key)
        if model_name is None:
            models = env.list_models()
            if models:
                return next(iter(models.values()))
            return "claude-sonnet-4-6"
        return model_name

    def resolve_model_ref(self, ref: str | None) -> str | None:
        """解析模型引用（仅支持 env_N.model_M 格式）。

        用于记忆提取/整合子代理等可指定模型的子系统。

        Args:
            ref: 模型引用字符串。None 表示不指定（调用方回退到当前模型）；
                仅识别 ``env_N.model_M`` 格式，其他格式视为无效返回 None。

        Returns:
            str | None: 解析出的模型名称；ref 为 None 或无效格式时返回 None
        """
        _, model_name = self.resolve_model_ref_with_env(ref)
        return model_name

    def resolve_model_ref_with_env(self, ref: str | None) -> tuple[str | None, str | None]:
        """解析模型引用，返回 (env_key, model_name)。

        用于跨环境构建 API client：当引用指向非当前 env 时，
        调用方需要按 env_key 的端点与凭据独立构建 client。

        Args:
            ref: 模型引用字符串（``env_N.model_M`` 格式）

        Returns:
            tuple[str | None, str | None]: (env_key, model_name)；
                ref 为 None 或无效格式时返回 (None, None)
        """
        if not ref:
            return None, None
        # 仅识别 env_N.model_M 格式
        parts = ref.split(".", 1)
        if len(parts) != 2 or not parts[0].startswith("env_"):
            return None, None
        env_key, model_key = parts
        env = self.get_env(env_key)
        if env is None:
            return None, None
        return env_key, env.get_model(model_key)

    # --- 兼容性属性 ---

    @property
    def active_model_name(self) -> str:
        """兼容性属性：当前活跃模型名称"""
        return self._active_model_name

    @property
    def api_key(self) -> str:
        """兼容性属性：当前活跃环境的 API 密钥"""
        return self._active_env.api_key

    @property
    def base_url(self) -> str | None:
        """兼容性属性：当前活跃环境的 base URL"""
        return self._active_env.base_url

    @property
    def api_format(self) -> str:
        """兼容性属性：当前活跃环境的 API 格式"""
        return self._active_env.api_format

    def _resolve_api_key_from_env(self, env: EnvConfig, env_key: str) -> str:
        """从 EnvConfig 与 credentials.json 解析 API 密钥（核心逻辑）。

        优先级：EnvConfig.api_key > EnvConfig.auth_token > credentials.json(env_N)

        Args:
            env: 环境配置对象
            env_key: 环境键名（用于读取 credentials.json）

        Returns:
            str: API 密钥字符串

        Raises:
            ValueError: 未找到密钥时抛出
        """
        # 检查 EnvConfig 中的 api_key
        if env.api_key:
            return env.api_key

        # 检查 EnvConfig 中的 auth_token
        if env.auth_token:
            return env.auth_token

        # 从 credentials.json 的 env_N 读取
        from illusion.auth.storage import load_env_credential

        env_cred = load_env_credential(env_key, "api_key")
        if env_cred:
            return env_cred
        env_cred = load_env_credential(env_key, "auth_token")
        if env_cred:
            return env_cred

        from illusion.config.i18n import t as _t

        raise ValueError(_t("no_api_key"))

    def resolve_api_key_for(self, env_key: str) -> str:
        """解析指定环境的 API 密钥（含 env 存在性校验）。

        Args:
            env_key: 环境键名（如 env_1）

        Returns:
            str: API 密钥字符串

        Raises:
            ValueError: env 不存在或未找到密钥时抛出
        """
        env = self.get_env(env_key)
        if env is None:
            from illusion.config.i18n import t as _t

            raise ValueError(_t("no_api_key"))
        return self._resolve_api_key_from_env(env, env_key)

    def _resolve_auth_from_env(self, env: EnvConfig, env_key: str) -> ResolvedAuth:
        """从 EnvConfig 与 credentials.json 解析认证信息（核心逻辑）。

        Args:
            env: 环境配置对象
            env_key: 环境键名（用于读取 credentials.json）

        Returns:
            ResolvedAuth: 解析后的认证对象

        Raises:
            ValueError: 认证配置错误时抛出
        """
        # 检查 EnvConfig 中的 api_key
        if env.api_key:
            return ResolvedAuth(
                auth_kind="api_key",
                value=env.api_key,
                source="env_config",
                state="configured",
            )

        # 检查 EnvConfig 中的 auth_token
        if env.auth_token:
            return ResolvedAuth(
                auth_kind="auth_token",
                value=env.auth_token,
                source="env_config",
                state="configured",
            )

        # 从 credentials.json 的 env_N 读取
        from illusion.auth.storage import load_env_credential

        env_cred = load_env_credential(env_key, "api_key")
        if env_cred:
            return ResolvedAuth(
                auth_kind="api_key",
                value=env_cred,
                source=f"file:{env_key}",
                state="configured",
            )
        env_cred = load_env_credential(env_key, "auth_token")
        if env_cred:
            return ResolvedAuth(
                auth_kind="auth_token",
                value=env_cred,
                source=f"file:{env_key}",
                state="configured",
            )

        from illusion.config.i18n import t as _t

        raise ValueError(_t("no_auth"))

    def resolve_auth_for(self, env_key: str) -> ResolvedAuth:
        """解析指定环境的认证信息（含 env 存在性校验）。

        Args:
            env_key: 环境键名（如 env_1）

        Returns:
            ResolvedAuth: 解析后的认证对象

        Raises:
            ValueError: env 不存在或认证配置错误时抛出
        """
        env = self.get_env(env_key)
        if env is None:
            from illusion.config.i18n import t as _t

            raise ValueError(_t("no_auth"))
        return self._resolve_auth_from_env(env, env_key)

    def resolve_api_key(self) -> str:
        """解析当前活跃环境的 API 密钥（兼容旧调用）。

        保留旧语义：_active_env 在活跃 env 缺失时回退到第一个可用 env，
        避免旧式 settings（如 model 无 env_N 前缀）升级后启动失败。
        """
        return self._resolve_api_key_from_env(self._active_env, self._active_env_key)

    def resolve_auth(self) -> ResolvedAuth:
        """解析当前活跃环境的认证信息（兼容旧调用）。

        保留旧语义：_active_env 在活跃 env 缺失时回退到第一个可用 env，
        避免旧式 settings（如 model 无 env_N 前缀）升级后终端模式启动失败。
        """
        return self._resolve_auth_from_env(self._active_env, self._active_env_key)

    def merge_cli_overrides(self, **overrides: Any) -> Settings:
        """返回应用了 CLI 覆盖的新 Settings（仅非 None 值）

        对全局字段（model/max_turns/effort 等）直接使用 model_copy。
        对 env 级字段（api_key/base_url/api_format）创建更新后的 EnvConfig
        并 setattr 到活跃 env_N 上，使 --api-key/--base-url/--api-format 真正生效。

        Args:
            **overrides: 要覆盖的字段

        Returns:
            Settings: 应用覆盖后的新实例
        """
        # env 级覆盖需写入活跃 EnvConfig，不能直接 model_copy
        env_keys = {"api_key", "auth_token", "base_url", "api_format"}
        env_overrides = {k: v for k, v in overrides.items() if v is not None and k in env_keys}
        global_updates = {k: v for k, v in overrides.items() if v is not None and k not in env_keys}

        new_settings = self.model_copy(update=global_updates)
        if env_overrides:
            env = new_settings._active_env
            env_key = new_settings._active_env_key
            updated_env = env.model_copy(update=env_overrides)
            setattr(new_settings, env_key, updated_env)
        return new_settings


def _default_notification_config() -> dict[str, Any]:
    """返回默认通知配置。

    作为显式默认配置写入 settings.json，使用户可直接查看/修改，
    而非仅在内存中按默认值加载运行（与 _default_sandbox_config 同一策略）。
    """
    return NotificationSettings().model_dump()


def _default_computer_use_config() -> dict[str, Any]:
    """返回默认 computer use 配置。

    与 sandbox/notifications 同一策略：缺失时一次性落盘，让字段在
    settings.json 中可见可改；用户手动改过该键后不再触碰。
    """
    return ComputerUseSettings().model_dump()


def _default_sandbox_config() -> dict[str, Any]:
    """返回默认沙箱配置。

    作为显式默认配置写入 settings.json，使用户可直接查看/修改，
    而非仅在内存中按默认值加载运行。
    """
    return {
        "enabled_platforms": [],
        "excluded_commands": [],
        "network": {
            "allowed_domains": [],
            "denied_domains": [],
            "allow_unix_sockets": [],
            "allow_all_unix_sockets": False,
            "allow_local_binding": False,
            "http_proxy_port": None,
            "socks_proxy_port": None,
        },
        "filesystem": {
            "allow_read": [],
            "deny_read": [],
            "allow_write": ["."],
            "deny_write": [],
        },
        "ignore_violations": {},
        "enable_weaker_nested_sandbox": False,
        "enable_weaker_network_isolation": False,
        "mandatory_deny_search_depth": 3,
        "allow_git_config": False,
        "ripgrep": None,
    }


def load_settings(config_path: Path | None = None) -> Settings:
    """从配置文件加载设置

    Args:
        config_path: settings.json 的路径。如果为 None，使用默认位置。

    Returns:
        Settings: 从配置文件加载的 Settings 实例
    """
    if config_path is None:
        from illusion.config.paths import get_config_file_path

        config_path = get_config_file_path()

    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        # 兼容 mcpServers（camelCase）键，映射到 mcp_servers（snake_case）
        if "mcpServers" in raw and "mcp_servers" not in raw:
            raw["mcp_servers"] = raw.pop("mcpServers")

        # 将默认沙箱配置显式写入 settings.json（透明可改，非仅内存默认值）。
        # 仅在配置缺失 sandbox 键时一次性落盘，避免每次加载都写。
        if "sandbox" not in raw:
            raw["sandbox"] = _default_sandbox_config()
            try:
                save_settings(Settings.model_validate(raw), config_path)
            except (OSError, ValueError):
                # 写入失败不阻塞加载（如只读配置目录）
                pass

        # 将默认通知配置（toast 总开关 / 音效）显式写入 settings.json。
        # 与 sandbox 同一策略：缺失时一次性落盘，让字段在文件中可见可改；
        # 用户手动加过该键后不再触碰。
        if "notifications" not in raw:
            raw["notifications"] = _default_notification_config()
            try:
                save_settings(Settings.model_validate(raw), config_path)
            except (OSError, ValueError):
                pass

        # 将默认 computer use 配置（开关）显式写入 settings.json。
        # 同一策略：缺失时一次性落盘，用户手动改过后不再触碰。
        if "computer_use" not in raw:
            raw["computer_use"] = _default_computer_use_config()
            try:
                save_settings(Settings.model_validate(raw), config_path)
            except (OSError, ValueError):
                pass

        # 清理 env_N 中不该存在的 model 字段
        for key in list(raw.keys()):
            if key.startswith("env_") and isinstance(raw[key], dict):
                raw[key].pop("model", None)

        return Settings.model_validate(raw)

    return Settings()


def save_settings(settings: Settings, config_path: Path | None = None) -> None:
    """将设置持久化到配置文件

    Args:
        settings: 要保存的 Settings 实例
        config_path: 写入路径。如果为 None，使用默认位置
    """
    if config_path is None:
        from illusion.config.paths import get_config_file_path

        config_path = get_config_file_path()

    config_path.parent.mkdir(parents=True, exist_ok=True)

    # 序列化并重排字段，env_N 置顶
    data = settings.model_dump()

    # 清理 env_N 中的 model 字段和 auth_token/api_key 空字符串
    for key in data:
        if key.startswith("env_") and isinstance(data[key], dict):
            data[key].pop("model", None)
            if not data[key].get("auth_token"):
                data[key].pop("auth_token", None)
            if not data[key].get("api_key"):
                data[key].pop("api_key", None)

    ordered: dict[str, object] = {}
    for key in sorted(data):
        if key.startswith("env_"):
            ordered[key] = data[key]
    for key, value in data.items():
        if not key.startswith("env_"):
            ordered[key] = value

    atomic_write_text(
        config_path,
        json.dumps(ordered, indent=2, ensure_ascii=False) + "\n",
    )
