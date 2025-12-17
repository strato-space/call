"""
DB-only repository interface backed by SQLite (default: call/repo.db).

Functions:
- list(project?, agent?, prompt?, state?, target?) -> list[dict]
- list_prompts(project?, agent?, prompt?, state?, target?) -> list[dict]
- find_projects(project?, target?) -> list[dict]
- find_agents(project?, agent?, target?) -> list[dict]
- find_prompts(project?, agent?, prompt?, state?, target?) -> list[dict]
"""

from __future__ import annotations

import json
import re
import os
import sqlite3
from pathlib import Path
import builtins as _builtins
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import logging

# Location of the SQLite database. Default to call/repo.db next to this module,
# but can be overridden via the DB_PATH environment variable.
DB_PATH = os.getenv("DB_PATH", "call/repo.db")
EVENT_DB_PATH = os.getenv("EVENT_DB_PATH", "call/call.db")


def _ensure_db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Schema: add 'state' to support ready/draft prompt state, and engine/orchestration for runtime
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS repo (
            target   TEXT PRIMARY KEY,
            project  TEXT,
            agent    TEXT,
            prompt   TEXT,
            path     TEXT,
            state    TEXT,
            engine   TEXT,
            orchestration TEXT,
            type     TEXT,
            rel_path TEXT,
            url      TEXT,
            goal     TEXT,
            card     TEXT
        )
        """
    )
    # Helpful indices for filters
    cur.execute("CREATE INDEX IF NOT EXISTS idx_repo_project ON repo(project)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_repo_agent   ON repo(agent)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_repo_prompt  ON repo(prompt)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_repo_state   ON repo(state)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_repo_target  ON repo(target)")
    # Migration: add columns when missing
    try:
        cur.execute("PRAGMA table_info(repo)")
        cols = [r[1] for r in cur.fetchall()]
        if "state" not in cols:
            cur.execute("ALTER TABLE repo ADD COLUMN state TEXT")
        if "engine" not in cols:
            cur.execute("ALTER TABLE repo ADD COLUMN engine TEXT")
        if "orchestration" not in cols:
            cur.execute("ALTER TABLE repo ADD COLUMN orchestration TEXT")
        if "type" not in cols:
            cur.execute("ALTER TABLE repo ADD COLUMN type TEXT")
        if "rel_path" not in cols:
            cur.execute("ALTER TABLE repo ADD COLUMN rel_path TEXT")
        if "url" not in cols:
            cur.execute("ALTER TABLE repo ADD COLUMN url TEXT")
        if "goal" not in cols:
            cur.execute("ALTER TABLE repo ADD COLUMN goal TEXT")
        if "card" not in cols:
            cur.execute("ALTER TABLE repo ADD COLUMN card TEXT")
    except Exception:
        pass
    conn.commit()
    return conn


def _rx(pattern: Optional[str]):
    if not pattern:
        return None
    return re.compile(
        "^" + re.escape(pattern).replace("\\*", ".*") + "$", re.IGNORECASE
    )


def _like_pattern(pat: Optional[str]) -> Optional[str]:
    """Convert wildcard '*' to SQL LIKE pattern ('%'). Escape existing '%' and '_' with backslash.
    Returns None if pat is falsy.
    """
    if not pat:
        return None
    s = str(pat)
    s = s.replace("%", r"\%").replace("_", r"\_")
    s = s.replace("*", "%")
    return s


def _ensure_events_db() -> sqlite3.Connection:
    Path(EVENT_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(EVENT_DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            event   TEXT NOT NULL,
            input   TEXT
        )
        """
    )
    cur.execute("PRAGMA table_info(events)")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "input" not in existing_columns:
        cur.execute("ALTER TABLE events ADD COLUMN input TEXT")
    if "payload" in existing_columns:
        cur.execute(
            "UPDATE events SET input = COALESCE(input, payload) WHERE payload IS NOT NULL"
        )
    conn.commit()
    return conn


@dataclass(frozen=True)
class EventRow:
    id: int
    event: str
    payload: Optional[Any]

    def __init__(self, id: int, event: str, payload: Optional[Any]):
        # Explicit init to tolerate keyword construction in tests and ensure
        # consistent types when reloaded under different envs.
        object.__setattr__(self, "id", int(id))
        object.__setattr__(self, "event", str(event))
        object.__setattr__(self, "payload", payload)

    @property
    def input(self) -> Optional[Any]:
        # Backward compatibility for older callers/tests
        return self.payload


