import importlib
from pathlib import Path
import pytest

from call.app.call import (
    _load_mcp_yaml_config,
    preinitialize_mcp_servers_async,
    preinitialize_mcp_servers_sync,
)
from call.app import call as call_module
import asyncio


def test_mcp_config_yaml_present_and_parsable():
    call_mod = importlib.import_module("call.app.call")
    cfg_path = Path(__file__).resolve().parents[2] / "mcp_config.yaml"
    assert cfg_path.exists(), "call/mcp_config.yaml should exist"
    data = call_mod._load_mcp_yaml_config(cfg_path)
    assert isinstance(data, dict)
    assert "mcpServers" in data


@pytest.mark.anyio
async def test_preinitialize_mcp_servers_async_uses_singleton(monkeypatch):
    captured_tag = {}

    async def fake_initialize_once():
        captured_tag["called"] = captured_tag.get("called", 0) + 1
        return {"fs": object()}, {"mcpServers": {}}

    monkeypatch.setattr(call_module, "_initialize_mcp_servers_once", fake_initialize_once)

    result = await preinitialize_mcp_servers_async("test-module")

    assert captured_tag["called"] == 1
    assert "fs" in result


def test_preinitialize_mcp_servers_sync_runs_event_loop(monkeypatch):
    call_count = {"count": 0}

    async def fake_initialize_once():
        call_count["count"] += 1
        return {"seq": object()}, {"mcpServers": {}}

    monkeypatch.setattr(call_module, "_initialize_mcp_servers_once", fake_initialize_once)

    result = preinitialize_mcp_servers_sync("cli")

    assert call_count["count"] == 1
    assert "seq" in result
