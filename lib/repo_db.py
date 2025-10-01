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

import re
import os
import sqlite3
from pathlib import Path
import builtins as _builtins
from typing import Dict, List, Optional, Tuple

import logging

# Location of the SQLite database. Default to call/repo.db next to this module,
# but can be overridden via the DB_PATH environment variable.
DB_PATH = os.getenv("DB_PATH", "call/repo.db")


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
    return re.compile("^" + re.escape(pattern).replace("\\*", ".*") + "$", re.IGNORECASE)


def _like_pattern(pat: Optional[str]) -> Optional[str]:
    """Convert wildcard '*' to SQL LIKE pattern ('%'). Escape existing '%' and '_' with backslash.
    Returns None if pat is falsy.
    """
    if not pat:
        return None
    s = str(pat)
    s = s.replace('%', r'\%').replace('_', r'\_')
    s = s.replace('*', '%')
    return s


def _build_where_and_params(project: Optional[str], agent: Optional[str], prompt: Optional[str], state: Optional[str]) -> tuple[list[str], list[str]]:
    where: list[str] = ["1=1"]
    params: list[str] = []
    lp = _like_pattern(project)
    la = _like_pattern(agent)
    lr = _like_pattern(prompt)
    ls = _like_pattern(state)
    if lp:
        where.append(("project LIKE ? ESCAPE '\\' COLLATE NOCASE") if ('*' in (project or '')) else ("project = ? COLLATE NOCASE"))
        params.append(lp)
    if la:
        where.append(("agent LIKE ? ESCAPE '\\' COLLATE NOCASE") if ('*' in (agent or '')) else ("agent = ? COLLATE NOCASE"))
        params.append(la)
    if lr:
        where.append(("prompt LIKE ? ESCAPE '\\' COLLATE NOCASE") if ('*' in (prompt or '')) else ("prompt = ? COLLATE NOCASE"))
        params.append(lr)
    if ls:
        where.append(("state LIKE ? ESCAPE '\\' COLLATE NOCASE") if ('*' in (state or '')) else ("state = ? COLLATE NOCASE"))
        params.append(ls)
    return where, params


def list(*, project: Optional[str] = None, agent: Optional[str] = None, prompt: Optional[str] = None, state: Optional[str] = None, target: Optional[str] = None) -> List[Dict[str, object]]:
    """Query the repo.db index and return a hierarchical structure (projects -> agents -> prompts).

    Wildcards: '*' supported in filters (case-insensitive, full-token match).
    """
    conn = _ensure_db(); cur = conn.cursor()
    rx_t = _rx(target)
    rx_p = _rx(prompt)
    # IMPORTANT: do NOT filter by prompt in SQL — we need agent-level rows (prompt="")
    where, params = _build_where_and_params(project, agent, None, state)
    sql = (
        "SELECT target as id, project, agent, prompt, path, state, target, type, rel_path, url, goal, card "
        "FROM repo WHERE "
        + " AND ".join(where)
    )
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    cur.close(); conn.close()

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

    # Build hierarchy: project -> agents[] (name/path/prompts[])
    proj_map: Dict[str, Dict[str, object]] = {}
    for row_id, prj, ag, pr, path, st, tgt, typ, rel, url, goal, card in items:
        # Ensure project bucket
        if prj not in proj_map:
            proj_map[prj] = {"name": prj, "type": "project", "agents": []}
        agents_list: List[Dict[str, object]] = proj_map[prj]["agents"]  # type: ignore
        # Skip rows without agent (project-only rows or prompts missing agent)
        if not ag:
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


