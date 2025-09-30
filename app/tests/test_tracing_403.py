import pytest

from types import SimpleNamespace

from call.lib import api as call_api
from call.app import call as app_call


@pytest.mark.asyncio
async def test_call_async_tracing_403_error_json(monkeypatch):
    cfg = SimpleNamespace(
        id="FakeAgent",
        type="agent",
        project="Proj",
        agent="FakeAgent",
        prompt=None,
        path="agent/Proj/FakeAgent/agent.md",
        url=None,
        goal=None,
        instructions="Test instructions",
        model="gpt-4.1-mini",
        attributes={},
    )
    cfg.input = None
    cfg.tools = []

    def fake_build_config(**kwargs):
        return cfg, None

    monkeypatch.setattr(call_api, "build_runnable_instructions_config", fake_build_config, raising=False)

    async def fake_prepare_mcp(astack):
        return [], None

    async def fake_build_tools(_cfg):
        return []

    monkeypatch.setattr(app_call, "_prepare_mcp_servers", fake_prepare_mcp, raising=False)
    monkeypatch.setattr(app_call, "build_tools_for_cfg", fake_build_tools, raising=False)
    monkeypatch.setattr(app_call, "_collect_tools", lambda *_: [], raising=False)

    async def fake_git_pull():
        return None

    monkeypatch.setattr(app_call, "_git_pull_prompt_repo", fake_git_pull, raising=False)

    async def fake_send_banner(**kwargs):
        return ""

    monkeypatch.setattr(app_call, "_send_welcome_banner", fake_send_banner, raising=False)

    async def fake_embed(payload, **kwargs):
        return payload

    monkeypatch.setattr(app_call, "_embed_files_in_user_input", fake_embed, raising=False)

    async def fake_init_bot_safe(**kwargs):
        return None

    async def fake_init_bot(**kwargs):
        return None

    monkeypatch.setattr(app_call, "_init_bot_safe", fake_init_bot_safe, raising=False)
    monkeypatch.setattr(app_call, "init_bot", fake_init_bot, raising=False)

    digest_calls: list = []
    error_calls: list = []

    async def fake_digest(**kwargs):
        digest_calls.append(kwargs)

    async def fake_error(**kwargs):
        error_calls.append(kwargs)

    monkeypatch.setattr(app_call, "_notify_digest_if_applicable", fake_digest, raising=False)
    monkeypatch.setattr(app_call, "_send_error_notification", fake_error, raising=False)

    class DummyAgent:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(app_call, "Agent", DummyAgent, raising=False)

    class DummySession:
        def __init__(self):
            self.id = 123

    monkeypatch.setattr(app_call, "_create_session_if_any", lambda *_: DummySession(), raising=False)

    async def fatal_run(*args, **kwargs):
        raise RuntimeError("request_forbidden: blocked by tracing")

    monkeypatch.setattr(app_call.Runner, "run", staticmethod(fatal_run))
    monkeypatch.setattr(app_call, "debug_print", lambda *a, **k: None, raising=False)

    app_call.selected_chat_id = None
    app_call.selected_thread_id = None
    app_call.force_no_session = False

    result = await call_api.call_async(project="Proj", agent="FakeAgent", input="Hello")

    assert result.get("ok") is False
    assert result.get("error_code") == 403
    assert result.get("code") == "REQUEST_FORBIDDEN"
    details = result.get("details") or {}
    assert isinstance(details, dict)
    inner_error = details.get("error") or {}
    assert isinstance(inner_error, dict)
    assert inner_error.get("type") == "request_forbidden"
    assert not digest_calls
    assert not error_calls
