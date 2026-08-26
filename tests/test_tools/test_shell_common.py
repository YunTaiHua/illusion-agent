"""shell_common 共享工具模块单元测试。"""

from __future__ import annotations

import asyncio
import sys

import pytest

from illusion.tools.shell_common import (
    CommandExecutor,
    NormalizedResult,
    OutputNormalizer,
    ShellErrorCode,
)


class TestShellErrorCode:
    def test_constants(self):
        assert ShellErrorCode.SUCCESS == 0
        assert ShellErrorCode.GENERAL_ERROR == 1
        assert ShellErrorCode.COMMAND_NOT_FOUND == 127
        assert ShellErrorCode.TIMEOUT == -1
        assert ShellErrorCode.PERMISSION_DENIED == 126
        assert ShellErrorCode.SIGNAL_BASE == 128


class TestOutputNormalizer:
    def test_decode_utf8(self):
        assert OutputNormalizer.decode_output("你好".encode()) == "你好"

    def test_decode_empty(self):
        assert OutputNormalizer.decode_output(b"") == ""

    def test_decode_utf16le_auto_detect(self):
        data = "PowerShell 输出\n".encode("utf-16-le")
        result = OutputNormalizer.decode_output(data)
        assert "PowerShell 输出" in result

    def test_decode_fallback_replace(self):
        # 无效字节序列，最终 fallback 到 utf-8 replace
        result = OutputNormalizer.decode_output(b"\xff\xfe\x80\x81\x00")
        assert isinstance(result, str)

    def test_format_result_with_stdout(self):
        result = OutputNormalizer.format_result(
            stdout=b"hello world\n",
            stderr=b"",
            return_code=0,
            timed_out=False,
            timeout_seconds=120,
        )
        assert result.output == "hello world"
        assert result.is_error is False
        assert result.return_code == 0

    def test_format_result_with_stdout_and_stderr(self):
        result = OutputNormalizer.format_result(
            stdout=b"out\n",
            stderr=b"err\n",
            return_code=0,
            timed_out=False,
            timeout_seconds=120,
        )
        assert "out" in result.output
        assert "err" in result.output

    def test_format_result_empty_success(self):
        """exit code 0 + 无输出 → 'Command completed successfully' 消息"""
        result = OutputNormalizer.format_result(
            stdout=b"",
            stderr=b"",
            return_code=0,
            timed_out=False,
            timeout_seconds=120,
        )
        assert result.is_error is False
        assert "successfully" in result.output
        assert "Exit code: 0" in result.output
        assert result.return_code == 0

    def test_format_result_empty_failure(self):
        """exit code 非零 + 无输出 → 'Process exited with code N' 消息"""
        result = OutputNormalizer.format_result(
            stdout=b"",
            stderr=b"",
            return_code=1,
            timed_out=False,
            timeout_seconds=120,
        )
        assert result.is_error is True
        assert "exited with code 1" in result.output
        assert "Exit code: 1" in result.output
        assert result.return_code == 1

    def test_format_result_timeout(self):
        """超时 → 超时消息"""
        result = OutputNormalizer.format_result(
            stdout=b"",
            stderr=b"",
            return_code=-1,
            timed_out=True,
            timeout_seconds=30,
        )
        assert result.is_error is True
        assert "timed out after 30s" in result.output
        assert result.return_code == -1

    def test_format_result_truncation(self):
        """超过 30000 字符截断"""
        long_output = b"x" * 31000
        result = OutputNormalizer.format_result(
            stdout=long_output,
            stderr=b"",
            return_code=0,
            timed_out=False,
            timeout_seconds=120,
        )
        assert len(result.output) < 31000
        assert "...[truncated]..." in result.output

    def test_format_result_failure_with_output(self):
        """非零退出码 + 有输出 → 原样返回输出（不附加额外消息）"""
        result = OutputNormalizer.format_result(
            stdout=b"",
            stderr=b"error: something failed\n",
            return_code=1,
            timed_out=False,
            timeout_seconds=120,
        )
        assert result.output == "error: something failed"
        assert result.is_error is True

    def test_normalized_result_is_frozen(self):
        result = NormalizedResult(output="test", is_error=False, return_code=0, metadata={})
        with pytest.raises(AttributeError):
            result.output = "changed"  # type: ignore[misc]


