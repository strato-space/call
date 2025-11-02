import importlib
from pathlib import Path
import pytest

from call.app.call import (
    _load_mcp_yaml_config,
    preinitialize_mcp_servers_async,
    preinitialize_mcp_servers_sync,
    _validate_and_cache_mcp_config,
    _prepare_mcp_servers,
    MCPInitializationError,
)
from call.app import call as call_module
import asyncio
import os
import anyio


@pytest.fixture(autouse=True)
def _reset_state():
    call_module._reset_mcp_state()
    yield
    call_module._reset_mcp_state()


def test_mcp_config_yaml_present_and_parsable():
    call_mod = importlib.import_module("call.app.call")
    cfg_path = Path(__file__).resolve().parents[2] / "mcp_config.yaml"
    assert cfg_path.exists(), "call/mcp_config.yaml should exist"
    data = call_mod._load_mcp_yaml_config(cfg_path)
    assert isinstance(data, dict)
    assert "mcpServers" in data


@pytest.mark.anyio
async def test_preinitialize_mcp_servers_async_validates_config(monkeypatch):
    captured_tag = {}

    async def fake_initialize_once():
        captured_tag["called"] = captured_tag.get("called", 0) + 1
        # Return empty servers dict, config with enabled servers
        return {}, {"mcpServers": {"fs": {"enabled": True}}}

    monkeypatch.setattr(call_module, "_validate_and_cache_mcp_config", fake_initialize_once)

    result = await preinitialize_mcp_servers_async("test-module")

    assert captured_tag["called"] == 1
    # preinitialize now returns empty dict - servers are created fresh per call
    assert result == {}


def test_preinitialize_mcp_servers_sync_runs_event_loop(monkeypatch):
    call_count = {"count": 0}

    async def fake_initialize_once():
        call_count["count"] += 1
        return {}, {"mcpServers": {"seq": {"enabled": True}}}

    monkeypatch.setattr(call_module, "_validate_and_cache_mcp_config", fake_initialize_once)

    result = preinitialize_mcp_servers_sync("test-module")
    assert call_count["count"] == 1
    # preinitialize now returns empty dict - servers are created fresh per call
    assert result == {}


@pytest.mark.anyio
async def test_initialize_requires_enable_flag(monkeypatch, tmp_path):
    config_path = tmp_path / "mcp.yaml"
    config_path.write_text("mcpServers:\n  fake: {}\n", encoding="utf-8")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ENABLE_MCP", "0")

    with pytest.raises(MCPInitializationError) as exc:
        await _validate_and_cache_mcp_config()
    assert "ENABLE_MCP" in str(exc.value)


@pytest.mark.anyio
async def test_initialize_success_and_prepare_reuse(monkeypatch, tmp_path):
    config_path = tmp_path / "mcp.yaml"
    # Make server enabled so config validation passes
    config_path.write_text("mcpServers:\n  fake: {enabled: true}\n", encoding="utf-8")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ENABLE_MCP", "1")

    servers_first, cfg_first = await _validate_and_cache_mcp_config()
    # Servers dict is empty - only config is cached
    assert servers_first == {}
    assert cfg_first["mcpServers"]
    assert "fake" in cfg_first["mcpServers"]

    servers_second, cfg_second = await _validate_and_cache_mcp_config()
    assert servers_second == servers_first
    assert cfg_second == cfg_first

    # _prepare_mcp_servers requires AsyncExitStack - test in integration tests


@pytest.mark.anyio
async def test_initialize_fails_on_no_enabled_servers(monkeypatch, tmp_path):
    config_path = tmp_path / "mcp.yaml"
    # Server exists but not enabled
    config_path.write_text("mcpServers:\n  fake: {enabled: false}\n", encoding="utf-8")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("ENABLE_MCP", "1")

    with pytest.raises(MCPInitializationError) as exc:
        await _validate_and_cache_mcp_config()
    assert "No enabled MCP servers" in str(exc.value)


@pytest.mark.anyio
async def test_prepare_respects_failed_state(monkeypatch):
    error = MCPInitializationError("boom")
    call_module._MCP_INIT_STATE = call_module._MCPInitState.FAILED
    call_module._MCP_INIT_ERROR = error

    with pytest.raises(MCPInitializationError) as exc:
        await _prepare_mcp_servers(None)
    assert exc.value is error
