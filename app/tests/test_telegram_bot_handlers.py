import json
import asyncio
import types
import pytest

from call.telegram_bot import bot as tg_bot


class FakeCallApi:
    def __init__(self):
        self.last_call = None
        self.last_payload = None

    def build_input_payload(
        self, *, target, main_text, extra_context=None, reply_text=None, download=False
    ):
        payload = {"target": target}
        if main_text:
            payload["input"] = main_text
        s = json.dumps(payload, ensure_ascii=False)
        self.last_payload = payload
        return s, payload

    async def call_async(
        self, *, project, agent, prompt, target, input, echo, chat_id, thread_id
    ):
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
        }
        # Return a simple error envelope so the handler replies
        return {
            "ok": False,
            "error_code": 404,
            "code": "NO_DATA_FOUND",
            "description": "not found",
        }

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
            return {
                "ok": True,
                "resolved": {"project": "UxFab", "name": token, "path": ""},
            }
        return {"ok": False, "error_code": 404, "description": "not found"}

    def list_prompts(self, **kwargs):
        return []

    def reload(self, **kwargs):
        return {"ok": True, "scanned": 0}

    async def clear_session(self, *args, **kwargs):
        return {"ok": True, "cleared": []}


class RecordingCallApi:
    """Minimal stub to capture arguments passed to build_input_payload.

    Used to test Telegram-specific context enrichment in build_input_payload_from_reply
    without depending on the real call.lib.api implementation.
    """

    def __init__(self):
        self.last_payload = None

    def build_input_payload(
        self,
        *,
        target,
        main_text,
        extra_context=None,
        reply_text=None,
        download=False,
    ):
        payload = {}
        if target:
            payload["target"] = target
        if isinstance(reply_text, str) and reply_text.strip():
            payload["replay"] = reply_text.strip()
        if isinstance(main_text, str) and main_text.strip():
            payload["input"] = main_text
        if isinstance(extra_context, list) and extra_context:
            payload["context"] = extra_context
        s = json.dumps(payload, ensure_ascii=False)
        self.last_payload = payload
        return s, payload

    async def call_async(
        self, *, project, agent, prompt, target, input, echo, chat_id, thread_id
    ):
        return {"ok": False, "error_code": 404, "code": "NO_DATA_FOUND"}

    def list(self, **kwargs):
        return []

    def resolve_agent(self, **kwargs):
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
    def __init__(self, id=100, type="group"):
        self.id = id
        self.type = type


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
    # NO_DATA_FOUND responses are now suppressed
    assert upd.message._replies == []


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


def test_plain_group_media_without_caption_is_ignored_for_specialized_bot(monkeypatch):
    """Specialized bots should ignore media-only updates in group chats unless mentioned."""
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    monkeypatch.setattr(tg_bot, "SELECTED_BOT_NAME", "MediaGenBlenderBot", raising=False)
    monkeypatch.setattr(tg_bot, "PROJECT_NAME", "MediaGenBlender", raising=False)

    class DummyVoice:
        file_id = "voice1"

    class DummyVoiceMessage(DummyMessage):
        def __init__(self):
            super().__init__("")
            self.caption = None
            self.voice = DummyVoice()
            self.photo = None
            self.document = None
            self.video = None
            self.audio = None

    upd = DummyUpdate("")
    upd.message = DummyVoiceMessage()
    ctx = DummyContext()

    async def _runner():
        await tg_bot.handle_plain_text(upd, ctx)
        await asyncio.sleep(0.02)

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
    upd = DummyUpdate("@AgentFabBot @Vasil3 run this")
    ctx = DummyContext()

    async def _runner():
        await tg_bot.handle_plain_text(upd, ctx)
        await asyncio.sleep(0.05)

    asyncio.run(_runner())
    assert services.last_call is not None
    assert services.last_call["target"] == "Vasil3"


def test_plain_group_at_target_delegates_to_library(monkeypatch):
    """Test that unknown targets are passed to call_api (no pre-validation)."""
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    # Set bot name so group messages work
    tg_bot.SELECTED_BOT_NAME = "TestBot"
    upd = DummyUpdate("@TestBot @UnknownAgent run")
    ctx = DummyContext()

    async def _runner():
        await tg_bot.handle_plain_text(upd, ctx)
        await asyncio.sleep(0.02)

    asyncio.run(_runner())
    # Target resolution now delegated to call_api - no pre-validation in bot layer
    assert services.last_call is not None
    assert services.last_call["target"] == "UnknownAgent"


