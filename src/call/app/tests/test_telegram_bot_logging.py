import asyncio
import importlib
import json
import logging
from types import SimpleNamespace


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

    # Assert: centralized debug_print was invoked with module prefix then tag
    assert logging_calls, "debug_print was not called"
    first = logging_calls[0]
    assert (
        first and first[0] == "[bot]" and first[1] == "[UPDATE]"
    ), f"unexpected debug_print payload: {first}"


def test_bot_log_update_emits_raw_json(monkeypatch, caplog):
    # Arrange
    mod = importlib.import_module("call.telegram_bot.bot")

    class DummyUpdate:
        def __init__(self):
            self.update_id = 7
            self.message = SimpleNamespace(text="hello", caption=None)
            self.effective_chat = SimpleNamespace(id=321)
            self.effective_user = SimpleNamespace(id=654)

        def to_dict(self):
            return {"update_id": self.update_id, "message": {"text": self.message.text}}

    monkeypatch.setenv("CALL_DEBUG", "1")
    monkeypatch.setattr(mod, "Update", DummyUpdate, raising=False)
    caplog.set_level(logging.INFO, logger="call.bot")

    # Act
    asyncio.run(mod._log_update(DummyUpdate(), None))

    # Assert
    expected = json.dumps(
        {"update_id": 7, "message": {"text": "hello"}}, ensure_ascii=False
    )
    # Prefer structured records when available; fall back to captured text stream.
    raw_entries = [
        rec.getMessage()
        for rec in caplog.records
        if rec.getMessage().startswith("Update raw: ")
    ]
    if not raw_entries:
        raw_entries = [
            line
            for line in caplog.text.splitlines()
            if "Update raw: " in line
        ]

    assert raw_entries, "raw update log not emitted"
    assert any(
        msg.strip().endswith(expected)
        for msg in raw_entries
    ), f"unexpected raw entries: {raw_entries!r}"
