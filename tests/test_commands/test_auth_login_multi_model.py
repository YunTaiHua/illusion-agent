"""auth login 多 model 输入与 add model 测试"""

from unittest.mock import MagicMock, patch

import pytest

from illusion.cli.auth import _prompt_models_and_create_env


def _make_env_config(api_format="anthropic", base_url="https://api.anthropic.com", models=None):
    """构造模拟 EnvConfig 对象"""
    env = MagicMock()
    env.api_format = api_format
    env.base_url = base_url
    models = models or {}
    env.list_models.return_value = models
    base: dict = {"api_format": api_format, "base_url": base_url}
    for i, m in enumerate(models.values()):
        base[f"model_{i + 1}"] = {"name": m, "capabilities": []}
    env.model_dump.return_value = base
    return env


def _make_manager(envs=None):
    """构造模拟 AuthManager"""
    manager = MagicMock()
    envs = envs or {}
    manager.list_envs.return_value = envs
    manager.settings = MagicMock()
    return manager


def test_multiple_models_new_env_no_existing():
    """无已有 env 时，循环输入多个 model 并创建 env_1"""
    manager = _make_manager(envs={})
    inputs = ["claude-sonnet-4-6", "y", "y", "claude-opus-4", "", "n"]
    with patch("builtins.input", side_effect=inputs):
        result = _prompt_models_and_create_env(
            manager=manager,
            api_format="anthropic",
            format_choice="anthropic",
            endpoint="https://api.anthropic.com",
            auth_field="api_key",
            credential="sk-test",
        )
    assert result == "env_1"
    env_config = manager.settings.env_1
    assert isinstance(env_config, dict)
    # 模型以对象格式写入：勾选图片能力的与未勾选的并列
    assert env_config["model_1"] == {"name": "claude-sonnet-4-6", "capabilities": ["image"]}
    assert env_config["model_2"] == {"name": "claude-opus-4", "capabilities": []}
    assert manager.settings.model == "env_1.model_1"
    manager.save_settings.assert_called_once()


def test_multiple_models_new_env_with_existing():
    """已有 env_1 时，新建 env_2（auth login 始终新建，不询问选择）"""
    existing_env = _make_env_config(models={"model_1": "existing-model"})
    manager = _make_manager(envs={"env_1": existing_env})
    inputs = ["claude-sonnet-4-6", "n", "y", "claude-opus-4", "n", "n"]
    with patch("builtins.input", side_effect=inputs):
        result = _prompt_models_and_create_env(
            manager=manager,
            api_format="anthropic",
            format_choice="anthropic",
            endpoint="https://api.anthropic.com",
            auth_field="api_key",
            credential="sk-new",
        )
    assert result == "env_2"
    env_config = manager.settings.env_2
    assert isinstance(env_config, dict)
    assert env_config["model_1"]["name"] == "claude-sonnet-4-6"
    assert env_config["model_2"]["name"] == "claude-opus-4"
    assert manager.settings.model == "env_2.model_1"


def test_default_enter_exits_loop():
    """回车默认退出 model 循环"""
    manager = _make_manager(envs={})
    inputs = ["claude-sonnet-4-6", "n", ""]
    with patch("builtins.input", side_effect=inputs):
        result = _prompt_models_and_create_env(
            manager=manager,
            api_format="anthropic",
            format_choice="anthropic",
            endpoint="https://api.anthropic.com",
            auth_field="api_key",
            credential="sk-test",
        )
    assert result == "env_1"
    env_config = manager.settings.env_1
    assert isinstance(env_config, dict)
    assert "model_1" in env_config
    assert "model_2" not in env_config