def test_normalize_token_strips_trailing_punctuation():
    """Test that _normalize_token removes trailing punctuation from agent names."""
    assert tg_bot._normalize_token("@220-PM-Status!") == "220-PM-Status"
    assert tg_bot._normalize_token("@Agent...") == "Agent"
    assert tg_bot._normalize_token("@Test,") == "Test"
    assert tg_bot._normalize_token("@Name;:!?") == "Name"
    assert tg_bot._normalize_token("@Clean") == "Clean"
    assert tg_bot._normalize_token("Agent!") == "Agent"


def test_handle_call_with_trailing_punctuation_in_agent_name(monkeypatch):
    """Test that /call @Agent! input correctly targets Agent after stripping punctuation."""
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("/call @Vasil3! do work")
    ctx = DummyContext()

    async def _runner():
        await tg_bot.handle_call(upd, ctx)
        await asyncio.sleep(0.01)

    asyncio.run(_runner())
    assert services.last_call is not None
    assert services.last_call["target"] == "Vasil3"
    assert "do work" in (services.last_call["input"] or "")


def test_handle_call_preserves_newlines_in_input(monkeypatch):
    """Test that /call preserves newlines in multiline input."""
    services = FakeCallApi()
    tg_bot.set_services(call_api_module=services)
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    upd = DummyUpdate("/call @Vasil3 line1\nline2\nline3")
    ctx = DummyContext()

    async def _runner():
        await tg_bot.handle_call(upd, ctx)
        await asyncio.sleep(0.01)

    asyncio.run(_runner())
    assert services.last_call is not None
    assert services.last_call["target"] == "Vasil3"
    # Check the payload dict directly (not the JSON string)
    payload = services.last_payload
    assert payload is not None
    input_text = payload.get("input", "")
    assert "line1" in input_text
    assert "line2" in input_text
    assert "line3" in input_text
    # Verify newlines are preserved in the dict
    assert "\n" in input_text


@pytest.mark.asyncio
async def test_build_input_payload_from_reply_with_attachments(monkeypatch):
    """build_input_payload_from_reply should add attachments, raw messages and bot info.

    - Reply message provides text for `replay`.
    - Attachments from reply and current message are converted to resource_link items.
    - Raw telegram_message objects and telegram_bot info are appended to context.
    """

    services = RecordingCallApi()
    tg_bot.set_services(call_api_module=services)

    # Ensure ALLOWED_USERS does not block anything and token is deterministic
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    monkeypatch.setenv("CALL_TELEGRAM_TOKEN", "TEST_TOKEN")
    # Force inclusion of telegram_message and telegram_bot context for this test
    monkeypatch.setattr(tg_bot, "INCLUDE_TELEGRAM_MESSAGE_CONTEXT", True, raising=False)
    monkeypatch.setattr(tg_bot, "INCLUDE_TELEGRAM_BOT_CONTEXT", True, raising=False)

    class DummyPhoto:
        def __init__(self, file_id, width, height):
            self.file_id = file_id
            self.width = width
            self.height = height

    class DummyDocument:
        def __init__(self, file_id, file_name=None, mime_type=None):
            self.file_id = file_id
            self.file_name = file_name
            self.mime_type = mime_type

    class DummyFile:
        def __init__(self, file_path):
            self.file_path = file_path

    class DummyBot:
        async def get_file(self, file_id):
            # Map file_id to a simple deterministic path
            return DummyFile(file_path=f"files/{file_id}")

        async def get_me(self):
            class DummyMe:
                def to_dict(self_inner):
                    return {
                        "id": 1,
                        "is_bot": True,
                        "first_name": "Test",
                        "username": "TestBot",
                    }

            return DummyMe()

    # Reply message with text and a photo
    reply_msg = types.SimpleNamespace(
        text="reply text",
        caption=None,
        chat=types.SimpleNamespace(id=42),
        message_id=10,
        photo=[
            DummyPhoto("p_small", 100, 100),
            DummyPhoto("p_big", 200, 200),
        ],
        document=None,
        video=None,
        voice=None,
        audio=None,
        to_dict=lambda: {"message_id": 10, "text": "reply text"},
    )

    # Current message with main text and a document
    current_msg = types.SimpleNamespace(
        text="current text",
        caption=None,
        chat=types.SimpleNamespace(id=42),
        message_id=11,
        reply_to_message=reply_msg,
        photo=[],
        document=DummyDocument("d1", file_name="doc.txt", mime_type="text/plain"),
        video=None,
        voice=None,
        audio=None,
        to_dict=lambda: {"message_id": 11, "text": "current text"},
    )

    update = types.SimpleNamespace(message=current_msg)

    ctx = DummyContext()
    ctx.bot = DummyBot()

    input_arg, payload = await tg_bot.build_input_payload_from_reply(
        None, "main text", update, ctx
    )

    # We delegate payload construction to RecordingCallApi
    assert isinstance(payload, dict)
    assert payload.get("input") == "main text"
    assert payload.get("replay") is None or payload.get("replay") == "reply text"

    context_items = payload.get("context") or []
    # There should be at least one resource_link, one telegram_message and one telegram_bot
    types_seen = {item.get("type") for item in context_items if isinstance(item, dict)}
    assert "resource_link" in types_seen
    assert "telegram_message" in types_seen
    assert "telegram_bot" in types_seen

    # Verify that at least one resource_link uses the expected Telegram file URL prefix
    rl_items = [i for i in context_items if i.get("type") == "resource_link"]
    assert rl_items, "Expected at least one resource_link item"
    assert any(
        str(it.get("uri", "")).startswith(
            "https://api.telegram.org/file/botTEST_TOKEN/"
        )
        for it in rl_items
    )

    # Verify that telegram_bot info contains our dummy username
    bot_items = [i for i in context_items if i.get("type") == "telegram_bot"]
    assert bot_items, "Expected at least one telegram_bot item"
    assert any(
        isinstance(it.get("bot"), dict) and it["bot"].get("username") == "TestBot"
        for it in bot_items
    )

