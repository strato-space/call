import asyncio
from types import SimpleNamespace

import pytest

from call.app import call as app_call
from call.lib import api as call_api
from call.lib import repo_db as call_repo
from call.mcp import server as mcp_server
from call.actions import main as actions_main


@pytest.fixture(autouse=True)
def reset_mcp_state():
    app_call._reset_mcp_state()
    yield
    app_call._reset_mcp_state()


@pytest.mark.asyncio
async def test_wait_for_mcp_init_returns_when_ready(monkeypatch):
    app_call._MCP_INIT_STATE = app_call._MCPInitState.READY

    started = False

    async def fake_start(tag: str):
        nonlocal started
        started = True

    monkeypatch.setattr(
        app_call, "start_mcp_owner_task", fake_start, raising=False
    )

    await app_call.wait_for_mcp_init(timeout=0.05)

    assert started is False


@pytest.mark.asyncio
async def test_wait_for_mcp_init_autostarts_owner(monkeypatch):
    started = SimpleNamespace(value=None)

    async def fake_start(tag: str):
        started.value = tag
        loop = asyncio.get_running_loop()
        dummy_task = loop.create_task(asyncio.sleep(0))
        app_call._MCP_OWNER_TASK = dummy_task
        app_call._MCP_INIT_STATE = app_call._MCPInitState.IN_PROGRESS
        event = app_call._ensure_mcp_event()
        event.clear()
        app_call._MCP_INIT_STATE = app_call._MCPInitState.READY
        event.set()
        return dummy_task

    monkeypatch.setattr(
        app_call, "start_mcp_owner_task", fake_start, raising=False
    )

    await app_call.wait_for_mcp_init(timeout=0.5)

    assert started.value == "waiter"


@pytest.mark.asyncio
async def test_wait_for_mcp_init_times_out(monkeypatch):
    loop = asyncio.get_running_loop()
    pending = loop.create_future()

    async def fake_start(tag: str):
        app_call._MCP_OWNER_TASK = pending
        event = app_call._ensure_mcp_event()
        event.clear()
        app_call._MCP_INIT_STATE = app_call._MCPInitState.IN_PROGRESS
        return pending

    monkeypatch.setattr(
        app_call, "start_mcp_owner_task", fake_start, raising=False
    )

    with pytest.raises(app_call.MCPInitializationError):
        await app_call.wait_for_mcp_init(timeout=0.05)

    pending.cancel()


@pytest.mark.asyncio
async def test_call_async_waits_for_mcp_init(monkeypatch):
    wait_calls: list[float] = []

    async def fake_wait(timeout: float):
        wait_calls.append(timeout)

    monkeypatch.setattr(app_call, "wait_for_mcp_init", fake_wait, raising=False)
    monkeypatch.setattr(call_repo, "push_event", lambda *a, **k: None, raising=False)

    result = await call_api.call_async(event="digest_ping")

    assert wait_calls == [120.0]
    assert result.get("ok") is True


@pytest.mark.asyncio
async def test_post_run_git_push_no_changes(monkeypatch, tmp_path):
    monkeypatch.delenv("CALL_DISABLE_POST_RUN_PUSH", raising=False)
    monkeypatch.setattr(
        app_call, "discover_prompt_repo", lambda: tmp_path, raising=False
    )

    commands: list[tuple[tuple[str, ...], str | None]] = []

    class DummyProc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            return b"", b""

        def kill(self):
            pass

    async def fake_exec(*cmd, **kwargs):
        env = kwargs.get("env", {})
        commands.append((tuple(cmd), env.get("GIT_TERMINAL_PROMPT")))
        return DummyProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await app_call.post_run_git_push("Agent", "input")

    assert commands == [
        (("git", "status", "--porcelain", "-uno"), "0")
    ]


@pytest.mark.asyncio
async def test_mcp_lifespan_uses_owner(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    async def fake_start(tag: str):
        calls.append(("start", tag))
        return asyncio.get_running_loop().create_task(asyncio.sleep(0))

    async def fake_stop():
        calls.append(("stop", None))

    monkeypatch.setattr(mcp_server, "start_mcp_owner_task", fake_start, raising=False)
    monkeypatch.setattr(mcp_server, "stop_mcp_owner_task", fake_stop, raising=False)

    async with mcp_server.lifespan(None):
        pass

    assert calls == [("start", "mcp"), ("stop", None)]


@pytest.mark.asyncio
async def test_actions_lifespan_uses_owner(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    async def fake_start(tag: str):
        calls.append(("start", tag))
        return asyncio.get_running_loop().create_task(asyncio.sleep(0))

    async def fake_stop():
        calls.append(("stop", None))

    monkeypatch.setattr(
        actions_main, "start_mcp_owner_task", fake_start, raising=False
    )
    monkeypatch.setattr(
        actions_main, "stop_mcp_owner_task", fake_stop, raising=False
    )

    async with actions_main.lifespan(None):
        pass

    assert calls == [("start", "actions"), ("stop", None)]