def test_credential_stored_for_new_env():
    """新建 env 时存储凭据"""
    manager = _make_manager(envs={})
    inputs = ["claude-sonnet-4-6", "n", "n"]
    with (
        patch("builtins.input", side_effect=inputs),
        patch("illusion.auth.storage.store_env_credential") as mock_store,
    ):
        _prompt_models_and_create_env(
            manager=manager,
            api_format="anthropic",
            format_choice="anthropic",
            endpoint="https://api.anthropic.com",
            auth_field="api_key",
            credential="sk-test",
        )
    mock_store.assert_called_once_with("env_1", "api_key", "sk-test")


def test_credential_none_skips_storage():
    """credential 为 None 时跳过凭据存储（copilot/codex）"""
    manager = _make_manager(envs={})
    inputs = ["gpt-4o", "n", "n"]
    with (
        patch("builtins.input", side_effect=inputs),
        patch("illusion.auth.storage.store_env_credential") as mock_store,
    ):
        _prompt_models_and_create_env(
            manager=manager,
            api_format="copilot",
            format_choice="copilot",
            endpoint="https://api.githubcopilot.com",
            auth_field="api_key",
            credential=None,
            extra_env_fields={"api_key": ""},
        )
    mock_store.assert_not_called()
    env_config = manager.settings.env_1
    assert env_config["api_key"] == ""


def test_default_model_used_when_empty():
    """有默认 model 时，空输入使用默认 model"""
    manager = _make_manager(envs={})
    inputs = ["", "n", "n"]  # 空输入 → 使用默认 model
    with patch("builtins.input", side_effect=inputs):
        result = _prompt_models_and_create_env(
            manager=manager,
            api_format="anthropic",
            format_choice="anthropic",
            endpoint="https://api.anthropic.com",
            auth_field="api_key",
            credential="sk-test",
        )
    assert result == "env_1"
    env_config = manager.settings.env_1
    # anthropic 默认 model 来自 _DEFAULT_MODELS
    assert env_config["model_1"]  # 非空


# ---- add model 测试 ----


def test_add_model_to_existing_env_interactive():
    """add model 交互式选择 env 并添加多个 model"""
    from illusion.cli.auth import add_model

    existing_env = _make_env_config(models={"model_1": "claude-sonnet-4-6"})
    manager = _make_manager(envs={"env_1": existing_env})

    inputs = ["claude-opus-4", "n", "y", "claude-haiku", "n", "n"]
    with (
        patch("builtins.input", side_effect=inputs),
        patch("illusion.auth.manager.AuthManager", return_value=manager),
        patch("illusion.cli.auth._ensure_language"),
        patch("illusion.cli.typer.prompt", return_value="1") as mock_prompt,
    ):
        try:
            add_model(env_key=None)  # type: ignore
        except SystemExit:
            pass

    mock_prompt.assert_called_once()
    env_config = manager.settings.env_1
    assert env_config["model_1"]["name"] == "claude-sonnet-4-6"
    assert env_config["model_2"]["name"] == "claude-opus-4"
    assert env_config["model_3"]["name"] == "claude-haiku"
    manager.save_settings.assert_called_once()


def test_add_model_with_env_key_arg():
    """add model env_1 直接指定 env"""
    from illusion.cli.auth import add_model

    existing_env = _make_env_config(models={"model_1": "existing-model"})
    manager = _make_manager(envs={"env_1": existing_env})

    inputs = ["new-model", "y", "n"]
    with (
        patch("builtins.input", side_effect=inputs),
        patch("illusion.auth.manager.AuthManager", return_value=manager),
        patch("illusion.cli.auth._ensure_language"),
    ):
        try:
            add_model(env_key="env_1")  # type: ignore
        except SystemExit:
            pass

    env_config = manager.settings.env_1
    assert env_config["model_1"]["name"] == "existing-model"
    assert env_config["model_2"] == {"name": "new-model", "capabilities": ["image"]}


def test_add_model_env_not_exist():
    """add model 指定不存在的 env 时报错"""
    import typer

    from illusion.cli.auth import add_model

    manager = _make_manager(envs={})
    with (
        patch("illusion.auth.manager.AuthManager", return_value=manager),
        patch("illusion.cli.auth._ensure_language"),
        pytest.raises(typer.Exit),
    ):
        add_model(env_key="env_999")  # type: ignore