def push_event(event: str, input_text: Any = None) -> int:
    """Persist an event payload into call.db.

    Returns the inserted row ID to align with future queue offsets.
    """
    conn = _ensure_events_db()
    cur = conn.cursor()
    input_value: Optional[str]
    if input_text is None:
        input_value = None
    elif isinstance(input_text, str):
        input_value = input_text
    else:
        try:
            input_value = json.dumps(input_text, ensure_ascii=False)
        except Exception:
            input_value = str(input_text)
    cur.execute(
        "INSERT INTO events (event, input) VALUES (?, ?)",
        (str(event), input_value),
    )
    conn.commit()
    row_id = cur.lastrowid
    cur.close()
    conn.close()
    return int(row_id)


def iter_events(
    *, after_id: Optional[int] = None, limit: Optional[int] = None
) -> List[EventRow]:
    """Read a batch of events in ascending order.

    This helper mirrors a polling consumer API to ease migration to MQ backends.
    """
    conn = _ensure_events_db()
    cur = conn.cursor()
    sql = "SELECT id, event, input FROM events WHERE 1=1"
    params: List[Any] = []
    if isinstance(after_id, int):
        sql += " AND id > ?"
        params.append(after_id)
    sql += " ORDER BY id ASC"
    if isinstance(limit, int) and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    def _parse_input(raw: Optional[str]) -> Optional[Any]:
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw

    return [
        EventRow(
            id=int(row_id),
            event=str(event_name or ""),
            payload=_parse_input(input_text if isinstance(input_text, str) else None),
        )
        for row_id, event_name, input_text in rows
    ]


def _build_where_and_params(
    project: Optional[str],
    agent: Optional[str],
    prompt: Optional[str],
    state: Optional[str],
) -> tuple[list[str], list[str]]:
    where: list[str] = ["1=1"]
    params: list[str] = []
    lp = _like_pattern(project)
    la = _like_pattern(agent)
    lr = _like_pattern(prompt)
    ls = _like_pattern(state)
    if lp:
        where.append(
            ("project LIKE ? ESCAPE '\\' COLLATE NOCASE")
            if ("*" in (project or ""))
            else ("project = ? COLLATE NOCASE")
        )
        params.append(lp)
    if la:
        where.append(
            ("agent LIKE ? ESCAPE '\\' COLLATE NOCASE")
            if ("*" in (agent or ""))
            else ("agent = ? COLLATE NOCASE")
        )
        params.append(la)
    if lr:
        where.append(
            ("prompt LIKE ? ESCAPE '\\' COLLATE NOCASE")
            if ("*" in (prompt or ""))
            else ("prompt = ? COLLATE NOCASE")
        )
        params.append(lr)
    if ls:
        where.append(
            ("state LIKE ? ESCAPE '\\' COLLATE NOCASE")
            if ("*" in (state or ""))
            else ("state = ? COLLATE NOCASE")
        )
        params.append(ls)
    return where, params