class TestCommandExecutor:
    @pytest.mark.asyncio
    async def test_run_and_normalize_success(self):
        """真实进程：echo 命令"""
        if sys.platform == "win32":
            cmd = ["cmd", "/c", "echo", "executor-test"]
        else:
            cmd = ["echo", "executor-test"]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        result = await CommandExecutor.run_and_normalize(process, timeout=10)
        assert "executor-test" in result.output
        assert result.is_error is False
        assert result.return_code == 0

    @pytest.mark.asyncio
    async def test_run_and_normalize_timeout(self):
        """超时进程被 kill"""
        if sys.platform == "win32":
            # Windows timeout.exe 不响应 process.kill()，使用 Python 子进程
            sleep_cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
        else:
            sleep_cmd = ["sleep", "60"]

        process = await asyncio.create_subprocess_exec(
            *sleep_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        result = await CommandExecutor.run_and_normalize(process, timeout=1)
        assert result.is_error is True
        assert "timed out" in result.output
        assert result.return_code == -1

    @pytest.mark.asyncio
    async def test_run_and_normalize_failure(self):
        """非零退出码"""
        if sys.platform == "win32":
            cmd = ["cmd", "/c", "exit", "1"]
        else:
            cmd = ["false"]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        result = await CommandExecutor.run_and_normalize(process, timeout=10)
        assert result.is_error is True


class TestRunAndNormalizeCancel:
    """run_and_normalize 在 CancelledError 时应 kill 子进程。"""

    @pytest.mark.asyncio
    async def test_cancelled_error_kills_process(self):
        """CancelledError 传播时 terminate_process_tree 被调用。"""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from illusion.tools.shell_common import CommandExecutor

        # 模拟一个长时间运行的子进程
        process = MagicMock()
        process.pid = 12345
        process.wait = AsyncMock()
        process.returncode = None

        # communicate 会被 wait_for 包装，模拟被 cancel
        async def _slow_communicate():
            await asyncio.sleep(100)

        process.communicate = _slow_communicate

        # 创建一个任务来执行 run_and_normalize，然后取消它
        with patch(
            "illusion.tools.shell_common.terminate_process_tree",
            new_callable=AsyncMock,
        ) as mock_terminate:
            task = asyncio.create_task(
                CommandExecutor.run_and_normalize(process, timeout=1000)
            )
            await asyncio.sleep(0.05)  # 让任务进入 wait_for
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            # 验证 terminate_process_tree 被调用（递归杀进程树）
            assert mock_terminate.called, (
                "terminate_process_tree() should be called on CancelledError"
            )


class TestAppendBackgroundTimeoutHint:
    """append_background_timeout_hint：达到工具最大时长限度的超时才追加后台模式提示。"""

    def test_appends_hint_at_max_timeout(self):
        """timeout 达到上限且超时 → 追加后台模式提示"""
        from illusion.tools.shell_common import MAX_TIMEOUT_MS, append_background_timeout_hint

        out = append_background_timeout_hint(
            "Command timed out after 600s",
            timed_out=True,
            timeout_ms=MAX_TIMEOUT_MS,
        )
        assert out.startswith("Command timed out after 600s")
        assert "run_in_background" in out
        assert "no timeout limit" in out

    def test_no_hint_below_max_timeout(self):
        """未达上限的超时（LLM 自设较短超时）→ 不追加，可加大重试"""
        from illusion.tools.shell_common import append_background_timeout_hint

        out = append_background_timeout_hint(
            "Command timed out after 30s", timed_out=True, timeout_ms=30_000,
        )
        assert out == "Command timed out after 30s"

    def test_no_hint_when_not_timed_out(self):
        """非超时路径（正常失败/成功）→ 不追加"""
        from illusion.tools.shell_common import MAX_TIMEOUT_MS, append_background_timeout_hint

        out = append_background_timeout_hint(
            "some output\nExit code: 1", timed_out=False, timeout_ms=MAX_TIMEOUT_MS,
        )
        assert out == "some output\nExit code: 1"
