from __future__ import annotations

import argparse
import importlib
import pytest


@pytest.fixture()
def cli_env(monkeypatch, tmp_path):
    db_path = tmp_path / "repo.db"
    card_path = tmp_path / "cards" / "demo.md"
    repo_db = importlib.import_module("call.lib.repo_db")
    api_module = importlib.import_module("call.lib.api")
    cli_main = importlib.import_module("call.cli.main")

    monkeypatch.setattr(repo_db, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(api_module.call_repo, "DB_PATH", str(db_path), raising=False)
    monkeypatch.setattr(cli_main.repo_db_module, "DB_PATH", str(db_path), raising=False)

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

    yield cli_main, api_module, repo_db, card_path


def test_cmd_read_outputs_card_text(cli_env, capsys):
    cli_main, _, _, _ = cli_env

    args = argparse.Namespace(id="DemoCard")
    rc = cli_main.cmd_read(args)

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "initial"


def test_cmd_write_updates_storage(cli_env, capsys):
    cli_main, api_module, repo_db, card_path = cli_env

    args = argparse.Namespace(id="DemoCard", card="updated", file="", stdin=False)
    rc = cli_main.cmd_write(args)

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == ""

    assert api_module.read("DemoCard") == "updated"

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
    assert card_path.read_text(encoding="utf-8") == "updated"
