import json
from types import SimpleNamespace

import pytest

from call.app import call as app_call


@pytest.mark.asyncio
async def test_send_welcome_banner_sends_message(monkeypatch):
    captured_compose = {}

    def fake_compose_welcome_html(**kwargs):
        captured_compose.update(kwargs)
        return "<b>HTML</b>"

    sent_payload = {}

    async def fake_send_telegram_welcome_message(*, text, chat_id, message_thread_id):
        sent_payload.update({
            "text": text,
            "chat_id": chat_id,
            "thread_id": message_thread_id,
        })

    cfg = SimpleNamespace(
        name="FooAgent",
        agent_yaml_path="/tmp/foo.md",
        attributes={},
        model="gpt-4.1-mini",
    )

    monkeypatch.setattr(app_call, "compose_welcome_html", fake_compose_welcome_html, raising=False)
    monkeypatch.setattr(app_call, "send_telegram_welcome_message", fake_send_telegram_welcome_message, raising=False)
    monkeypatch.setattr(app_call, "debug_print", lambda *a, **k: None, raising=False)

    result = await app_call._send_welcome_banner(
        cfg=cfg,
        user_input="hello",
        mcp_servers=["srv-A"],
        selected_chat_id=123,
        selected_thread_id=456,
    )

    assert result == "<b>HTML</b>"
    assert sent_payload == {"text": "<b>HTML</b>", "chat_id": 123, "thread_id": 456}
    assert captured_compose["mcp_servers_started"] == ["srv-A"]
    assert captured_compose["user_input"] == "hello"


@pytest.mark.asyncio
async def test_send_welcome_banner_skips_without_chat(monkeypatch):
    calls = []

    def fake_compose_welcome_html(**kwargs):  # pragma: no cover - should not be called
        calls.append("compose")
        return "HTML"

    async def fake_send(**kwargs):  # pragma: no cover - should not be called
        calls.append("send")

    cfg = SimpleNamespace(name="Foo", agent_yaml_path=None, attributes={}, model=None)

    monkeypatch.setattr(app_call, "compose_welcome_html", fake_compose_welcome_html, raising=False)
    monkeypatch.setattr(app_call, "send_telegram_welcome_message", fake_send, raising=False)
    monkeypatch.setattr(app_call, "debug_print", lambda *a, **k: None, raising=False)

    result = await app_call._send_welcome_banner(
        cfg=cfg,
        user_input="ignored",
        mcp_servers=[],
        selected_chat_id=None,
        selected_thread_id=None,
    )

    assert result is None
    assert calls == []


@pytest.mark.asyncio
async def test_embed_files_in_user_input_adds_base64(monkeypatch):
    calls = []
    content = b"file-bytes"

    class DummyResponse:
        def __init__(self, payload):
            self.status_code = 200
            self.content = payload

    def client_factory():
        class _Client:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

            async def get(self_inner, url):
                calls.append(url)
                return DummyResponse(content)

        return _Client()

    payload = json.dumps({
        "context": [
            {"type": "file", "url": "https://example.com/file.txt"},
            {"type": "text", "value": "skip"},
        ]
    })

    monkeypatch.setattr(app_call, "debug_print", lambda *a, **k: None, raising=False)

    result = await app_call._embed_files_in_user_input(payload, client_factory=client_factory)

    data = json.loads(result)
    assert calls == ["https://example.com/file.txt"]
    assert "base64" in data["context"][0]
    assert data["context"][0]["base64"]


@pytest.mark.asyncio
async def test_embed_files_in_user_input_handles_invalid_json(monkeypatch):
    monkeypatch.setattr(app_call, "debug_print", lambda *a, **k: None, raising=False)
    raw = "not-json"
    assert await app_call._embed_files_in_user_input(raw) == raw

    raw_obj = json.dumps({"context": {"type": "file"}})
    assert await app_call._embed_files_in_user_input(raw_obj) == raw_obj


