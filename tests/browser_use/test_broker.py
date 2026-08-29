"""Broker 测试：TCP 往返、令牌鉴权、subagent 拒绝、generation 校验。"""

from __future__ import annotations

import asyncio
import json

import pytest

from illusion.browser_use.broker import BrowserBroker
from illusion.browser_use.config import BrowserSettings
from illusion.browser_use.protocol import BrowserCommandFailure
from illusion.browser_use.service import BrowserUseService
from illusion.browser_use.session import ManagedBrowser


class _StubBrowser(ManagedBrowser):
    """测试替身：不启动真实 Playwright。"""

    def __init__(self, settings: BrowserSettings) -> None:
        super().__init__(settings)
        self._started = True

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def _make_service() -> BrowserUseService:
    return BrowserUseService(BrowserSettings(enabled=True))
    # 注：真实 BrowserUseService 内部构造 ManagedBrowser —— 测试中仅使用
    # broker 协议层与 descriptor（不触发浏览器启动），execute 走 stub 注入。


async def test_broker_round_trip() -> None:
    """list/execute 请求经 TCP 往返并返回正确载荷。"""
    service = _make_service()
    # 注入 stub 浏览器，避免真实启动
    service._browser = _StubBrowser(service._settings)
    await service.start()
    try:
        endpoint = service._broker.endpoint
        assert endpoint is not None
        host, port = endpoint
        reader, writer = await asyncio.open_connection(host, port)

        async def request(payload: dict[str, object]) -> dict[str, object]:
            writer.write(json.dumps({**payload, "token": service._token}).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
            return json.loads(line.decode())

        listing = await request({"id": "1", "runtimeScope": "main", "op": "list"})
        assert listing["ok"] is True
        assert listing["browsers"][0]["id"] == "cdp"
        assert listing["browsers"][0]["type"] == "cdp"

        result = await request(
            {
                "id": "2",
                "runtimeScope": "main",
                "op": "execute",
                "browserId": "cdp",
                "browserGeneration": service._browser.generation,
                "command": {"method": "list"},
            }
        )
        assert result["ok"] is True
        assert result["result"]["ok"] is True
        writer.close()
    finally:
        await service.stop()


async def test_broker_rejects_bad_token() -> None:
    """令牌错误 → ok=false + unauthorized。"""
    service = _make_service()
    service._browser = _StubBrowser(service._settings)
    await service.start()
    try:
        host, port = service._broker.endpoint  # type: ignore[misc]
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(
            json.dumps({"id": "1", "token": "x" * 40, "runtimeScope": "main", "op": "list"}).encode() + b"\n"
        )
        await writer.drain()
        response = json.loads((await reader.readline()).decode())
        assert response["ok"] is False
        assert response["error"] == "unauthorized"
        writer.close()
    finally:
        await service.stop()


async def test_broker_rejects_subagent_scope() -> None:
    """subagent 作用域拒绝（browser use 仅主代理可用）。"""
    service = _make_service()
    service._browser = _StubBrowser(service._settings)
    await service.start()
    try:
        host, port = service._broker.endpoint  # type: ignore[misc]
        reader, writer = await asyncio.open_connection(host, port)
        writer.write(
            json.dumps(
                {"id": "1", "token": service._token, "runtimeScope": "subagent", "op": "list"}
            ).encode() + b"\n"
        )
        await writer.drain()
        response = json.loads((await reader.readline()).decode())
        assert response["ok"] is False
        assert "subagent" in response["error"]
        writer.close()
    finally:
        await service.stop()


async def test_service_generation_mismatch() -> None:
    """browserGeneration 过期 → 拒绝（内核重置后旧绑定失效，broker 折叠为错误响应）。"""
    service = _make_service()
    service._browser = _StubBrowser(service._settings)
    await service.start()
    try:
        stale_generation = service._browser.generation + 5
        with pytest.raises(BrowserCommandFailure, match="stale"):
            await service._handle_execute("cdp", stale_generation, {"method": "list"})
    finally:
        await service.stop()


async def test_service_descriptor_and_status() -> None:
    """descriptor 携带档案信息；status_summary 不含敏感字段。"""
    service = _make_service()
    service._browser = _StubBrowser(service._settings)
    await service.start()
    try:
        descriptor = service._descriptor()
        assert descriptor["id"] == "cdp"
        assert descriptor["generation"] >= 0
        assert "空白档案" in descriptor["name"]
        summary = service.status_summary()
        assert summary["enabled"] is True
        assert "token" not in json.dumps(summary)
    finally:
        await service.stop()


def test_broker_token_minimum_length() -> None:
    """令牌必须 >= 32 字符（协议约束）。"""
    with pytest.raises(ValueError):
        BrowserBroker("short-token", None, None)  # type: ignore[arg-type]
