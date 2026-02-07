from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

from call.lib.history_models import HistoryMessage
from call.lib.paths import default_event_db_path


SCHEMA_VERSION = "call.history.v1"

logger = logging.getLogger(__name__)


def _db_path() -> str:
    return os.getenv("CALL_DB", str(default_event_db_path()))


def _ensure_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_history (
            session_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            agent_name TEXT NOT NULL,
            engine TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            history_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_conversation_history_conversation_agent
        ON conversation_history (conversation_id, agent_name)
        """
    )


def session_id(conversation_id: str, agent_name: str) -> str:
    return f"{conversation_id}::{agent_name}"


def load_history(conversation_id: str, agent_name: str) -> list[HistoryMessage]:
    sid = session_id(conversation_id, agent_name)
    db_path = _db_path()
    try:
        if not Path(db_path).exists():
            return []
        with sqlite3.connect(db_path) as conn:
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT history_json FROM conversation_history WHERE session_id = ?",
                (sid,),
            ).fetchone()
        if not row or not row[0]:
            return []
        raw = json.loads(row[0])
        if not isinstance(raw, list):
            return []
        out: list[HistoryMessage] = []
        skipped = 0
        first_skip: str | None = None
        for item in raw:
            try:
                out.append(HistoryMessage.model_validate(item))
            except Exception as exc:
                skipped += 1
                if first_skip is None:
                    first_skip = f"{type(exc).__name__}: {exc}"
        if skipped:
            logger.debug(
                "[history] loaded %s messages for %s (skipped=%s, first=%s)",
                len(out),
                sid,
                skipped,
                first_skip,
            )
        return out
    except Exception as exc:
        logger.debug("[history] load failed for %s: %s", sid, exc)
        return []


def save_history(
    conversation_id: str,
    agent_name: str,
    engine: str,
    messages: list[HistoryMessage],
) -> None:
    sid = session_id(conversation_id, agent_name)
    db_path = _db_path()
    _ensure_dir(db_path)
    payload = json.dumps([m.model_dump(mode="json") for m in messages], ensure_ascii=False)
    with sqlite3.connect(db_path) as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO conversation_history
                (session_id, conversation_id, agent_name, engine, schema_version, history_json)
            VALUES
                (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                conversation_id = excluded.conversation_id,
                agent_name = excluded.agent_name,
                engine = excluded.engine,
                schema_version = excluded.schema_version,
                history_json = excluded.history_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (sid, conversation_id, agent_name, engine, SCHEMA_VERSION, payload),
        )


def clear_history(conversation_id: str, agent_name: str) -> None:
    sid = session_id(conversation_id, agent_name)
    db_path = _db_path()
    if not Path(db_path).exists():
        return
    with sqlite3.connect(db_path) as conn:
        _ensure_schema(conn)
        conn.execute("DELETE FROM conversation_history WHERE session_id = ?", (sid,))
