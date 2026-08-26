"""Web 端 env 配置和 OAuth 路由模块。

供 web 前端和未来 Electron 客户端通过 HTTP REST 管理 API 环境配置。
WebSocket 继续承载实时聊天流，与此处 HTTP 端点职责分离。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError

from illusion.auth.manager import AuthManager
from illusion.config.i18n import t as _t
from illusion.config.settings import Settings, load_settings, save_settings


def _next_model_key(env_data: dict[str, Any]) -> str:
    """为 env 自动分配下一个模型键（model_N，取现有最大编号 +1）

    与 CLI /model add 的分配规则一致。删除中间模型后编号不连续时
    （如剩 model_1/model_3）按最大编号续排，避免复用已有 key 覆盖模型。

    Args:
        env_data: env 配置字典（可能含 model_N 字段）

    Returns:
        str: 新模型键名（如 model_4）
    """
    existing_nums = []
    for k in env_data:
        if isinstance(k, str) and k.startswith("model_"):
            try:
                existing_nums.append(int(k.split("_", 1)[1]))
            except (ValueError, IndexError):
                continue
    return f"model_{max(existing_nums, default=0) + 1}"


class CreateEnvRequest(BaseModel):
    """新增 env 请求体。"""

    api_format: str = Field(..., description="API 格式：anthropic/openai/copilot/codex")
    base_url: str | None = None
    api_key: str = ""
    auth_token: str = ""
    model_1: str
    model_2: str | None = None


class ModelEntry(BaseModel):
    """模型条目。"""

    key: str | None = Field(
        default=None,
        pattern=r"^model_\d+$",
        description="模型键名（如 model_3）。缺省时由后端按现有最大编号 +1 自动分配，"
        "避免前端用过期快照计算 key 导致连续添加时相互覆盖",
    )
    value: str = Field(..., min_length=1, description="模型名称")


class UpdateEnvRequest(BaseModel):
    """修改 env 请求体。"""

    api_format: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    auth_token: str | None = None
    add_models: list[ModelEntry] | None = None
    remove_models: list[str] | None = None


class OauthPollRequest(BaseModel):
    """OAuth 轮询请求体。"""

    device_code: str = Field(..., min_length=1, description="设备码")


class UpdateUiLanguageRequest(BaseModel):
    """修改界面语言请求体。"""

    ui_language: str = Field(..., pattern="^(zh-CN|en-US)$")


class UpdateWorkingDirectoryRequest(BaseModel):
    """修改工作目录请求体。

    空字符串表示清除工作目录设置（置为 None）。
    """

    working_directory: str = ""


class UpdateModelParamsRequest(BaseModel):
    """修改模型参数（context_window / max_tokens / max_turns，均为可选）。"""

    context_window: int | None = Field(default=None, gt=0, description="上下文窗口大小（token）")
    max_tokens: int | None = Field(default=None, gt=0, description="最大输出 tokens")
    max_turns: int | None = Field(default=None, ge=1, le=512, description="最大轮次（1~512）")


class UpdateMemoryRequest(BaseModel):
    """修改记忆配置请求体。

    字段均可选，只更新提供的字段：
        - enabled: 是否启用记忆功能
        - auto_extract: 是否允许后台 LLM 自动提取/整合（关闭后仅手动记录）
        - extract_model: 提取子代理模型（env_N.model_M），空串清除
        - dream_model: 整合子代理模型（env_N.model_M），空串清除
        - directory: 自定义记忆目录（绝对路径或 ~/ 开头），空串清除
    """

    enabled: bool | None = None
    auto_extract: bool | None = None
    extract_model: str | None = None
    dream_model: str | None = None
    directory: str | None = None


class UpdateTitleRequest(BaseModel):
    """修改会话自动标题配置请求体。

    字段均可选，只更新提供的字段：
        - enabled: 是否启用自动标题
        - model: 标题生成子代理模型（env_N.model_M），空串清除（继承当前）
    """

    enabled: bool | None = None
    model: str | None = None


class UpdatePermissionReviewRequest(BaseModel):
    """修改权限 LLM 自动审核配置请求体。

    字段均可选，只更新提供的字段：
        - auto_review: auto 模式下高危操作与沙箱拦截（工作区外读写）是否改由 LLM 审核放行
        - review_model: 审核模型（env_N.model_M），空串清除（继承当前会话模型）
    """

    auto_review: bool | None = None
    review_model: str | None = None


class UpdateThemeRequest(BaseModel):
    """修改 Web 端主题请求体。

    取值：light（浅色）/ dark（深色）/ system（跟随系统）。
    该字段仅用于 web 前端，不传递到 terminal 端。
    """

    theme: str = Field(..., pattern="^(light|dark|system)$")


class UpdateSandboxRequest(BaseModel):
    """修改沙箱配置请求体。

    字段均可选，只更新提供的字段。数据结构与
    illusion.config.settings.SandboxSettings 对齐（snake_case）。
    """

    enabled_platforms: list[str] | None = None
    excluded_commands: list[str] | None = None
    network: dict[str, Any] | None = None
    filesystem: dict[str, Any] | None = None
    ignore_violations: dict[str, list[str]] | None = None
    enable_weaker_nested_sandbox: bool | None = None
    enable_weaker_network_isolation: bool | None = None
    mandatory_deny_search_depth: int | None = None
    allow_git_config: bool | None = None
    ripgrep: dict[str, Any] | None = None


class UpdatePermissionRiskRequest(BaseModel):
    """修改权限风险分级配置请求体。

    字段均可选，只更新提供的字段。数据结构与
    PermissionSettings 中的 risk 分级字段对齐（snake_case）。
    分别为 HIGH（dangerous_bash/powershell_patterns）、
    LOW（read_only_commands）、MEDIUM（medium_risk_tools）。
    """

    dangerous_bash_patterns: list[str] | None = None
    dangerous_powershell_patterns: list[str] | None = None
    read_only_commands: list[str] | None = None
    medium_risk_tools: list[str] | None = None


def _sandbox_settings_payload(sandbox: Any) -> dict[str, Any]:
    """将 SandboxSettings 序列化为前端可读的字典。"""
    return {
        "enabled_platforms": list(sandbox.enabled_platforms),
        "excluded_commands": list(sandbox.excluded_commands),
        "network": {
            "allowed_domains": list(sandbox.network.allowed_domains),
            "denied_domains": list(sandbox.network.denied_domains),
            "allow_unix_sockets": list(sandbox.network.allow_unix_sockets),
            "allow_all_unix_sockets": sandbox.network.allow_all_unix_sockets,
            "allow_local_binding": sandbox.network.allow_local_binding,
            "http_proxy_port": sandbox.network.http_proxy_port,
            "socks_proxy_port": sandbox.network.socks_proxy_port,
        },
        "filesystem": {
            "allow_read": list(sandbox.filesystem.allow_read),
            "deny_read": list(sandbox.filesystem.deny_read),
            "allow_write": list(sandbox.filesystem.allow_write),
            "deny_write": list(sandbox.filesystem.deny_write),
        },
        "ignore_violations": dict(sandbox.ignore_violations),
        "enable_weaker_nested_sandbox": sandbox.enable_weaker_nested_sandbox,
        "enable_weaker_network_isolation": sandbox.enable_weaker_network_isolation,
        "mandatory_deny_search_depth": sandbox.mandatory_deny_search_depth,
        "allow_git_config": sandbox.allow_git_config,
        "ripgrep": (
            {"command": sandbox.ripgrep.command, "args": list(sandbox.ripgrep.args)}
            if sandbox.ripgrep is not None
            else None
        ),
    }


def _permission_risk_payload() -> dict[str, Any]:
    """返回权限风险分级（内置只读，LOW/MEDIUM/HIGH 三层级）。

    风险分级规则已内置（risk.py），web 端只读展示，不支持修改：
        - HIGH: dangerous_bash_patterns / dangerous_powershell_patterns
        - MEDIUM: medium_risk_tools
        - LOW: read_only_commands
    """
    from illusion.permissions.risk import (
        DEFAULT_DANGEROUS_BASH_PATTERNS,
        DEFAULT_DANGEROUS_POWERSHELL_PATTERNS,
        DEFAULT_MEDIUM_RISK_TOOLS,
        DEFAULT_READ_ONLY_COMMANDS,
    )

    return {
        "dangerous_bash_patterns": list(DEFAULT_DANGEROUS_BASH_PATTERNS),
        "dangerous_powershell_patterns": list(DEFAULT_DANGEROUS_POWERSHELL_PATTERNS),
        "read_only_commands": list(DEFAULT_READ_ONLY_COMMANDS),
        "medium_risk_tools": list(DEFAULT_MEDIUM_RISK_TOOLS),
    }


def register_env_routes(app: FastAPI, host_config: Any | None = None) -> None:
    """注册 env/oauth/settings 相关 HTTP 路由到 FastAPI app。"""

    @app.get("/api/envs")
    async def list_envs() -> dict[str, Any]:
        """列出所有 env_N 配置。"""
        manager = AuthManager()
        statuses = manager.get_env_credential_statuses()
        envs = []
        for env_key, info in statuses.items():
            envs.append(
                {
                    "env_key": env_key,
                    "api_format": info.get("api_format", ""),
                    "base_url": info.get("base_url", ""),
                    "has_credential": info.get("has_credential", False),
                    "active": info.get("active", False),
                    "models": [],
                }
            )
        # 从 settings 读取 models
        settings = load_settings()
        for env in envs:
            env_config = settings.list_envs().get(env["env_key"])
            if env_config:
                env["models"] = env_config.list_models()
        active_key = manager.get_active_env_key() if envs else None
        return {"envs": envs, "active_env_key": active_key}

    @app.post("/api/envs")
    async def create_env(req: CreateEnvRequest) -> dict[str, Any]:
        """新增 env。"""
        settings = load_settings()
        # 自动分配 env_N key
        existing = set(settings.list_envs().keys())
        n = 1
        while f"env_{n}" in existing:
            n += 1
        env_key = f"env_{n}"
        # 构建 env 配置数据（不包含敏感凭证）
        env_data: dict[str, Any] = {
            "api_format": req.api_format,
            "base_url": req.base_url or "",
            "model_1": req.model_1,
        }
        if req.model_2:
            env_data["model_2"] = req.model_2
        # 合并到 settings 的 model_extra 中
        extras = dict(settings.model_extra or {})
        extras[env_key] = env_data
        new_settings = settings.model_copy(update=extras)
        # 如果是第一个 env，设置为 active
        if not existing:
            new_settings.model = f"{env_key}.model_1"
        # 原子写入（save_settings 内部处理 atomic_write + 字段排序）
        save_settings(new_settings)
        # 凭证保存到 credentials.json
        if req.api_key:
            manager = AuthManager()
            manager.store_env_api_key(env_key, req.api_key)
        if req.auth_token:
            manager = AuthManager()
            manager.store_env_auth_token(env_key, req.auth_token)
        return {"env_key": env_key, "success": True}

    @app.patch("/api/envs/{env_key}")
    async def update_env(env_key: str, req: UpdateEnvRequest) -> dict[str, Any]:
        """修改 env 字段。"""
        settings = load_settings()
        envs = settings.list_envs()
        if env_key not in envs:
            raise HTTPException(status_code=404, detail=_t("unknown_env", env_key=env_key))
        # 使用 AuthManager.update_env 处理 api_format/base_url/api_key/auth_token
        manager = AuthManager()
        if (
            req.api_format is not None
            or req.base_url is not None
            or req.api_key is not None
            or req.auth_token is not None
        ):
            manager.update_env(
                env_key,
                api_format=req.api_format,
                base_url=req.base_url,
                api_key=req.api_key,
                auth_token=req.auth_token,
            )
        # 处理 add_models / remove_models（直接操作 model_extra）
        if req.add_models or req.remove_models:
            settings = load_settings()  # 重新加载（update_env 可能已保存）
            # 用 model_dump → 修改 → model_validate 替代 model_copy(update=extras)，
            # 后者对 Pydantic extra 字段（env_N）更新不可靠
            data = settings.model_dump()
            env_data = data.get(env_key, {})
            if isinstance(env_data, dict):
                if req.add_models:
                    for m in req.add_models:
                        key = m.key or _next_model_key(env_data)
                        env_data[key] = m.value
                if req.remove_models:
                    for key in req.remove_models:
                        env_data.pop(key, None)
                data[env_key] = env_data
                new_settings = Settings.model_validate(data)
                save_settings(new_settings)
        return {"success": True}

    @app.delete("/api/envs/{env_key}")
    async def delete_env(env_key: str) -> dict[str, Any]:
        """删除 env（拒绝删除 active env）。"""
        manager = AuthManager()
        # 先检查环境是否存在
        if env_key not in manager.list_envs():
            raise HTTPException(status_code=404, detail=_t("unknown_env", env_key=env_key))
        # 再检查是否为活动环境
        if env_key == manager.get_active_env_key():
            raise HTTPException(status_code=400, detail=_t("cannot_remove_active_env"))
        manager.remove_env(env_key)
        manager.clear_env_api_key(env_key)
        return {"success": True}

    @app.post("/api/envs/{env_key}/activate")
    async def activate_env(env_key: str) -> dict[str, Any]:
        """切换 active env。"""
        manager = AuthManager()
        try:
            manager.use_env(env_key)
        except ValueError:
            raise HTTPException(status_code=404, detail=_t("unknown_env", env_key=env_key))
        return {"success": True}

    @app.post("/api/oauth/{provider}/start")
    async def oauth_start(provider: str) -> dict[str, Any]:
        """启动 OAuth device flow。"""
        if provider == "copilot":
            from illusion.auth.copilot import CopilotAuth

            auth = CopilotAuth()
            return await asyncio.to_thread(auth.start_device_flow)
        elif provider == "codex":
            from illusion.auth.codex_oauth import CodexOAuth

            auth = CodexOAuth()  # type: ignore[assignment]
            return await asyncio.to_thread(auth.start_device_flow)
        else:
            raise HTTPException(
                status_code=400, detail=_t("unknown_oauth_provider", provider=provider)
            )

    @app.post("/api/oauth/{provider}/poll")
    async def oauth_poll(provider: str, req: OauthPollRequest) -> dict[str, Any]:
        """轮询 OAuth 完成状态。"""
        if provider == "copilot":
            from illusion.auth.copilot import CopilotAuth

            auth = CopilotAuth()
            try:
                success = await asyncio.to_thread(auth.poll_for_token, req.device_code)
                return {"success": success}
            except RuntimeError as e:
                return {"success": False, "error": str(e)}
        elif provider == "codex":
            from illusion.auth.codex_oauth import CodexOAuth

            auth = CodexOAuth()  # type: ignore[assignment]
            try:
                success = await asyncio.to_thread(auth.poll_for_token, req.device_code)
                return {"success": success}
            except RuntimeError as e:
                return {"success": False, "error": str(e)}
        else:
            raise HTTPException(
                status_code=400, detail=_t("unknown_oauth_provider", provider=provider)
            )

    @app.patch("/api/settings/ui_language")
    async def update_ui_language(req: UpdateUiLanguageRequest) -> dict[str, Any]:
        """修改界面语言。"""
        settings = load_settings()
        new_settings = settings.model_copy(update={"ui_language": req.ui_language})
        save_settings(new_settings)
        return {"success": True}

    @app.get("/api/settings")
    async def get_settings() -> dict[str, Any]:
        """读取非敏感 settings 字段（供配置表单回显）。

        仅返回配置表单需要的字段，不含任何凭据（api_key/auth_token 存于
        credentials.json，由 /api/envs 单独管理 has_credential 标志）。
        """
        settings = load_settings()

        return {
            "ui_language": settings.ui_language,
            "working_directory": settings.working_directory,
            "context_window": settings.context_window,
            "max_tokens": settings.max_tokens,
            "max_turns": settings.max_turns,
            "model": settings.model,
            "theme": settings.theme,
            "memory": {
                "enabled": settings.memory.enabled,
                "auto_extract": settings.memory.auto_extract,
                "extract_model": settings.memory.extract_model,
                "dream_model": settings.memory.dream_model,
                "directory": settings.memory.directory,
            },
            "title": {
                "enabled": settings.title.enabled,
                "model": settings.title.model,
            },
            "sandbox": _sandbox_settings_payload(settings.sandbox),
            "permission": _permission_risk_payload(),
            "permission_review": {
                "auto_review": settings.permission.auto_review,
                "review_model": settings.permission.review_model,
            },
        }

    @app.patch("/api/settings/permission")
    async def update_permission(_req: UpdatePermissionRiskRequest) -> dict[str, Any]:
        """权限风险分级已内置，web 端只读展示，不支持修改。

        返回 400 明确告知前端该配置为只读，避免静默忽略写请求。
        """
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail="权限风险分级已内置为只读，可编辑的沙箱路径白名单请使用 /api/settings/sandbox",
        )

    @app.patch("/api/settings/permission-review")
    async def update_permission_review(req: UpdatePermissionReviewRequest) -> dict[str, Any]:
        """修改权限 LLM 自动审核配置。

        仅更新请求中提供的字段，其余保持不变；保存后审核层在下次工具确认时
        读取新配置即时生效（审核只在 full_auto 模式下生效，yolo/plan/default 不变）。
        """
        settings = load_settings()
        updates: dict[str, Any] = {}
        if req.auto_review is not None:
            updates["auto_review"] = req.auto_review
        if req.review_model is not None:
            review_model_value = (req.review_model or "").strip() or None
            # 设置时即校验引用有效性（坏 ref 只会在首次审批时懒失败静默回退）
            if review_model_value is not None:
                env_key, model_name = settings.resolve_model_ref_with_env(review_model_value)
                if not (env_key and model_name):
                    raise HTTPException(
                        status_code=400,
                        detail=_t("permission_review_model_invalid", ref=review_model_value),
                    )
            updates["review_model"] = review_model_value
        new_permission = settings.permission.model_copy(update=updates)
        new_settings = settings.model_copy(update={"permission": new_permission})
        save_settings(new_settings)
        return {
            "success": True,
            "permission_review": {
                "auto_review": new_permission.auto_review,
                "review_model": new_permission.review_model,
            },
        }

    @app.patch("/api/settings/sandbox")
    async def update_sandbox(req: UpdateSandboxRequest) -> dict[str, Any]:
        """修改沙箱配置。

        仅更新请求中提供的字段，其余保持不变。保存后返回最新沙箱配置。
        """
        from illusion.config.settings import (
            SandboxFilesystemSettings,
            SandboxNetworkSettings,
            SandboxRipgrepSettings,
        )
        from illusion.sandbox import SandboxManager

        settings = load_settings()
        current = settings.sandbox
        # 嵌套结构先经对应模型验证再更新：model_copy(update=...) 不做验证，
        # 请求体里的裸 dict 若直接塞入会替换掉 SandboxXxxSettings 实例
        # （后续属性访问 AttributeError、序列化告警、落盘结构退化）。
        validators: dict[str, Any] = {
            "network": SandboxNetworkSettings,
            "filesystem": SandboxFilesystemSettings,
            "ripgrep": SandboxRipgrepSettings,
        }
        updates: dict[str, Any] = {}
        for field in UpdateSandboxRequest.model_fields:
            value = getattr(req, field)
            if value is None:
                continue
            validator = validators.get(field)
            if validator is None:
                updates[field] = value
                continue
            try:
                updates[field] = validator.model_validate(value)
            except ValidationError as exc:
                # 非法嵌套字段（如域名 pattern 不匹配）返回 400 而非 500
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        new_sandbox = current.model_copy(update=updates)
        new_settings = settings.model_copy(update={"sandbox": new_sandbox})
        save_settings(new_settings)
        # 热重载沙箱管理器配置，使修改立即生效
        try:
            SandboxManager().update_config(load_settings())
        except (OSError, ValueError, RuntimeError) as exc:
            import logging
            logging.getLogger(__name__).warning("sandbox 配置热重载失败: %s", exc)
        return {"success": True, "sandbox": _sandbox_settings_payload(new_sandbox)}

    @app.patch("/api/settings/memory")
    async def update_memory(req: UpdateMemoryRequest) -> dict[str, Any]:
        """修改记忆配置。

        - enabled: 启用/禁用记忆功能
        - auto_extract: 允许/禁止后台 LLM 自动提取与整合（关闭后仅手动记录）
        - directory: 自定义记忆目录；空字符串清除（置为 None）；
          非空值经 resolve_custom_memory_dir 校验（绝对路径或 ~/ 开头），
          校验失败返回 400。
        """
        from illusion.memory.paths import resolve_custom_memory_dir

        settings = load_settings()
        updates: dict[str, Any] = {}

        if req.enabled is not None:
            updates["enabled"] = req.enabled

        if req.auto_extract is not None:
            updates["auto_extract"] = req.auto_extract

        if req.extract_model is not None:
            raw = (req.extract_model or "").strip()
            updates["extract_model"] = raw or None

        if req.dream_model is not None:
            raw = (req.dream_model or "").strip()
            updates["dream_model"] = raw or None

        if req.directory is not None:
            raw = (req.directory or "").strip()
            if not raw:
                updates["directory"] = None
            else:
                resolved = resolve_custom_memory_dir(raw)
                if resolved is None:
                    raise HTTPException(
                        status_code=400,
                        detail=_t("set_invalid_path", path=raw)
                        or "Invalid memory directory (must be an absolute path)",
                    )
                updates["directory"] = str(resolved)

        if updates:
            new_settings = settings.model_copy(
                update={"memory": settings.memory.model_copy(update=updates)}
            )
            save_settings(new_settings)
        return {"success": True, "memory": {**updates}}

    @app.patch("/api/settings/title")
    async def update_title(req: UpdateTitleRequest) -> dict[str, Any]:
        """修改会话自动标题配置。

        - enabled: 启用/禁用自动标题
        - model: 标题生成子代理模型；空串清除（置为 None，继承当前会话模型）
        """
        settings = load_settings()
        updates: dict[str, Any] = {}

        if req.enabled is not None:
            updates["enabled"] = req.enabled

        if req.model is not None:
            raw = (req.model or "").strip()
            updates["model"] = raw or None

        if updates:
            new_settings = settings.model_copy(
                update={"title": settings.title.model_copy(update=updates)}
            )
            save_settings(new_settings)
        return {"success": True, "title": {**updates}}

    @app.patch("/api/settings/theme")
    async def update_theme(req: UpdateThemeRequest) -> dict[str, Any]:
        """修改 Web 端主题。

        取值：light / dark / system。仅写入 settings.json，不传递到 terminal 端。
        """
        settings = load_settings()
        new_settings = settings.model_copy(update={"theme": req.theme})
        save_settings(new_settings)
        return {"success": True}

    @app.patch("/api/settings/working_directory")
    async def update_working_directory(req: UpdateWorkingDirectoryRequest) -> dict[str, Any]:
        """修改工作目录。

        空字符串表示清除工作目录（置为 None）；非空字符串经
        validate_and_normalize 校验并规范化后写入 settings.json。
        校验失败返回 400。
        """
        from illusion.cli.workspace import validate_and_normalize

        raw = (req.working_directory or "").strip()
        if not raw:
            # 清除工作目录
            settings = load_settings()
            new_settings = settings.model_copy(update={"working_directory": None})
            save_settings(new_settings)
            return {"success": True, "working_directory": None}
        resolved, err = validate_and_normalize(raw)
        if resolved is None:
            raise HTTPException(status_code=400, detail=err or _t("set_invalid_path", path=raw))
        settings = load_settings()
        new_settings = settings.model_copy(update={"working_directory": str(resolved)})
        save_settings(new_settings)
        return {"success": True, "working_directory": str(resolved)}

    @app.patch("/api/settings/model-params")
    async def update_model_params(req: UpdateModelParamsRequest) -> dict[str, Any]:
        """修改模型参数（context_window / max_tokens / max_turns）。

        仅更新请求体提供的字段；FastAPI 的 Field 校验保证取值为正（max_turns 限 1~512）。
        保存后把变更热应用到所有活跃 Web 主机的会话引擎与 app_state，
        使右栏上下文窗口显示与后续实际请求立即生效。
        """
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        if not updates:
            return {"success": True}
        settings = load_settings()
        new_settings = settings.model_copy(update=updates)
        save_settings(new_settings)
        # 运行时热生效：各活跃主机同步会话引擎与 app_state，并推送状态快照
        from illusion.ui.web.ws_host import iter_active_hosts

        for host in iter_active_hosts():
            host.apply_runtime_settings_sync()
        return {"success": True, **updates}