@pytest.mark.asyncio
async def test_build_and_run_agent_uses_send_welcome_banner(monkeypatch):
    banner_calls = []

    async def fake_send_banner(**kwargs):
        banner_calls.append(kwargs)
        return "HTML"

    monkeypatch.setattr(app_call, "_send_welcome_banner", fake_send_banner, raising=False)
    async def fake_prepare_mcp_servers(astack):
        return [], None

    monkeypatch.setattr(app_call, "_prepare_mcp_servers", fake_prepare_mcp_servers, raising=False)

    async def fake_base_tools(cfg):
        return []

    monkeypatch.setattr(app_call, "_base_tools_for_cfg", fake_base_tools, raising=False)
    monkeypatch.setattr(app_call, "_collect_tool_entries", lambda *_: [], raising=False)
    monkeypatch.setattr(app_call, "resolve_vector_stores", lambda *_: [], raising=False)
    monkeypatch.setattr(app_call, "WebSearchTool", lambda: "web", raising=False)
    monkeypatch.setattr(app_call, "_merge_outputs", lambda *a, **k: {})
    monkeypatch.setattr(app_call, "_extract_tg_targets", lambda *_: (None, None))
    monkeypatch.setattr(app_call, "send_digest_notification", lambda **_: None, raising=False)
    monkeypatch.setattr(app_call, "post_run_git_push", lambda **_: None, raising=False)
    monkeypatch.setattr(app_call, "init_bot", lambda **_: None, raising=False)
    monkeypatch.setattr(app_call, "debug_print", lambda *a, **k: None, raising=False)

    class DummyAgent:
        def __init__(self, *_, **__):
            pass

    monkeypatch.setattr(app_call, "Agent", DummyAgent, raising=False)

    async def fake_run(agent, *_, **__):
        return SimpleNamespace(final_output="ok")

    monkeypatch.setattr(app_call.Runner, "run", staticmethod(fake_run))

    class DummySession:
        def __init__(self, session_id, db_path):
            self.session_id = session_id
            self.db_path = db_path

    monkeypatch.setattr(app_call, "SQLiteSession", DummySession, raising=False)

    cfg = SimpleNamespace(
        name="FooAgent",
        instructions="",
        model="gpt-4.1-mini",
        attributes={},
        agent_yaml_path="/tmp/foo.md",
        project="Proj",
    )

    app_call.selected_chat_id = 111
    app_call.selected_thread_id = 222
    app_call.force_no_session = False

    async with app_call.build_and_run_agent(cfg, user_input="hi"):
        pass

    assert banner_calls, "_send_welcome_banner should be invoked"
    assert banner_calls[0]["cfg"] is cfg


@pytest.mark.asyncio
async def test_build_and_run_agent_uses_embed_helper(monkeypatch):
    embed_calls = []

    async def fake_embed(payload, **kwargs):
        embed_calls.append(payload)
        return payload

    monkeypatch.setattr(app_call, "_embed_files_in_user_input", fake_embed, raising=False)

    async def fake_send_banner(**kwargs):
        return "HTML"

    monkeypatch.setattr(app_call, "_send_welcome_banner", fake_send_banner, raising=False)

    async def fake_prepare_mcp_servers(astack):
        return [], None

    monkeypatch.setattr(app_call, "_prepare_mcp_servers", fake_prepare_mcp_servers, raising=False)

    async def fake_base_tools(cfg):
        return []

    monkeypatch.setattr(app_call, "_base_tools_for_cfg", fake_base_tools, raising=False)
    monkeypatch.setattr(app_call, "_collect_tool_entries", lambda *_: [], raising=False)
    monkeypatch.setattr(app_call, "resolve_vector_stores", lambda *_: [], raising=False)
    monkeypatch.setattr(app_call, "WebSearchTool", lambda: "web", raising=False)
    monkeypatch.setattr(app_call, "_merge_outputs", lambda *a, **k: {})
    monkeypatch.setattr(app_call, "_extract_tg_targets", lambda *_: (None, None))
    monkeypatch.setattr(app_call, "send_digest_notification", lambda **_: None, raising=False)
    monkeypatch.setattr(app_call, "post_run_git_push", lambda **_: None, raising=False)
    monkeypatch.setattr(app_call, "init_bot", lambda **_: None, raising=False)
    monkeypatch.setattr(app_call, "debug_print", lambda *a, **k: None, raising=False)

    class DummyAgent:
        def __init__(self, *_, **__):
            pass

    monkeypatch.setattr(app_call, "Agent", DummyAgent, raising=False)

    async def fake_run(agent, *_, **__):
        return SimpleNamespace(final_output="ok")

    monkeypatch.setattr(app_call.Runner, "run", staticmethod(fake_run))

    class DummySession:
        def __init__(self, session_id, db_path):
            self.session_id = session_id
            self.db_path = db_path

    monkeypatch.setattr(app_call, "SQLiteSession", DummySession, raising=False)

    cfg = SimpleNamespace(
        name="FooAgent",
        instructions="",
        model="gpt-4.1-mini",
        attributes={},
        agent_yaml_path="/tmp/foo.md",
        project="Proj",
    )

    app_call.selected_chat_id = 111
    app_call.selected_thread_id = 222
    app_call.force_no_session = False

    json_input = json.dumps({"context": []})

    async with app_call.build_and_run_agent(cfg, user_input=json_input):
        pass

    assert embed_calls == [json_input]