def list(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    state: Optional[str] = None,
    target: Optional[str] = None,
) -> List[Dict[str, object]]:
    """Query the repo.db index and return a hierarchical structure (projects -> agents -> prompts).

    Wildcards: '*' supported in filters (case-insensitive, full-token match).
    """
    conn = _ensure_db()
    cur = conn.cursor()
    rx_t = _rx(target)
    rx_p = _rx(prompt)
    # IMPORTANT: do NOT filter by prompt in SQL — we need agent-level rows (prompt="")
    where, params = _build_where_and_params(project, agent, None, state)
    sql = (
        "SELECT target as id, project, agent, prompt, path, state, target, type, rel_path, url, goal, card "
        "FROM repo WHERE " + " AND ".join(where)
    )
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    # Filter in Python for simplicity
    items: List[Tuple[str, str, str, str, str, str, str, str, str, str, str, str]] = []
    for row_id, prj, ag, pr, path, st, tgt, typ, rel, url, goal, card in rows:
        if rx_t and not (tgt and rx_t.match(tgt)):
            continue
        # prompt filter: keep agent-level rows (pr == ""), and only include prompt rows that match
        if rx_p and pr and not rx_p.match(pr):
            continue
        items.append(
            (
                row_id or "",
                prj or "",
                ag or "",
                pr or "",
                path or "",
                st or "",
                tgt or "",
                typ or "",
                rel or "",
                url or "",
                goal or "",
                card or "",
            )
        )

    # Build hierarchy: project -> agents[] (name/path/prompts[]) + prompts[] (project-level)
    proj_map: Dict[str, Dict[str, object]] = {}
    for row_id, prj, ag, pr, path, st, tgt, typ, rel, url, goal, card in items:
        # Ensure project bucket
        if prj not in proj_map:
            proj_map[prj] = {"name": prj, "type": "project", "agents": [], "prompts": []}
        agents_list: List[Dict[str, object]] = proj_map[prj]["agents"]  # type: ignore
        project_prompts: List[str] = proj_map[prj]["prompts"]  # type: ignore
        # Handle project-level prompts (no agent)
        if not ag:
            # Add prompt to project-level prompts list
            if pr and pr not in project_prompts:
                project_prompts.append(pr)
            continue
        # Find or create agent entry
        agent_entry = None
        for a in agents_list:
            if a.get("name") == ag:
                agent_entry = a
                break
        if agent_entry is None:
            agent_entry = {
                "type": "agent",
                "id": row_id or tgt or ag or "",
                "name": ag,
                "aliases": [],
                "prompts": [],
                "path": path if ag and not pr else "",
            }
            agents_list.append(agent_entry)
        if row_id and agent_entry.get("id") != row_id:
            agent_entry["id"] = row_id
        # Add prompt if present
        if pr:
            prompts: List[str] = agent_entry["prompts"]  # type: ignore
            if not isinstance(prompts, _builtins.list):
                prompts = []
                agent_entry["prompts"] = prompts  # type: ignore
            if pr not in prompts:
                prompts.append(pr)
        # Update path for agent rows when pr is empty (agent-level record)
        if ag and not pr:
            if path:
                agent_entry["path"] = path

    # If project filter without wildcard, return only that project
    out = _builtins.list(proj_map.values())
    if project and "*" not in project:
        out = [x for x in out if x.get("name") == project]
    return out


