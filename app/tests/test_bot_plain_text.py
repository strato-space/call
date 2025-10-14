import types
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_handle_plain_text_no_nameerror(monkeypatch):
    """
    Ensure handle_plain_text does not raise when deriving bot base from _get_bot_project.
    We force should_handle=False to avoid scheduling background tasks.
    """
    import importlib

    bot = importlib.import_module("call.telegram_bot.bot")

    # Ensure auth wrapper does not access update.effective_user
    monkeypatch.setattr(bot, "ALLOWED_USERS", set())

    # Return a valid base project and avoid scheduling a call
    monkeypatch.setattr(bot, "_get_bot_project", lambda update: "AgentFab")
    monkeypatch.setattr(
        bot, "_resolve_agent_and_input", lambda text, base, is_private: ("", "", False)
    )

    class _Msg:
        text = "@Name hi"
        message_thread_id = 1

    class _Chat:
        id = 123
        type = "private"

    class _User:
        id = 42

    class _Update:
        message = _Msg()
        effective_chat = _Chat()
        effective_user = _User()

    ctx = types.SimpleNamespace(application=types.SimpleNamespace())

    # Should not raise (regression for NameError: _get_bot_base)
    await bot.handle_plain_text(_Update(), ctx)