def test_add_model_no_existing_env():
    """无已有 env 时报错"""
    import typer

    from illusion.cli.auth import add_model

    manager = _make_manager(envs={})
    with (
        patch("illusion.auth.manager.AuthManager", return_value=manager),
        patch("illusion.cli.auth._ensure_language"),
        pytest.raises(typer.Exit),
    ):
        add_model(env_key=None)  # type: ignore


# ---- 首次登录工作目录引导测试 ----


def test_auth_login_first_time_prompts_working_dir(tmp_path, monkeypatch):
    """首次登录时应触发工作目录提示"""
    from unittest.mock import patch, MagicMock

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}")
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_dir))

    # mock CopilotAuth 以避免真实 OAuth（_copilot_login 内部 from illusion.auth.copilot import CopilotAuth）
    # 直接调用 _copilot_login(mock_manager) 传入 mock manager，无需 mock AuthManager
    with patch("illusion.auth.copilot.CopilotAuth") as mock_copilot_cls:
        mock_copilot = MagicMock()
        mock_copilot.start_device_flow.return_value = {
            "device_code": "test",
            "user_code": "ABCD1234",
            "verification_uri": "https://github.com/login/device",
        }
        mock_copilot.poll_for_token.return_value = True
        mock_copilot.get_status.return_value = {"username": "testuser"}
        mock_copilot_cls.return_value = mock_copilot

        mock_manager = MagicMock()
        mock_manager.settings.ui_language = "zh-CN"
        mock_manager.settings.list_envs.return_value = {}
        mock_manager.settings.working_directory = None

        with patch("illusion.cli.auth._prompt_models_and_create_env", return_value="env_1"), \
             patch("illusion.cli.auth.is_first_login", return_value=True), \
             patch("illusion.cli.auth.prompt_working_directory") as mock_prompt:
            # 触发 copilot 登录（走 _copilot_login 路径）
            from illusion.cli.auth import _copilot_login
            _copilot_login(mock_manager)
            mock_prompt.assert_called_once_with(mock_manager.settings)


def test_auth_login_not_first_time_skips_prompt(tmp_path, monkeypatch):
    """非首次登录时不触发工作目录提示"""
    from unittest.mock import patch, MagicMock

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}")
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_dir))

    with patch("illusion.auth.copilot.CopilotAuth") as mock_copilot_cls:
        mock_copilot = MagicMock()
        mock_copilot.start_device_flow.return_value = {
            "device_code": "test",
            "user_code": "ABCD1234",
            "verification_uri": "https://github.com/login/device",
        }
        mock_copilot.poll_for_token.return_value = True
        mock_copilot.get_status.return_value = {"username": "testuser"}
        mock_copilot_cls.return_value = mock_copilot

        mock_manager = MagicMock()
        mock_manager.settings.ui_language = "zh-CN"

        with patch("illusion.cli.auth._prompt_models_and_create_env", return_value="env_2"), \
             patch("illusion.cli.auth.is_first_login", return_value=False), \
             patch("illusion.cli.auth.prompt_working_directory") as mock_prompt:
            from illusion.cli.auth import _copilot_login
            _copilot_login(mock_manager)
            mock_prompt.assert_not_called()


def test_auth_login_working_dir_skip_on_enter(tmp_path, monkeypatch):
    """首次登录但用户回车跳过，settings.working_directory 仍为 None"""
    from illusion.cli.workspace import prompt_working_directory
    from illusion.config.settings import Settings

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text("{}")
    monkeypatch.setenv("ILLUSION_CONFIG_DIR", str(config_dir))

    settings = Settings()
    # 模拟用户回车（空输入）
    monkeypatch.setattr("builtins.input", lambda _: "")
    prompt_working_directory(settings)
    assert settings.working_directory is None