def find_prompts(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    state: Optional[str] = None,
    target: Optional[str] = None,
    prompt: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Return prompt rows from repo.db with wildcard-aware filters.

    Wildcards:
      - '*' supported for project/agent/prompt/state/target
      - Filters are ANDed; target applied after SQL fetch to preserve `LIKE` semantics
      - Project-scoped queries also surface prompts whose project column is blank so that
        global prompts remain discoverable across projects.
    """
    conn = _ensure_db()
    cur = conn.cursor()
    try:
        rx_t = _rx(target)

        def _add_filter(
            column: str, value: Optional[str], *, allow_global: bool = False
        ) -> tuple[Optional[str], Optional[str]]:
            if value is None:
                return None, None
            raw_value = str(value)
            has_wildcard = "*" in raw_value
            pattern = _like_pattern(raw_value)
            if not pattern:
                return None, None
            if has_wildcard:
                clause = f"{column} LIKE ? ESCAPE '\\' COLLATE NOCASE"
            else:
                clause = f"{column} = ? COLLATE NOCASE"
                if allow_global:
                    clause = f"({clause} OR {column} = '' OR {column} IS NULL)"
            return clause, pattern

        filters: list[str] = ["prompt != ''"]
        params: list[str] = []

        project_clause, project_param = _add_filter(
            "project", project, allow_global=True
        )
        if project_clause:
            filters.append(project_clause)
        if project_param:
            params.append(project_param)

        agent_clause, agent_param = _add_filter("agent", agent)
        if agent_clause:
            filters.append(agent_clause)
        if agent_param:
            params.append(agent_param)

        # Prompt filter: match both prompt and target so callers can use
        # prompt ids the same way they use card ids (target). This mirrors
        # select_card() behavior where prompt names can also live in target.
        if prompt is not None:
            raw_prompt = str(prompt)
            has_wildcard = "*" in raw_prompt
            if has_wildcard:
                pattern = _like_pattern(raw_prompt)
                if pattern:
                    clause = (
                        "(prompt LIKE ? ESCAPE '\\' COLLATE NOCASE "
                        "OR target LIKE ? ESCAPE '\\' COLLATE NOCASE)"
                    )
                    filters.append(clause)
                    params.append(pattern)
                    params.append(pattern)
            else:
                # Exact match: use raw value for '=' comparison to avoid
                # escaping '_' or '%' as in LIKE patterns.
                clause = "(prompt = ? COLLATE NOCASE OR target = ? COLLATE NOCASE)"
                filters.append(clause)
                params.append(raw_prompt)
                params.append(raw_prompt)

        state_clause, state_param = _add_filter("state", state)
        if state_clause:
            filters.append(state_clause)
        if state_param:
            params.append(state_param)

        sql = (
            "SELECT target as id, project, agent, prompt, path, state, target, engine, orchestration, type, rel_path, url, goal "
            "FROM repo WHERE " + " AND ".join(filters)
        )
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        for row_id, prj, ag, pr, path, st, tgt, eng, orch, typ, rel, url, goal in rows:
            if rx_t and not (tgt and rx_t.match(tgt)):
                continue
            rel_path = (rel or path or "").strip()
            if not (row_id or tgt or pr):
                continue
            if not rel_path:
                continue
            out.append(
                {
                    "id": row_id or tgt or pr or "",
                    "project": prj or "",
                    "agent": ag or "",
                    "prompt": pr or "",
                    "path": path or "",
                    "rel_path": rel or "",
                    "state": st or "",
                    "target": tgt or row_id or pr or "",
                    "engine": eng or "",
                    "orchestration": orch or "",
                    "type": (typ or "prompt"),
                    "url": url or "",
                    "goal": goal or "",
                }
            )
        return out
    finally:
        cur.close()
        conn.close()


# Backwards-compat alias
list_prompts = find_prompts


def find_agents(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    target: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Find agent rows (prompt-empty) with wildcard filters. Returns an array of rows."""
    conn = _ensure_db()
    cur = conn.cursor()
    try:
        rx_t = _rx(target)
        where, params = _build_where_and_params(project, agent, None, None)
        # Only non-prompt agent rows
        where = ["(prompt IS NULL OR prompt = '')", "agent != ''"] + [
            w for w in where if w != "1=1"
        ]
        sql = (
            "SELECT target as id, project, agent, path, target, type, rel_path, url, goal "
            "FROM repo WHERE " + " AND ".join(where)
        )
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        for row_id, prj, ag, path, tgt, typ, rel, url, goal in rows:
            if rx_t and not (tgt and rx_t.match(tgt)):
                continue
            rel_path = (rel or path or "").strip()
            if not (row_id or tgt or ag):
                continue
            if not rel_path:
                continue
            out.append(
                {
                    "id": row_id or tgt or ag or "",
                    "project": prj or "",
                    "agent": ag or "",
                    "path": path or "",
                    "rel_path": rel or "",
                    "target": tgt or row_id or ag or "",
                    "type": (typ or "agent"),
                    "url": url or "",
                    "goal": goal or "",
                }
            )
        return out
    finally:
        cur.close()
        conn.close()


def find_projects(
    *, project: Optional[str] = None, target: Optional[str] = None
) -> List[Dict[str, str]]:
    """Find project rows with wildcard filters. Returns an array of rows."""
    conn = _ensure_db()
    cur = conn.cursor()
    try:
        rx_t = _rx(target)
        where, params = _build_where_and_params(project, None, None, None)
        # Only project-level rows
        where = ["prompt = ''", "agent = ''", "project != ''"] + [
            w for w in where if w != "1=1"
        ]
        sql = (
            "SELECT target as id, project, path, target, type, rel_path, url, goal "
            "FROM repo WHERE " + " AND ".join(where)
        )
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        for row_id, prj, path, tgt, typ, rel, url, goal in rows:
            if rx_t and not (tgt and rx_t.match(tgt)):
                continue
            rel_path = (rel or path or "").strip()
            if not (row_id or tgt or prj):
                continue
            if not rel_path:
                continue
            out.append(
                {
                    "id": row_id or tgt or prj or "",
                    "project": prj or "",
                    "path": path or "",
                    "rel_path": rel or "",
                    "target": tgt or row_id or prj or "",
                    "type": (typ or "project"),
                    "url": url or "",
                    "goal": goal or "",
                }
            )
        return out
    finally:
        cur.close()
        conn.close()


def _parse_card_text(
    text: str, *, hint: str = ""
) -> tuple[Dict[str, object], str, str]:
    raw = text or ""
    if not raw.strip():
        return {}, "", ""

    def _format_error(message: str, cause: Exception | None = None) -> CardFormatError:
        detail = message.strip() or "card metadata could not be parsed"
        identifier = (hint or "").strip()
        if identifier:
            detail = f"card '{identifier}' {detail}"
        err = CardFormatError(detail)
        if cause is not None:
            err.__cause__ = cause  # type: ignore[attr-defined]
        return err

    try:
        from call.lib.utils import parse_metadata_and_prompt as _parse

        parsed = _parse(raw, path=hint or None)
    except Exception as exc:
        try:
            import yaml as _yaml

            data = _yaml.safe_load(raw)
        except Exception:
            raise _format_error("metadata could not be parsed", exc)
        if isinstance(data, dict):
            return data, "", raw
        raise _format_error("metadata could not be parsed", exc)
    meta = dict(parsed or {})
    body = str(meta.get("prompt") or "")
    meta.pop("prompt", None)
    return meta, body, raw


class CardNotFoundError(FileNotFoundError):
    """Raised when a card record cannot be located in repo.db or on disk."""


class CardFormatError(ValueError):
    """Raised when a stored card cannot be parsed into metadata/prompt content."""


def get_card(card_id: str) -> tuple[Dict[str, object], str, str]:
    """Load card content by identifier from repo.db."""

    if not card_id or not str(card_id).strip():
        raise ValueError("card id is required")

    conn = _ensure_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT card FROM repo WHERE target = ? LIMIT 1",
            (card_id,),
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not row:
        raise CardNotFoundError(f"card '{card_id}' not found")

    card_ref = row[0]

    if not isinstance(card_ref, str) or not card_ref.strip():
        raise CardNotFoundError(f"card '{card_id}' not found")

    meta, body, raw = _parse_card_text(card_ref, hint=card_id)
    if not raw:
        raise CardNotFoundError(f"card '{card_id}' not found")
    return meta, body, raw


class SelectionError(Exception):
    """Base error for selection helpers that expect a single repo row."""

    def __init__(
        self,
        message: str,
        *,
        kind: str,
        code: str,
        status: int = 400,
        options: Optional[List[Dict[str, str]]] = None,
        filters: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.status = status
        self.options = options or []
        self.filters = filters or {}


class TooManyRowsError(SelectionError):
    """Raised when more than one row satisfies the requested selector."""

    def __init__(
        self, message: str, *, kind: str, options: Optional[List[Dict[str, str]]] = None
    ) -> None:
        super().__init__(
            message,
            kind=kind,
            code="TOO_MANY_ROWS",
            status=400,
            options=options,
        )


class SelectionNotFoundError(SelectionError):
    """Raised when a required row cannot be found for the provided selector."""

    def __init__(
        self, message: str, *, kind: str, filters: Optional[Dict[str, str]] = None
    ) -> None:
        super().__init__(
            message,
            kind=kind,
            code="NO_DATA_FOUND",
            status=404,
            filters=filters,
        )


@dataclass(frozen=True)
class RepoCardRow:
    """Single repo table row with values preserved as stored in SQLite."""

    id: str | None
    target: str | None
    project: str | None
    agent: str | None
    prompt: str | None
    path: str | None
    state: str | None
    engine: str | None
    orchestration: str | None
    type: str | None
    rel_path: str | None
    url: str | None
    goal: str | None
    card: str | None


def _add_filter_clause(
    where: List[str], params: List[str], *, column: str, value: Optional[str]
) -> None:
    if value is None:
        return
    try:
        raw = str(value)
    except Exception:
        raw = ""
    if raw == "":
        where.append(f"{column} = ''")
        return
    has_wildcard = "*" in raw
    pattern = _like_pattern(raw) if has_wildcard else None

    if column == "prompt" and (raw == "" or raw is None):
        # Allow fallback to prompt-less rows when empty string is requested explicitly.
        where.append("(prompt = '' OR prompt IS NULL)")
        return

    if column == "prompt" and not has_wildcard and isinstance(raw, str) and raw:
        # Allow matching prompt names within YAML mapping where they are stored as
        # "project/prompt" target entries without an explicit agent.
        where.append(
            "(prompt = ? COLLATE NOCASE OR target = ? COLLATE NOCASE"
            " OR target = ? COLLATE NOCASE)"
        )
        # Use the raw value for exact '=' comparisons to avoid escaping
        # '_' and '%' as in LIKE patterns.
        params.append(raw)
        params.append(raw)
        params.append(raw)
        return

    if has_wildcard:
        where.append(f"{column} LIKE ? ESCAPE '\\' COLLATE NOCASE")
        if pattern is not None:
            params.append(pattern)
    else:
        where.append(f"{column} = ? COLLATE NOCASE")
        params.append(raw)


def _kind_filters(kind: Optional[str]) -> List[str]:
    if kind == "prompt":
        return ["prompt != ''"]
    if kind == "agent":
        return ["prompt = ''", "agent != ''"]
    if kind == "project":
        return ["agent = ''", "prompt = ''"]
    return []


def select_card(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    target: Optional[str] = None,
    kind: Optional[str] = None,
) -> RepoCardRow:
    """Return a single repo row matching the provided filters.

    Args:
        project/agent/prompt/target: Optional selectors. '*' is treated as a wildcard.
        kind: Optional explicit type hint ("project", "agent", "prompt").

    Raises:
        SelectionNotFoundError: When no rows satisfy the filters.
        TooManyRowsError: When multiple rows satisfy the filters.
    
    Target resolution priority (when kind is not specified): project → agent → prompt
    This ensures prompts cannot override projects or agents (more secure).
    """

    conn = _ensure_db()
    cur = conn.cursor()
    try:
        # When target is specified without explicit kind, use cascading lookup: project → agent → prompt
        if target and not kind:
            # Try project first (with constraints if provided)
            try:
                result = select_card(project=project, agent=agent, prompt=prompt, target=target, kind="project")
                return result
            except SelectionNotFoundError:
                pass
            
            # Try agent second (with constraints if provided)
            try:
                result = select_card(project=project, agent=agent, prompt=prompt, target=target, kind="agent")
                return result
            except SelectionNotFoundError:
                pass
            
            # Try prompt last (with constraints if provided)
            result = select_card(project=project, agent=agent, prompt=prompt, target=target, kind="prompt")
            return result
        
        where: List[str] = ["1=1"]
        params: List[str] = []

        where.extend(_kind_filters(kind))

        _add_filter_clause(where, params, column="project", value=project)
        _add_filter_clause(where, params, column="agent", value=agent)

        if kind == "agent":
            _add_filter_clause(where, params, column="prompt", value="")
        elif kind == "project":
            _add_filter_clause(where, params, column="prompt", value="")
            _add_filter_clause(where, params, column="agent", value="")
        else:
            _add_filter_clause(where, params, column="prompt", value=prompt)

        _add_filter_clause(where, params, column="target", value=target)

        sql = (
            "SELECT target, project, agent, prompt, path, state, engine, orchestration, type, rel_path, url, goal, card "
            "FROM repo WHERE " + " AND ".join(where)
        )
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    if not rows:
        clean_filters = {
            k: v
            for k, v in {
                "project": project,
                "agent": agent,
                "prompt": prompt,
                "target": target,
            }.items()
            if isinstance(v, str) and v
        }
        raise SelectionNotFoundError(
            "No card found matching the provided filters",
            kind=(kind or "card"),
            filters=clean_filters,
        )

    if len(rows) > 1:
        options: List[Dict[str, str]] = []
        for row in rows[:20]:
            tgt, prj, ag, prm, path, *_ = row
            options.append(
                {
                    "target": tgt,
                    "project": prj,
                    "agent": ag,
                    "prompt": prm,
                    "path": path,
                }
            )
        raise TooManyRowsError(
            "Multiple cards matched the provided filters",
            kind=(kind or "card"),
            options=options,
        )

    tgt, prj, ag, prm, path, state, eng, orch, typ, rel, url, goal, card = rows[0]

    return RepoCardRow(
        id=tgt,
        target=tgt,
        project=prj,
        agent=ag,
        prompt=prm,
        path=path,
        state=state,
        engine=eng,
        orchestration=orch,
        type=typ,
        rel_path=rel,
        url=url,
        goal=goal,
        card=card,
    )


__all__ = [
    "RepoCardRow",
    "select_card",
    "SelectionError",
    "SelectionNotFoundError",
    "TooManyRowsError",
]