def find_prompts(*, project: Optional[str] = None, agent: Optional[str] = None, state: Optional[str] = None, target: Optional[str] = None, prompt: Optional[str] = None) -> List[Dict[str, str]]:
    """Return prompt rows from repo.db with wildcard-aware filters.

    Wildcards:
      - '*' supported for project/agent/prompt/state/target
      - Filters are ANDed; target applied after SQL fetch to preserve `LIKE` semantics
    """
    conn = _ensure_db(); cur = conn.cursor()
    try:
        rx_t = _rx(target)
        where, params = _build_where_and_params(project, agent, prompt, state)
        # Ensure we only select prompt rows
        where = ["prompt != ''"] + [w for w in where if w != "1=1"]
        sql = (
            "SELECT target as id, project, agent, prompt, path, state, target, engine, orchestration, type, rel_path, url, goal "
            "FROM repo WHERE "
            + " AND ".join(where)
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
            out.append({
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
            })
        return out
    finally:
        cur.close(); conn.close()

# Backwards-compat alias
list_prompts = find_prompts


def find_agents(*, project: Optional[str] = None, agent: Optional[str] = None, target: Optional[str] = None) -> List[Dict[str, str]]:
    """Find agent rows (prompt-empty) with wildcard filters. Returns an array of rows."""
    conn = _ensure_db(); cur = conn.cursor()
    try:
        rx_t = _rx(target)
        where, params = _build_where_and_params(project, agent, None, None)
        # Only non-prompt agent rows
        where = ["(prompt IS NULL OR prompt = '')", "agent != ''"] + [w for w in where if w != "1=1"]
        sql = (
            "SELECT target as id, project, agent, path, target, type, rel_path, url, goal "
            "FROM repo WHERE "
            + " AND ".join(where)
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
            out.append({
                "id": row_id or tgt or ag or "",
                "project": prj or "",
                "agent": ag or "",
                "path": path or "",
                "rel_path": rel or "",
                "target": tgt or row_id or ag or "",
                "type": (typ or "agent"),
                "url": url or "",
                "goal": goal or "",
            })
        return out
    finally:
        cur.close(); conn.close()


def find_projects(*, project: Optional[str] = None, target: Optional[str] = None) -> List[Dict[str, str]]:
    """Find project rows with wildcard filters. Returns an array of rows."""
    conn = _ensure_db(); cur = conn.cursor()
    try:
        rx_t = _rx(target)
        where, params = _build_where_and_params(project, None, None, None)
        # Only project-level rows
        where = ["prompt = ''", "agent = ''", "project != ''"] + [w for w in where if w != "1=1"]
        sql = (
            "SELECT target as id, project, path, target, type, rel_path, url, goal "
            "FROM repo WHERE "
            + " AND ".join(where)
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
            out.append({
                "id": row_id or tgt or prj or "",
                "project": prj or "",
                "path": path or "",
                "rel_path": rel or "",
                "target": tgt or row_id or prj or "",
                "type": (typ or "project"),
                "url": url or "",
                "goal": goal or "",
            })
        return out
    finally:
        cur.close(); conn.close()


def _parse_card_text(text: str, *, hint: str = "") -> tuple[Dict[str, object], str, str]:
    raw = text or ""
    if not raw.strip():
        return {}, "", ""
    try:
        from call.lib.utils import parse_metadata_and_prompt as _parse

        parsed = _parse(raw, path=hint or None)
    except ValueError:
        try:
            import yaml as _yaml

            data = _yaml.safe_load(raw)
        except Exception:
            return {}, "", raw
        if isinstance(data, dict):
            return data, "", raw
        return {}, "", raw
    except Exception:
        return {}, "", raw
    meta = dict(parsed or {})
    body = str(meta.get("prompt") or "")
    meta.pop("prompt", None)
    return meta, body, raw


class CardNotFoundError(FileNotFoundError):
    """Raised when a card record cannot be located in repo.db or on disk."""


def get_card(card_id: str) -> tuple[Dict[str, object], str, str]:
    """Load card content by identifier from repo.db."""

    if not card_id or not str(card_id).strip():
        raise ValueError("card id is required")

    conn = _ensure_db(); cur = conn.cursor()
    try:
        cur.execute(
            "SELECT card FROM repo WHERE target = ? LIMIT 1",
            (card_id,),
        )
        row = cur.fetchone()
    finally:
        cur.close(); conn.close()

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

    def __init__(self, message: str, *, kind: str, options: Optional[List[Dict[str, str]]] = None) -> None:
        super().__init__(
            message,
            kind=kind,
            code="TOO_MANY_ROWS",
            status=400,
            options=options,
        )


class SelectionNotFoundError(SelectionError):
    """Raised when a required row cannot be found for the provided selector."""

    def __init__(self, message: str, *, kind: str, filters: Optional[Dict[str, str]] = None) -> None:
        super().__init__(
            message,
            kind=kind,
            code="NO_DATA_FOUND",
            status=404,
            filters=filters,
        )


def _first_stripped(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    try:
        text = str(value)
    except Exception:
        return None
    stripped = text.strip()
    return stripped or None


def _require_single(
    *,
    kind: str,
    fetcher,
    filters: Dict[str, Optional[str]],
    required: bool,
) -> Optional[Dict[str, str]]:
    kwargs = dict(filters)
    try:
        rows = fetcher(**kwargs) or []
    except SelectionError:
        raise
    except Exception as exc:  # pragma: no cover - propagated as SelectionError
        raise SelectionError(
            f"Failed to query {kind}",
            kind=kind,
            code="INTERNAL_ERROR",
            status=500,
        ) from exc

    if not rows:
        if required:
            clean_filters = {k: v for k, v in kwargs.items() if isinstance(v, str) and v}
            raise SelectionNotFoundError(
                f"No {kind} found matching the provided filters",
                kind=kind,
                filters=clean_filters,
            )
        return None

    if len(rows) > 1:
        raise TooManyRowsError(
            (
                "Multiple prompts matched your criteria"
                if kind == "prompt"
                else "Multiple agents matched your criteria"
                if kind == "agent"
                else "Multiple projects matched your criteria"
            ),
            kind=kind,
            options=[r for r in rows[:20]],
        )

    return rows[0]


def select_unique_rows(
    *,
    project: Optional[str],
    agent: Optional[str],
    prompt: Optional[str],
    require_project: Optional[bool] = None,
    require_agent: Optional[bool] = None,
    require_prompt: Optional[bool] = None,
) -> tuple[Optional[Dict[str, str]], Optional[Dict[str, str]], Optional[Dict[str, str]]]:
    """Return single project/agent/prompt rows when present, enforcing uniqueness."""

    project_filter = _first_stripped(project)
    agent_filter = _first_stripped(agent)
    prompt_filter = _first_stripped(prompt)

    if require_project is None:
        require_project = bool(project_filter)
    if require_agent is None:
        require_agent = bool(agent_filter)
    if require_prompt is None:
        require_prompt = bool(prompt_filter)

    project_row = None
    agent_row = None
    prompt_row = None

    if project_filter:
        project_row = _require_single(
            kind="project",
            fetcher=find_projects,
            filters={"project": project_filter},
            required=require_project,
        )

    if agent_filter:
        agent_row = _require_single(
            kind="agent",
            fetcher=find_agents,
            filters={"project": project_filter, "agent": agent_filter},
            required=require_agent,
        )

    if prompt_filter:
        prompt_row = _require_single(
            kind="prompt",
            fetcher=find_prompts,
            filters={
                "project": project_filter,
                "agent": agent_filter,
                "prompt": prompt_filter,
                "state": None,
                "target": None,
            },
            required=require_prompt,
        )

    return project_row, agent_row, prompt_row
