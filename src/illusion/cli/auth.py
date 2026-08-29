"""
认证管理子命令
==============

提供认证登录、状态查看、登出、切换环境以及添加模型等功能。

子命令:
    - login: 交互式配置提供商认证（首次登录时选择语言和工作目录）
    - status: 查看所有环境的认证状态
    - logout: 清除指定环境的凭据
    - switch: 切换活动环境
    - add model: 向已有环境添加模型
"""
from __future__ import annotations

import sys
from typing import Any

import typer

from illusion.cli import add_app, auth_app
from illusion.cli.shared import _ensure_language
from illusion.cli.workspace import is_first_login, prompt_working_directory
from illusion.config.i18n import MESSAGES as _I18N
from illusion.config.i18n import t as _t

_FORMAT_OPTIONS: list[tuple[str, dict[str, str]]] = [
    ("anthropic", _I18N["anthropic_label"]),
    ("openai", _I18N["openai_label"]),
    ("response", _I18N["response_label"]),
    ("copilot", _I18N["copilot_label"]),
    ("codex", _I18N["codex_label"]),
]

_DEFAULT_ENDPOINTS: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "response": "https://api.openai.com/v1",
    "copilot": "https://api.githubcopilot.com",
    "codex": "https://chatgpt.com/backend-api",
}

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.4",
    "response": "gpt-5.4",
    "copilot": "gpt-5.5",
    "codex": "codex-mini",
}


def _prompt_models_and_create_env(
    manager: Any,
    api_format: str,
    format_choice: str,
    endpoint: str,
    auth_field: str,
    credential: str | None,
    extra_env_fields: dict[str, str] | None = None,
) -> str:
    """交互式输入多个 model 名并新建 env 保存。

    auth login 始终新建 env，不询问选择已有环境。
    添加 model 到已有环境请使用 `illusion add model`。

    Args:
        manager: AuthManager 实例
        api_format: API 格式（anthropic/openai/response/copilot/codex）
        format_choice: 提供商选择键（用于查找默认 model）
        endpoint: API 端点 URL
        auth_field: 凭据字段名（api_key/auth_token）
        credential: 凭据值，None 表示不存储（copilot/codex 由内部管理）
        extra_env_fields: 新建 env 时额外的字段（如 copilot 的 {"api_key": ""}）

    Returns:
        str: 最终保存的 env_key
    """
    from illusion.auth.storage import store_env_credential
    from illusion.commands.model import _ask_capabilities

    # 1. model 名循环输入：每个 model 询问图片能力（对象格式声明，
    #    未勾选视为无图片能力，fail-closed）
    models_to_add: list[tuple[str, list[str]]] = []
    default_model = _DEFAULT_MODELS.get(format_choice, "")
    while True:
        if default_model:
            prompt_text = f"{_t('enter_model')} ({_t('default_endpoint')}: {default_model}): "
            model_input = input(prompt_text).strip()
            if not model_input:
                model_input = default_model
        else:
            model_input = input(f"{_t('enter_model')}: ").strip()
            if not model_input:
                print(_t("model_required"), file=sys.stderr)
                continue
        capabilities = _ask_capabilities()
        models_to_add.append((model_input, capabilities))
        # 询问是否继续（直接回车默认退出）
        cont = input(f"{_t('add_another_model')} ").strip().lower()
        if cont != "y":
            break

    # 2. 新建 env_N
    envs = manager.list_envs()
    env_keys = list(envs.keys())
    existing_env_nums = []
    for k in env_keys:
        try:
            existing_env_nums.append(int(k.split("_")[1]))
        except (ValueError, IndexError):
            pass
    next_env_num = max(existing_env_nums, default=0) + 1
    target_env_key = f"env_{next_env_num}"
    env_config: dict[str, Any] = {
        "api_format": api_format,
        "base_url": endpoint,
    }
    if extra_env_fields:
        env_config.update(extra_env_fields)
    for i, (model_name, capabilities) in enumerate(models_to_add):
        env_config[f"model_{i + 1}"] = {"name": model_name, "capabilities": capabilities}
    setattr(manager.settings, target_env_key, env_config)
    if credential is not None:
        store_env_credential(target_env_key, auth_field, credential)
    manager.settings.model = f"{target_env_key}.model_1"

    manager.save_settings()
    return target_env_key


