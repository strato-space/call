from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def api_modules(monkeypatch, tmp_path):
    db_path = tmp_path / "repo.db"
    card_path = tmp_path / "cards" / "demo.md"
    repo_db = importlib.import_module("call.lib.repo_db")
    api_mod = importlib.import_module("call.lib.api")
    monkeypatch.setattr(repo_db, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(api_mod.call_repo, "DB_PATH", str(db_path), raising=False)

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

    yield api_mod, repo_db, card_path


def test_read_returns_raw_card_text(api_modules):
    api_mod, _, _ = api_modules
    result = api_mod.read("DemoCard")
    assert result == "initial"


def test_write_updates_db_and_filesystem(api_modules):
    api_mod, repo_db, card_path = api_modules

    api_mod.write("DemoCard", "updated text")

    assert api_mod.read("DemoCard") == "updated text"

    conn = repo_db._ensure_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT card FROM repo WHERE target = ?", ("DemoCard",))
        stored = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    assert stored is not None
    assert stored[0] == "updated text"
    assert card_path.read_text(encoding="utf-8") == "updated text"
