import asyncio

import builtins


def test_bot_log_update_uses_central_debug_print(monkeypatch):
    # Arrange: import module and patch debug_print
    import importlib
    logging_calls = []

    def _recorder(*parts):
        logging_calls.append(parts)
    # Ensure debug output is enabled regardless of runner env
    monkeypatch.setenv("CALL_DEBUG", "1")

    # Build a minimal Update-like object
    class _Msg:
        def __init__(self, text):
            self.text = text
            self.caption = None

    class _Chat:
        def __init__(self, id):
            self.id = id

    class _Update:
        def __init__(self):
            self.update_id = 42
            self.message = _Msg("hello from test")
            self.effective_chat = _Chat(123)
            self.effective_user = type("_U", (), {"id": 999})()

    mod = importlib.import_module("call.telegram_bot.bot")
    # Patch the module-level reference that the bot imported
    monkeypatch.setattr(mod, "debug_print", _recorder, raising=True)

    # Act: call the async logger
    asyncio.run(mod._log_update(_Update(), None))

    # Assert: centralized debug_print was invoked with an UPDATE entry
    assert logging_calls, "debug_print was not called"
    first = logging_calls[0]
    assert first and first[0] == "[UPDATE]", f"unexpected debug_print payload: {first}"