@auth_app.command("login")
def auth_login() -> None:
    """交互式配置提供商认证

    流程：选择提供商 → 认证 → 保存
    Copilot 使用 GitHub OAuth 设备码流程，其他提供商使用 API 密钥。
    始终新建 env，添加 model 到已有环境请使用 `illusion add model`。
    """
    from illusion.auth.flows import ApiKeyFlow
    from illusion.auth.manager import AuthManager
    from illusion.config import load_settings

    # 首次登录时选择 language，与工作目录设置判断一致
    if is_first_login(load_settings()):
        _ensure_language()

    manager = AuthManager()

    # 1. 选择 API 格式
    print(_t("select_api_format"))
    for i, (key, labels) in enumerate(_FORMAT_OPTIONS, 1):
        lang = manager.settings.ui_language or "en-US"
        label = labels.get(lang, labels.get("en-US", key))
        print(f"  {i}. {label}")
    raw = typer.prompt(_t("enter_number"), default="1")
    try:
        idx = int(raw.strip()) - 1
        if 0 <= idx < len(_FORMAT_OPTIONS):
            format_choice = _FORMAT_OPTIONS[idx][0]
        else:
            print(_t("invalid_selection"), file=sys.stderr)
            raise typer.Exit(1)
    except ValueError:
        print(_t("invalid_selection"), file=sys.stderr)
        raise typer.Exit(1)

    # --- Copilot 走设备码 OAuth 流程 ---
    if format_choice == "copilot":
        _copilot_login(manager)
        return

    # --- Codex 走外部 CLI 凭据读取流程 ---
    if format_choice == "codex":
        _codex_login(manager)
        return

    # --- 其他提供商走 API 密钥流程 ---

    # 2. 确定 API 格式：所选格式即协议；第三方/自建端点在下一步端点中
    #    直接填入即可（不单设"自定义"选项）
    auth_field = "api_key"  # 默认使用 api_key
    api_format = format_choice
    if api_format == "anthropic":
        # anthropic 协议支持 api_key 与 auth_token（Bearer）两种认证
        print(_t("select_auth_type"))
        print(f"  1. api_key ({_t('auth_type_api_key')})")
        print(f"  2. auth_token ({_t('auth_type_auth_token')})")
        raw = typer.prompt(_t("enter_number"), default="1")
        if raw.strip() == "2":
            auth_field = "auth_token"

    # 3. 输入端点（默认端点兜底；直接输入即自定义端点）
    default_ep = _DEFAULT_ENDPOINTS[format_choice]
    prompt_text = f"{_t('enter_endpoint')} ({_t('default_endpoint')}: {default_ep}): "
    endpoint = input(prompt_text).strip()
    if not endpoint:
        endpoint = default_ep

    # 4. 输入 API 密钥 / Auth Token
    if auth_field == "auth_token":
        prompt_text = _t("enter_auth_token")
    else:
        prompt_text = _t("enter_api_key")
    flow = ApiKeyFlow(prompt_text=prompt_text)
    try:
        credential = flow.run()
    except ValueError:
        print(_t("api_key_required"), file=sys.stderr)
        raise typer.Exit(1)

    # 5. 输入 model 名 + 新建 env 保存
    was_first_login = is_first_login(manager.settings)
    saved_env_key = _prompt_models_and_create_env(
        manager=manager,
        api_format=api_format,
        format_choice=format_choice,
        endpoint=endpoint,
        auth_field=auth_field,
        credential=credential,
    )

    print(_t("env_saved", env_key=saved_env_key))

    if was_first_login:
        prompt_working_directory(manager.settings)


def _copilot_login(manager: Any) -> None:
    """Copilot 设备码 OAuth 认证流程

    Args:
        manager: AuthManager 实例
    """

    from illusion.auth.copilot import CopilotAuth

    copilot = CopilotAuth()

    # 1. 启动设备码流程
    try:
        flow = copilot.start_device_flow()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)

    # 2. 显示用户码和验证 URL
    print(_t("copilot_open_url"))
    print(f"  {flow['verification_uri']}")
    print(_t("copilot_enter_code", code=flow["user_code"]))
    print()
    print(_t("copilot_waiting"))

    # 3. 轮询等待授权
    try:
        success = copilot.poll_for_token(flow["device_code"])
    except RuntimeError as exc:
        msg = str(exc)
        if "过期" in msg or "expired" in msg.lower():
            print(_t("copilot_device_expired"), file=sys.stderr)
        elif "拒绝" in msg or "denied" in msg.lower():
            print(_t("copilot_auth_denied"), file=sys.stderr)
        elif "订阅" in msg or "subscription" in msg.lower():
            print(_t("copilot_no_subscription"), file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)

    if not success:
        print(_t("copilot_device_expired"), file=sys.stderr)
        raise typer.Exit(1)

    status = copilot.get_status()
    username = status.get("username") or ""
    print(_t("copilot_auth_success", user=username))

    # 4. 输入 model 名 + 新建 env 保存
    was_first_login = is_first_login(manager.settings)
    saved_env_key = _prompt_models_and_create_env(
        manager=manager,
        api_format="copilot",
        format_choice="copilot",
        endpoint=_DEFAULT_ENDPOINTS["copilot"],
        auth_field="api_key",
        credential=None,  # copilot token 由 CopilotAuth 内部管理
        extra_env_fields={"api_key": ""},
    )

    print(_t("env_saved", env_key=saved_env_key))

    if was_first_login:
        prompt_working_directory(manager.settings)


