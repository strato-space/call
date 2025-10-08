from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def actions_env(monkeypatch, tmp_path):
    db_path = tmp_path / "repo.db"
    card_path = tmp_path / "cards" / "demo.md"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("API_ACCESS_TOKEN", "TEST_TOKEN")

    repo_db = importlib.import_module("call.lib.repo_db")
    api_module = importlib.import_module("call.lib.api")
    actions_main = importlib.import_module("call.actions.main")
    deps_module = importlib.import_module("call.actions.deps")
    monkeypatch.setattr(repo_db, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(api_module.call_repo, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(actions_main.repo_db_module, "DB_PATH", str(db_path), raising=False)

    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text("initial", encoding="utf-8")

    conn = repo_db._ensure_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO repo (target, project, agent, prompt, path, state, engine, orchestration, type, rel_path, url, goal, card) "
            "VALUES (?, ?, ?, ?, ?, '', '', '', '', '', '', '', ?)",
            ("DemoCard", "", "", "", str(card_path), "initial"),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "actions::cards")
    deps_module._API_ACCESS_TOKEN = None  # type: ignore[attr-defined]

    client = TestClient(actions_main.app)
    try:
        yield client, repo_db, api_module, card_path
    finally:
        client.close()
        deps_module._API_ACCESS_TOKEN = None  # type: ignore[attr-defined]


def test_read_endpoint_returns_card(actions_env):
    client, _, _, _ = actions_env

    headers = {"Authorization": "Bearer TEST_TOKEN"}
    resp = client.get("/read/DemoCard", headers=headers)
    assert resp.status_code == 200
    assert resp.text == "initial"


def test_write_endpoint_updates_storage(actions_env):
    client, repo_db, api_module, card_path = actions_env

    headers = {"Authorization": "Bearer TEST_TOKEN"}
    resp = client.post(
        "/write/DemoCard",
        content="updated",
        headers={**headers, "Content-Type": "text/plain"},
    )
    assert resp.status_code == 200
    assert resp.text == "ok"

    follow = client.get("/read/DemoCard", headers=headers)
    assert follow.status_code == 200
    assert follow.text == "updated"

    conn = repo_db._ensure_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT card FROM repo WHERE target = ?", ("DemoCard",))
        stored = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    assert stored is not None
    assert stored[0] == "updated"
    assert api_module.read("DemoCard") == "updated"
    assert card_path.read_text(encoding="utf-8") == "updated"
