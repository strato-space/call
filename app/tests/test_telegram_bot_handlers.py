import json
import asyncio
import types
import pytest

from call.telegram_bot import bot as tg_bot


class FakeCallApi:
    def __init__(self):
        self.last_call = None
        self.last_payload = None

    def build_input_payload(self, *, target, main_text, extra_context=None, reply_text=None, download=False):
        payload = {"target": target}
        if main_text:
            payload["input"] = main_text
        s = json.dumps(payload, ensure_ascii=False)
        self.last_payload = payload
        return s, payload

    async def call_async(self, *, project, agent, prompt, target, input, echo, chat_id, thread_id, merge):
        # Record exactly what was passed
        self.last_call = {
            "project": project,
            "agent": agent,
            "prompt": prompt,
            "target": target,
            "input": input,
            "echo": echo,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "merge": merge,
        }
        # Return a simple error envelope so the handler replies
        return {"ok": False, "error_code": 404, "code": "NO_DATA_FOUND", "description": "not found"}

    def list(self, **kwargs):
        # Minimal project listing to satisfy _is_valid_target(project)
        proj = (kwargs.get("project") or "").strip()
        if proj in {"AgentFab", "UxFab", "FanFab"}:
            return [{"name": proj}]
        return []

    def resolve_agent(self, **kwargs):
        # Accept a few known tokens as valid targets for tests
        agent = (kwargs.get("agent") or "").strip()
        prompt = (kwargs.get("prompt") or "").strip()
        token = agent or prompt
        if token in {"Vasil3", "3-OnlineChunkSummarization", "DialogPostAnalysis"}:
            return {"ok": True, "resolved": {"project": "UxFab", "name": token, "path": ""}}
        return {"ok": False, "error_code": 404, "description": "not found"}

    def list_prompts(self, **kwargs):
        return []

    def reload(self, **kwargs):
        return {"ok": True, "scanned": 0}

    async def clear_session(self, *args, **kwargs):
        return {"ok": True, "cleared": []}


class DummyMessage:
    def __init__(self, text):
        self.text = text
        self.message_thread_id = 0
        self.message_id = 123
        self._replies = []

    async def reply_text(self, text, parse_mode=None):
        self._replies.append((text, parse_mode))
        return types.SimpleNamespace(message_id=456)


class DummyChat:
    def __init__(self, id=100):
        self.id = id


class DummyUpdate:
    def __init__(self, text):
        self.message = DummyMessage(text)
        self.effective_chat = DummyChat(100)
        self.effective_user = types.SimpleNamespace(id=999)


class DummyContext:
    def __init__(self):
        self.bot = types.SimpleNamespace()


def test_handle_call_prompt_token_calls_api_with_target(monkeypatch):
    # Arrange
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    # Ensure auth decorator lets the test through
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("/call @3-OnlineChunkSummarization")
    ctx = DummyContext()

    # Act
    async def _runner():
        await tg_bot.handle_call(upd, ctx)
        await asyncio.sleep(0.01)
    asyncio.run(_runner())

    # Assert
    assert services.last_call is not None
    assert services.last_call["target"] == "3-OnlineChunkSummarization"
    # Bot should have replied with a concise error envelope string
    replies = upd.message._replies
    assert replies and replies[0][0].startswith("Error:")


def test_handle_call_agent_token_calls_api_with_target(monkeypatch):
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("/call @Vasil3 some text")
    ctx = DummyContext()

    async def _runner():
        await tg_bot.handle_call(upd, ctx)
        await asyncio.sleep(0.01)
    asyncio.run(_runner())

    assert services.last_call is not None
    assert services.last_call["target"] == "Vasil3"


def test_handle_call_project_plus_prompt_calls_api_with_target(monkeypatch):
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("/call @AgentFab @3-OnlineChunkSummarization")
    ctx = DummyContext()

    async def _runner():
        await tg_bot.handle_call(upd, ctx)
        await asyncio.sleep(0.01)
    asyncio.run(_runner())

    assert services.last_call is not None
    assert services.last_call["target"] == "AgentFab"


def test_plain_private_input_only_calls_api_with_no_target(monkeypatch):
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("just text")
    upd.effective_chat.type = "private"
    ctx = DummyContext()
    async def _runner():
        await tg_bot.handle_plain_text(upd, ctx)
        await asyncio.sleep(0.01)
    asyncio.run(_runner())
    assert services.last_call is not None
    assert (services.last_call["target"] or "") == ""
    assert "just text" in (services.last_call["input"] or "")


def test_plain_private_at_target_valid_calls_api_with_target(monkeypatch):
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("@Vasil3 do work")
    upd.effective_chat.type = "private"
    ctx = DummyContext()
    async def _runner():
        await tg_bot.handle_plain_text(upd, ctx)
        await asyncio.sleep(0.01)
    asyncio.run(_runner())
    assert services.last_call is not None
    assert services.last_call["target"] == "Vasil3"


def test_plain_group_plain_text_is_ignored(monkeypatch):
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("hello everyone")
    # No type => not private => group-like
    ctx = DummyContext()
    async def _runner():
        await tg_bot.handle_plain_text(upd, ctx)
        await asyncio.sleep(0.01)
    asyncio.run(_runner())
    assert services.last_call is None


def test_plain_group_single_at_means_input_only(monkeypatch):
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("@ just input only")
    ctx = DummyContext()
    async def _runner():
        await tg_bot.handle_plain_text(upd, ctx)
        await asyncio.sleep(0.01)
    asyncio.run(_runner())
    assert services.last_call is not None
    assert (services.last_call["target"] or "") == ""
    assert "just input only" in (services.last_call["input"] or "")


def test_plain_group_atbot_target_valid(monkeypatch):
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    # Pretend the bot name derived project is AgentFab
    tg_bot.SELECTED_BOT_NAME = "AgentFabBot"
    tg_bot.PROJECT_NAME = "AgentFab"
    upd = DummyUpdate("@AgentFabBot Vasil3 run this")
    ctx = DummyContext()
    async def _runner():
        await tg_bot.handle_plain_text(upd, ctx)
        await asyncio.sleep(0.05)
    asyncio.run(_runner())
    assert services.last_call is not None
    assert services.last_call["target"] == "Vasil3"


def test_plain_group_at_target_invalid_is_ignored(monkeypatch):
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("@UnknownAgent run")
    ctx = DummyContext()
    async def _runner():
        await tg_bot.handle_plain_text(upd, ctx)
        await asyncio.sleep(0.02)
    asyncio.run(_runner())
    assert services.last_call is None