def _codex_login(manager: Any) -> None:
    """Codex OAuth 设备码认证流程

    使用 OpenAI Device Code 流程让用户通过浏览器授权 ChatGPT 账号。

    Args:
        manager: AuthManager 实例
    """
    from illusion.auth.codex_oauth import CodexOAuth

    codex = CodexOAuth()

    # 1. 启动设备码流程
    try:
        flow = codex.start_device_flow()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)

    # 2. 显示用户码和验证 URL
    print(_t("codex_open_url"))
    print(f"  {flow['verification_uri']}")
    print(_t("codex_enter_code", code=flow["user_code"]))
    print()
    print(_t("codex_waiting"))

    # 3. 轮询等待授权
    try:
        success = codex.poll_for_token(flow["device_code"])
    except RuntimeError as exc:
        msg = str(exc)
        if "过期" in msg or "expired" in msg.lower():
            print(_t("codex_device_expired"), file=sys.stderr)
        elif "拒绝" in msg or "denied" in msg.lower():
            print(_t("codex_auth_denied"), file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        raise typer.Exit(1)

    if not success:
        print(_t("codex_device_expired"), file=sys.stderr)
        raise typer.Exit(1)

    status = codex.get_status()
    username = status.get("username") or ""
    print(_t("codex_auth_success", user=username))

    # 4. 输入 model 名 + 新建 env 保存
    was_first_login = is_first_login(manager.settings)
    saved_env_key = _prompt_models_and_create_env(
        manager=manager,
        api_format="codex",
        format_choice="codex",
        endpoint=_DEFAULT_ENDPOINTS["codex"],
        auth_field="api_key",
        credential=None,  # codex token 由 CodexOAuth 内部管理
        extra_env_fields={"api_key": ""},
    )

    print(_t("env_saved", env_key=saved_env_key))

    if was_first_login:
        prompt_working_directory(manager.settings)


@auth_app.command("status")
def auth_status_cmd() -> None:
    """显示所有环境的认证状态"""
    from illusion.auth.manager import AuthManager

    _ensure_language()
    manager = AuthManager()
    statuses = manager.get_env_credential_statuses()

    if not statuses:
        print(_t("no_envs"))
        return

    print(_t("env_status_title"))

    # 列宽
    col_env = 10
    col_format = 12
    col_model = 28
    col_endpoint = 36
    col_cred = 10

    header = (
        f"{_t('col_env'):<{col_env}} "
        f"{_t('col_format'):<{col_format}} "
        f"{_t('col_model'):<{col_model}} "
        f"{_t('col_endpoint'):<{col_endpoint}} "
        f"{_t('col_credential'):<{col_cred}} "
    )
    print(header)
    print("-" * len(header))

    for name, info in statuses.items():
        cred_str = _t("configured") if info["has_credential"] else _t("missing")
        active_str = f" {_t('active_mark')}" if info["active"] else ""
        ep = info["base_url"] or "-"
        print(
            f"{name:<{col_env}} "
            f"{info['api_format']:<{col_format}} "
            f"{info['model']:<{col_model}} "
            f"{ep:<{col_endpoint}} "
            f"{cred_str:<{col_cred}} "
            f"{active_str}"
        )


@auth_app.command("logout")
def auth_logout(
    env_key: str | None = typer.Argument(None, help="Environment to clear (e.g. env_1)"),
) -> None:
    """清除环境的已存储凭据

    Args:
        env_key: 要清除的环境，默认交互式选择
    """
    from illusion.auth.manager import AuthManager

    _ensure_language()
    manager = AuthManager()

    if env_key is None:
        envs = manager.list_envs()
        if not envs:
            print(_t("no_envs"))
            raise typer.Exit(1)
        print(_t("select_env_to_logout"))
        env_keys = list(envs.keys())
        for i, k in enumerate(env_keys, 1):
            print(f"  {i}. {k}")
        raw = typer.prompt(_t("enter_number"), default="1")
        try:
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(env_keys):
                env_key = env_keys[idx]
            else:
                print(_t("invalid_selection"), file=sys.stderr)
                raise typer.Exit(1)
        except ValueError:
            print(_t("invalid_selection"), file=sys.stderr)
            raise typer.Exit(1)

    manager.clear_env_api_key(env_key)
    print(_t("credential_cleared", env_key=env_key))


@auth_app.command("switch")
def auth_switch(
    env_key: str | None = typer.Argument(None, help="Environment to switch to (e.g. env_1)"),
) -> None:
    """切换活动环境

    Args:
        env_key: 要切换的环境，无参数时交互式选择
    """
    from illusion.auth.manager import AuthManager

    _ensure_language()
    manager = AuthManager()

    if env_key is None:
        envs = manager.list_envs()
        if not envs:
            print(_t("no_envs"))
            raise typer.Exit(1)
        print(_t("select_env_to_switch"))
        env_keys = list(envs.keys())
        for i, k in enumerate(env_keys, 1):
            print(f"  {i}. {k}")
        raw = typer.prompt(_t("enter_number"), default="1")
        try:
            idx = int(raw.strip()) - 1
            if 0 <= idx < len(env_keys):
                env_key = env_keys[idx]
            else:
                print(_t("invalid_selection"), file=sys.stderr)
                raise typer.Exit(1)
        except ValueError:
            print(_t("invalid_selection"), file=sys.stderr)
            raise typer.Exit(1)

    try:
        manager.use_env(env_key)
    except ValueError:
        print(_t("env_not_found", env_key=env_key), file=sys.stderr)
        raise typer.Exit(1)
    print(_t("switched_to", env_key=env_key))


@add_app.command("model")
def add_model(
    env_key: str = typer.Argument(None, help="环境键名（如 env_1），省略则交互式选择"),
) -> None:
    """在已有的 env 中添加 model（支持循环输入多个）

    Args:
        env_key: 环境键名，如 env_1；省略则交互式选择
    """
    from illusion.auth.manager import AuthManager
    from illusion.commands.model import _ask_capabilities

    _ensure_language()
    manager = AuthManager()

    envs = manager.list_envs()
    env_keys = list(envs.keys())
    if not env_keys:
        print(_t("no_existing_env"), file=sys.stderr)
        raise typer.Exit(1)

    # 1. 选择 env
    target_env_key: str | None = None
    if env_key:
        if env_key not in env_keys:
            print(_t("env_not_exist", env_key=env_key), file=sys.stderr)
            raise typer.Exit(1)
        target_env_key = env_key
    else:
        print(_t("existing_envs"))
        for i, ek in enumerate(env_keys, 1):
            env_cfg = envs[ek]
            models = env_cfg.list_models()
            model_list = ", ".join(models.values()) if models else _t("no_models")
            print(f"  {i}. {ek} [{env_cfg.api_format}] {env_cfg.base_url} (models: {model_list})")
        raw = typer.prompt(_t("enter_number"), default="1")
        try:
            idx = int(raw.strip())
            if 1 <= idx <= len(env_keys):
                target_env_key = env_keys[idx - 1]
            else:
                print(_t("invalid_selection"), file=sys.stderr)
                raise typer.Exit(1)
        except ValueError:
            print(_t("invalid_selection"), file=sys.stderr)
            raise typer.Exit(1)

    # 2. 循环输入 model 名
    env = envs[target_env_key]
    env_config = env.model_dump(exclude_none=True)
    existing_nums = []
    for k in env_config:
        if k.startswith("model_"):
            try:
                existing_nums.append(int(k.split("_")[1]))
            except (ValueError, IndexError):
                pass
    next_num = max(existing_nums, default=0) + 1

    added_models: list[tuple[str, str]] = []
    while True:
        model_input = input(f"{_t('enter_model')}: ").strip()
        if not model_input:
            print(_t("model_required"), file=sys.stderr)
            continue
        capabilities = _ask_capabilities()
        model_key = f"model_{next_num}"
        env_config[model_key] = {"name": model_input, "capabilities": capabilities}
        added_models.append((model_key, model_input))
        next_num += 1
        # 询问是否继续（直接回车默认退出）
        cont = input(f"{_t('add_another_model')} ").strip().lower()
        if cont != "y":
            break

    if not added_models:
        return

    setattr(manager.settings, target_env_key, env_config)
    manager.save_settings()

    for model_key, model_name in added_models:
        print(_t("model_added", env_key=target_env_key, model_key=model_key, model_name=model_name))
