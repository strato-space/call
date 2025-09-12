import asyncio
from pathlib import Path
import tempfile
import textwrap

import pytest


# We import from the app module under test
from call.app.call import send_digest_notification


class DummyMsg:
    def __init__(self, message_id: int = 1):
        self.message_id = message_id
        self.chat_id = 1
        self.message_thread_id = None


@pytest.mark.asyncio
async def test_send_digest_notification_empty_text_fallback(monkeypatch):
    sent = {}

    def fake_publish_results(**kwargs):  # should not be called in this case
        raise AssertionError("publish_results must not be called for empty text")

    async def fake_send_message(*, chat_id, text, reply_markup=None, message_thread_id=None):
        sent["text"] = text
        sent["chat_id"] = chat_id
        sent["reply_markup"] = reply_markup
        sent["message_thread_id"] = message_thread_id
        return DummyMsg()

    monkeypatch.setattr("call.app.call.publish_results", fake_publish_results)
    monkeypatch.setattr("call.app.call.telegram_send_message", fake_send_message)

    msg = await send_digest_notification(
        text="   ",
        input_text="hello world",
        agent_name="Test",
        chat_id=123,
        message_thread_id=456,
    )

    assert isinstance(msg, DummyMsg)
    assert sent["text"].startswith("📰")
    assert "hello world" in sent["text"]
    assert sent["reply_markup"] is None


@pytest.mark.asyncio
async def test_send_digest_notification_publishes_on_long_text(monkeypatch):
    published = {"url": None}
    sent = {}

    def fake_publish_results(title, content):
        published["url"] = "https://example.com/digest"
        return published["url"]

    async def fake_send_message(*, chat_id, text, reply_markup=None, message_thread_id=None):
        sent["text"] = text
        sent["reply_markup"] = reply_markup
        return DummyMsg()

    monkeypatch.setattr("call.app.call.publish_results", fake_publish_results)
    monkeypatch.setattr("call.app.call.telegram_send_message", fake_send_message)

    long_text = "x" * 5000
    _ = await send_digest_notification(
        text=long_text,
        input_text="inp",
        agent_name="Test",
        chat_id=123,
    )

    assert published["url"] is not None
    assert "https://example.com/digest" in sent["text"]


@pytest.mark.asyncio
async def test_send_digest_notification_buttons_macro(monkeypatch):
    from telegram import InlineKeyboardMarkup

    published = {"url": None}
    captured = {"reply_markup": None}

    def fake_publish_results(title, content):
        published["url"] = "https://example.com/digest"
        return published["url"]

    async def fake_send_message(*, chat_id, text, reply_markup=None, message_thread_id=None):
        captured["reply_markup"] = reply_markup
        return DummyMsg()

    monkeypatch.setattr("call.app.call.publish_results", fake_publish_results)
    monkeypatch.setattr("call.app.call.telegram_send_message", fake_send_message)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        agent_yaml = td_path / "agent.yaml"
        agent_yaml.write_text(textwrap.dedent(
            """
            id: Test
            name: Test
            buttons:
              - label: Open
                url: "{{digest_url}}"
            """
        ), encoding="utf-8")

        long_text = "y" * 5000
        _ = await send_digest_notification(
            text=long_text,
            input_text="inp",
            agent_name="Test",
            agent_path=str(agent_yaml),
            chat_id=123,
        )

        rm = captured["reply_markup"]
        assert isinstance(rm, InlineKeyboardMarkup)
        btn = rm.inline_keyboard[0][0]
        assert published["url"] == btn.url
