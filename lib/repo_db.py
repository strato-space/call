"""
DB-only repository interface backed by SQLite (call/repo.db).

Functions:
- list(project?, agent?, prompt?, state?, target?) -> list[dict]
- list_prompts(project?, agent?, prompt?, state?, target?) -> list[dict]
- find_projects(project?, target?) -> list[dict]
- find_agents(project?, agent?, target?) -> list[dict]
- find_prompts(project?, agent?, prompt?, state?, target?) -> list[dict]
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
import builtins as _builtins
from typing import Dict, List, Optional, Tuple

# Location of the SQLite database. Default to call/repo.db next to this module.
DB_PATH = str(Path(__file__).resolve().parents[1] / "repo.db")


def _ensure_db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Schema: add 'state' to support ready/draft prompt state, and engine/orchestration for runtime
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS repo (
            target  TEXT PRIMARY KEY,
            project TEXT,
            agent   TEXT,
            prompt  TEXT,
            path    TEXT,
            state   TEXT,
            engine  TEXT,
            orchestration TEXT,
            type    TEXT,
            rel_path TEXT,
            url     TEXT,
            goal    TEXT
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
    sql = "SELECT project, agent, prompt, path, state, target, type, rel_path, url, goal FROM repo WHERE " + " AND ".join(where)
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    cur.close(); conn.close()

    # Filter in Python for simplicity
    items: List[Tuple[str, str, str, str, str, str, str, str, str, str]] = []
    for prj, ag, pr, path, st, tgt, typ, rel, url, goal in rows:
        if rx_t and not (tgt and rx_t.match(tgt)):
            continue
        # prompt filter: keep agent-level rows (pr == ""), and only include prompt rows that match
        if rx_p and pr and not rx_p.match(pr):
            continue
        items.append((prj or "", ag or "", pr or "", path or "", st or "", tgt or "", typ or "", rel or "", url or "", goal or ""))

    # Build hierarchy: project -> agents[] (name/path/prompts[])
    proj_map: Dict[str, Dict[str, object]] = {}
    for prj, ag, pr, path, st, tgt, typ, rel, url, goal in items:
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
            agent_entry = {"type": "agent", "id": "", "name": ag, "aliases": [], "prompts": [], "path": path if ag and not pr else ""}
            agents_list.append(agent_entry)
        # Add prompt if present
        if pr:
            prompts: List[str] = agent_entry["prompts"]  # type: ignore
            if not isinstance(prompts, _builtins.list):
                prompts = []
                agent_entry["prompts"] = prompts  # type: ignore
            if pr not in prompts:
                prompts.append(pr)
        # Update path for agent rows when pr is empty (agent-level record)
        if ag and not pr and path:
            agent_entry["path"] = path

    # If project filter without wildcard, return only that project
    out = _builtins.list(proj_map.values())
    if project and "*" not in project:
        out = [x for x in out if x.get("name") == project]
    return out


def list_prompts(*, project: Optional[str] = None, agent: Optional[str] = None, state: Optional[str] = None, target: Optional[str] = None, prompt: Optional[str] = None) -> List[Dict[str, str]]:
    """Return a flat list of prompt rows from the index with fields {project, agent, prompt, path, state, target, engine, orchestration}.

    Wildcards: '*' supported in project/agent/prompt/state/target. All filters are ANDed, with target applied last.
    """
    conn = _ensure_db(); cur = conn.cursor()
    try:
        rx_t = _rx(target)
        where, params = _build_where_and_params(project, agent, prompt, state)
        # Ensure we only select prompt rows
        where = ["prompt != ''"] + [w for w in where if w != "1=1"]
        sql = "SELECT project, agent, prompt, path, state, target, engine, orchestration, type, rel_path, url, goal FROM repo WHERE " + " AND ".join(where)
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        for prj, ag, pr, path, st, tgt, eng, orch, typ, rel, url, goal in rows:
            if rx_t and not (tgt and rx_t.match(tgt)):
                continue
            out.append({
                "project": prj or "",
                "agent": ag or "",
                "prompt": pr or "",
                "path": path or "",
                "state": st or "",
                # Target: prompt name/id only
                "target": (tgt or pr or ""),
                "engine": eng or "",
                "orchestration": orch or "",
                "type": typ or "",
                "rel_path": rel or "",
                "url": url or "",
                "goal": goal or "",
            })
        return out
    finally:
        cur.close(); conn.close()


def find_prompts(*, project: Optional[str] = None, agent: Optional[str] = None, prompt: Optional[str] = None, state: Optional[str] = None, target: Optional[str] = None) -> List[Dict[str, str]]:
    """Find prompt records with wildcard support. Returns an array of rows."""
    return list_prompts(project=project, agent=agent, state=state, target=target, prompt=prompt)


def find_agents(*, project: Optional[str] = None, agent: Optional[str] = None, target: Optional[str] = None) -> List[Dict[str, str]]:
    """Find agent rows (prompt-empty) with wildcard filters. Returns an array of rows."""
    conn = _ensure_db(); cur = conn.cursor()
    try:
        rx_t = _rx(target)
        where, params = _build_where_and_params(project, agent, None, None)
        # Only non-prompt agent rows
        where = ["(prompt IS NULL OR prompt = '')", "agent != ''"] + [w for w in where if w != "1=1"]
        sql = "SELECT project, agent, path, target, type, rel_path, url, goal FROM repo WHERE " + " AND ".join(where)
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        for prj, ag, path, tgt, typ, rel, url, goal in rows:
            if rx_t and not (tgt and rx_t.match(tgt)):
                continue
            out.append({
                "project": prj or "",
                "agent": ag or "",
                "path": path or "",
                "target": tgt or f"a:{prj}/{ag}",
                "type": typ or "",
                "rel_path": rel or "",
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
        sql = "SELECT project, path, target, type, rel_path, url, goal FROM repo WHERE " + " AND ".join(where)
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        for prj, path, tgt, typ, rel, url, goal in rows:
            if rx_t and not (tgt and rx_t.match(tgt)):
                continue
            out.append({
                "project": prj or "",
                "path": path or "",
                "target": tgt or f"p:{prj}",
                "type": typ or "",
                "rel_path": rel or "",
                "url": url or "",
                "goal": goal or "",
            })
        return out
    finally:
        cur.close(); conn.close()