@pytest.mark.asyncio
async def test_build_input_payload_from_reply_without_telegram_bot_when_flag_disabled(
    monkeypatch,
):
    """When INCLUDE_TELEGRAM_BOT_CONTEXT is False, no telegram_bot items are added."""

    services = RecordingCallApi()
    tg_bot.set_services(call_api_module=services)

    # Ensure ALLOWED_USERS does not block anything and token is deterministic
    monkeypatch.setattr(tg_bot, "ALLOWED_USERS", set(), raising=False)
    monkeypatch.setenv("CALL_TELEGRAM_TOKEN", "TEST_TOKEN")
    # Explicitly disable telegram_bot context; allow telegram_message for this check
    monkeypatch.setattr(tg_bot, "INCLUDE_TELEGRAM_MESSAGE_CONTEXT", True, raising=False)
    monkeypatch.setattr(tg_bot, "INCLUDE_TELEGRAM_BOT_CONTEXT", False, raising=False)

    class DummyFile:
        def __init__(self, file_path):
            self.file_path = file_path

    class DummyBot:
        async def get_file(self, file_id):
            return DummyFile(file_path=f"files/{file_id}")

        async def get_me(self):
            class DummyMe:
                def to_dict(self_inner):
                    return {
                        "id": 1,
                        "is_bot": True,
                        "first_name": "Test",
                        "username": "TestBot",
                    }

            return DummyMe()

    reply_msg = types.SimpleNamespace(
        text="reply text",
        caption=None,
        chat=types.SimpleNamespace(id=42),
        message_id=10,
        photo=[],
        document=None,
        video=None,
        voice=None,
        audio=None,
        to_dict=lambda: {"message_id": 10, "text": "reply text"},
    )

    current_msg = types.SimpleNamespace(
        text="current text",
        caption=None,
        chat=types.SimpleNamespace(id=42),
        message_id=11,
        reply_to_message=reply_msg,
        photo=[],
        document=None,
        video=None,
        voice=None,
        audio=None,
        to_dict=lambda: {"message_id": 11, "text": "current text"},
    )

    update = types.SimpleNamespace(message=current_msg)

    ctx = DummyContext()
    ctx.bot = DummyBot()

    _input_arg, payload = await tg_bot.build_input_payload_from_reply(
        None, "main text", update, ctx
    )

    context_items = payload.get("context") or []
    types_seen = {item.get("type") for item in context_items if isinstance(item, dict)}
    assert "telegram_bot" not in types_seen
