from unittest.mock import AsyncMock

import anyio
import pytest


pytestmark = pytest.mark.anyio("asyncio")


async def test_reply_to_message_id_is_task_local(monkeypatch):
    """Two concurrent sends must not cross-wire reply_to ids."""
    from call.app import call as app_call

    sent = []

    class DummyBot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)
            return object()

    async def passthrough(op, **_):
        return await op()

    monkeypatch.setattr(app_call, "bot", DummyBot(), raising=False)
    monkeypatch.setattr(app_call, "init_bot", AsyncMock(), raising=False)
    monkeypatch.setattr(app_call, "async_retry", passthrough, raising=False)
    monkeypatch.setattr(app_call, "selected_chat_id", 1, raising=False)
    monkeypatch.setattr(app_call, "selected_thread_id", None, raising=False)
    monkeypatch.setattr(app_call, "reply_to_message_id", None, raising=False)

    async def _send_with_reply(reply_id: int):
        token = app_call.reply_to_message_id_var.set(reply_id)
        try:
            await app_call.telegram_send_message(chat_id=1, text="ok")
        finally:
            app_call.reply_to_message_id_var.reset(token)

    async with anyio.create_task_group() as tg:
        tg.start_soon(_send_with_reply, 101)
        tg.start_soon(_send_with_reply, 202)

    assert len(sent) == 2
    # Python-telegram-bot may use ReplyParameters or reply_to_message_id depending on availability.
    got = []
    for kw in sent:
        if "reply_parameters" in kw and kw["reply_parameters"] is not None:
            got.append(getattr(kw["reply_parameters"], "message_id", None))
        else:
            got.append(kw.get("reply_to_message_id"))
    assert sorted(got) == [101, 202]
