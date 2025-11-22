import asyncio

import pytest


# We import from the app module under test
from call.app.call import send_digest_notification


class DummyMsg:
    def __init__(self, message_id: int = 1):
        self.message_id = message_id
        self.chat_id = 1
        self.message_thread_id = None


def test_send_digest_notification_empty_text_fallback(monkeypatch):
    sent = {}

    def fake_publish_results(**kwargs):  # should not be called in this case
        raise AssertionError("publish_results must not be called for empty text")

    async def fake_send_message(
        *, chat_id, text, reply_markup=None, message_thread_id=None
    ):
        sent["text"] = text
        sent["chat_id"] = chat_id
        sent["reply_markup"] = reply_markup
        sent["message_thread_id"] = message_thread_id
        return DummyMsg()

    monkeypatch.setattr("call.app.call.publish_results", fake_publish_results)
    monkeypatch.setattr("call.app.call.telegram_send_message", fake_send_message)

    async def _run():
        return await send_digest_notification(
            text="   ",
            input_text="hello world",
            agent_name="Test",
            chat_id=123,
            message_thread_id=456,
        )

    msg = asyncio.run(_run())

    assert isinstance(msg, DummyMsg)
    assert sent["text"].startswith("📰")
    assert "hello world" in sent["text"]
    assert sent["reply_markup"] is None


def test_send_digest_notification_chunks_long_plain_text(monkeypatch):
    published = {"called": False}
    sent_chunks: list[str] = []

    def fake_publish_results(title, content):
        # Для простого текста publish_results больше не должен вызываться
        published["called"] = True
        return "https://example.com/digest"

    async def fake_send_message(
        *, chat_id, text, reply_markup=None, message_thread_id=None
    ):
        sent_chunks.append(text)
        return DummyMsg()

    monkeypatch.setattr("call.app.call.publish_results", fake_publish_results)
    monkeypatch.setattr("call.app.call.telegram_send_message", fake_send_message)

    long_text = "x" * 5000

    async def _run():
        return await send_digest_notification(
            text=long_text,
            input_text="inp",
            agent_name="Test",
            chat_id=123,
        )

    _ = asyncio.run(_run())

    # publish_results не должен быть вызван для plain text
    assert published["called"] is False
    # Ожидаем несколько сообщений (батчинг)
    assert len(sent_chunks) >= 2
    # Суммарная длина не меньше исходной (с учётом возможных переводов строк)
    assert sum(len(c) for c in sent_chunks) >= len(long_text)
    # Каждый кусок не превышает телеграмный лимит
    assert all(len(c) <= 4000 for c in sent_chunks)


def test_send_digest_notification_buttons_macro(monkeypatch):
    from telegram import InlineKeyboardMarkup

    published = {"url": None}
    captured = {"reply_markup": None}

    def fake_publish_results(title, content):
        published["url"] = "https://example.com/digest"
        return published["url"]

    async def fake_send_message(
        *, chat_id, text, reply_markup=None, message_thread_id=None
    ):
        captured["reply_markup"] = reply_markup
        return DummyMsg()

    monkeypatch.setattr("call.app.call.publish_results", fake_publish_results)
    monkeypatch.setattr("call.app.call.telegram_send_message", fake_send_message)

    long_text = "y" * 5000

    async def _run():
        return await send_digest_notification(
            text=long_text,
            input_text="inp",
            agent_name="Test",
            buttons=[{"label": "Open", "url": "{{digest_url}}"}],
            chat_id=123,
        )

    _ = asyncio.run(_run())

    rm = captured["reply_markup"]
    assert isinstance(rm, InlineKeyboardMarkup)
    btn = rm.inline_keyboard[0][0]
    assert published["url"] == btn.url


def test_send_digest_notification_multiple_buttons(monkeypatch):
    from telegram import InlineKeyboardMarkup

    captured = {"reply_markup": None}

    async def fake_send_message(
        *, chat_id, text, reply_markup=None, message_thread_id=None
    ):
        captured["reply_markup"] = reply_markup
        return DummyMsg()

    monkeypatch.setattr(
        "call.app.call.publish_results", lambda *a, **k: "https://example.com/digest"
    )
    monkeypatch.setattr("call.app.call.telegram_send_message", fake_send_message)

    buttons = [
        {"label": "Docs", "url": "https://example.com/docs"},
        {"label": "Open", "url": "{{digest_url}}"},
    ]

    async def _run():
        return await send_digest_notification(
            text="z" * 5000,
            agent_name="Test",
            buttons=buttons,
            chat_id=999,
        )

    _ = asyncio.run(_run())

    rm = captured["reply_markup"]
    assert isinstance(rm, InlineKeyboardMarkup)
    rendered = rm.inline_keyboard[0]
    assert rendered[0].text == "Docs"
    assert rendered[0].url == "https://example.com/docs"
    assert rendered[1].text == "Open"
    assert rendered[1].url == "https://example.com/digest"


def test_send_digest_notification_button_rows(monkeypatch):
    from telegram import InlineKeyboardMarkup

    captured = {"reply_markup": None}

    monkeypatch.setattr(
        "call.app.call.publish_results", lambda *a, **k: "https://example.com/digest"
    )

    async def fake_send_message(
        *, chat_id, text, reply_markup=None, message_thread_id=None
    ):
        captured["reply_markup"] = reply_markup
        return DummyMsg()

    monkeypatch.setattr("call.app.call.telegram_send_message", fake_send_message)

    buttons = [
        {"row": [{"label": "Result", "url": "{{digest_url}}"}]},
        {
            "row": [
                {"label": "Agent", "url": "https://example.com/agent"},
                {"label": "Structure", "url": "https://example.com/structure"},
            ]
        },
    ]

    async def _run():
        return await send_digest_notification(
            text="z" * 5000,
            agent_name="Test",
            buttons=buttons,
            chat_id=999,
        )

    _ = asyncio.run(_run())

    rm = captured["reply_markup"]
    assert isinstance(rm, InlineKeyboardMarkup)
    assert len(rm.inline_keyboard) == 2
    first_row = rm.inline_keyboard[0]
    second_row = rm.inline_keyboard[1]
    assert len(first_row) == 1
    assert first_row[0].text == "Result"
    assert first_row[0].url == "https://example.com/digest"
    assert len(second_row) == 2
    assert second_row[0].text == "Agent"
    assert second_row[0].url == "https://example.com/agent"
    assert second_row[1].text == "Structure"
    assert second_row[1].url == "https://example.com/structure"
