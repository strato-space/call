import pytest


def test_create_session_is_unique_for_same_chat_thread(monkeypatch):
    from call.app import call as app_call

    class DummySession:
        def __init__(self, session_id, db_path):
            self.id = session_id
            self.session_id = session_id
            self.db_path = db_path

    monkeypatch.setattr(app_call, "SQLiteSession", DummySession, raising=False)
    monkeypatch.setenv("CALL_DB", ".cache/call/test.db")

    s1 = app_call._create_session_if_any(123, None)
    s2 = app_call._create_session_if_any(123, None)
    assert s1 is not None and s2 is not None
    assert s1.session_id != s2.session_id

