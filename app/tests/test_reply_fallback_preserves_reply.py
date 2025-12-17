from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.anyio("asyncio")


async def test_telegram_send_message_parse_error_fallback_keeps_reply(monkeypatch):
    from telegram.error import BadRequest

    from call.app import call as app_call

    calls = []

    class DummyBot:
        async def send_message(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise BadRequest("can't parse entities")
            return object()

    async def passthrough(op, **_):
        return await op()

    monkeypatch.setattr(app_call, "bot", DummyBot(), raising=False)
    monkeypatch.setattr(app_call, "init_bot", AsyncMock(), raising=False)
    monkeypatch.setattr(app_call, "async_retry", passthrough, raising=False)
    monkeypatch.setattr(app_call, "selected_chat_id", 1, raising=False)
    monkeypatch.setattr(app_call, "selected_thread_id", None, raising=False)
    monkeypatch.setattr(app_call, "reply_to_message_id", None, raising=False)

    token = app_call.reply_to_message_id_var.set(777)
    try:
        await app_call.telegram_send_message(chat_id=1, text="<b>bad</b> <x>")
    finally:
        app_call.reply_to_message_id_var.reset(token)

    assert len(calls) == 2

    # Second attempt should keep reply threading (either ReplyParameters or reply_to_message_id)
    second = calls[1]
    if "reply_parameters" in second and second["reply_parameters"] is not None:
        assert getattr(second["reply_parameters"], "message_id", None) == 777
    else:
        assert second.get("reply_to_message_id") == 777

