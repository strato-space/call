import importlib
import os
from typing import Any

import pytest

import call.lib.repo_db as repo_db


@pytest.fixture
def events_repo_db(tmp_path):
    original_path = os.environ.get("EVENT_DB_PATH")
    os.environ["EVENT_DB_PATH"] = str(tmp_path / "events.db")
    reloaded = importlib.reload(repo_db)
    try:
        yield reloaded
    finally:
        if original_path is None:
            os.environ.pop("EVENT_DB_PATH", None)
        else:
            os.environ["EVENT_DB_PATH"] = original_path
        importlib.reload(repo_db)


def test_push_event_persists_json_payload(events_repo_db):
    mod = events_repo_db

    first_id = mod.push_event("test.event", {"foo": "bar"})

    events = mod.iter_events()

    assert events == [
        mod.EventRow(id=first_id, event="test.event", payload={"foo": "bar"})
    ]


def test_push_event_after_id_and_limit(events_repo_db):
    mod = events_repo_db

    first_id = mod.push_event("test.one", {"value": 1})
    second_id = mod.push_event("test.two", {"value": 2})

    events = mod.iter_events(after_id=first_id, limit=1)

    assert events == [
        mod.EventRow(id=second_id, event="test.two", payload={"value": 2})
    ]


def test_push_event_falls_back_to_string(events_repo_db):
    mod = events_repo_db

    class Unserializable:
        def __str__(self) -> str:
            return "not-json"

    payload: Any = Unserializable()

    event_id = mod.push_event("test.weird", payload)

    [event] = mod.iter_events()

    assert event == mod.EventRow(id=event_id, event="test.weird", payload="not-json")
