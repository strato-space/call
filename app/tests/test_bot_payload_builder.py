import os
import types
import json
import asyncio
import pytest

from types import SimpleNamespace

pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend():
    return "asyncio"


class DummyFile:
    def __init__(self, file_path: str):
        self.file_path = file_path


class DummyBot:
    def __init__(self, file_path: str = "documents/file.bin"):
        self._file_path = file_path

    async def get_file(self, file_id: str):
        return DummyFile(self._file_path)


class DummyMessage:
    def __init__(self, text: str = "", caption: str = "", document: object = None):
        self.text = text
        self.caption = caption
        self.document = document


class DummyUpdate:
    def __init__(self, message):
        self.message = message


class DummyContext:
    def __init__(self, bot):
        self.bot = bot


async def _build_payload(name, main_text, reply_text=None, with_document=False):
    from call.telegram_bot.bot import build_input_payload_from_reply

    # Reply message (text and optional document)
    reply = DummyMessage(
        text=reply_text or "",
        document=(SimpleNamespace(file_id="doc1") if with_document else None),
    )
    # Current message that replies to 'reply'
    cur = DummyMessage(text=main_text or "", document=None)
    cur.reply_to_message = reply
    update = DummyUpdate(cur)
    ctx = DummyContext(DummyBot())
    arg, payload = await build_input_payload_from_reply(
        name, main_text or "", update, ctx
    )
    try:
        parsed = json.loads(arg)
    except Exception:
        parsed = None
    return arg, payload, parsed


async def test_payload_reply_text_only(monkeypatch):
    monkeypatch.setenv("CALL_TELEGRAM_TOKEN", "111:AAA")
    arg, payload, parsed = await _build_payload(
        "AgentX", "hello", reply_text="prev text", with_document=False
    )
    assert isinstance(parsed, dict)
    assert parsed.get("target") == "AgentX"
    assert parsed.get("input") == "hello"  # main text present
    # No context for text-only replies
    assert "context" not in parsed or parsed.get("context") in (None, [])
    # Replay contains reply text
    assert parsed.get("replay") == "prev text"


async def test_payload_reply_with_document(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "222:BBB")
    arg, payload, parsed = await _build_payload(
        "AgentX", "", reply_text="", with_document=True
    )
    assert isinstance(parsed, dict)
    # input falls back to reply text (empty here), so may be missing; context should contain a URL
    ctx = parsed.get("context") or []
    assert ctx and any(it.get("url") for it in ctx)
    # The URL should be a Telegram file download link
    url = next(it.get("url") for it in ctx if it.get("url"))
    assert url.startswith("https://api.telegram.org/file/bot")
