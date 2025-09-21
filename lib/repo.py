"""
Repository scanner and index for projects/agents/prompts across multiple repos.

- Reads repos list from environment or call/.env (key: repos, e.g. "agent, prompt").
- Scans:
  - agent repo: projects -> agents (project.yaml/.md, agent.yaml/.md)
  - prompt repo: ready/ and draft/ for .md (with METADATA) and .yaml/.yml
- Stores results in SQLite DB (repo.db) with a single table 'repo':
  - target TEXT PRIMARY KEY   # canonical id: p:<project>, a:<project>/<agent>, r:<project>/<agent>/<prompt>
  - project TEXT
  - agent   TEXT
  - prompt  TEXT
  - path    TEXT              # on-disk path of the defining file

Duplicates: if the same target is discovered with a different path, we log a warning and overwrite (last write wins).

Public API:
- scan() -> dict { ok: bool, scanned: int }
- list(project: str|None=None, agent: str|None=None, prompt: str|None=None) -> list[dict]
  Returns hierarchical structure similar to call.lib.api.list(), built from the DB.
"""
from __future__ import annotations

import os
import builtins
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from call.lib.logging import debug_print
from call.lib.discovery import (
    discover_agent_repo,
    discover_prompt_repo,
    load_projects_index,
    _read_prompt_metadata,  # best-effort; returns dict
)

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
            orchestration TEXT
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
    except Exception:
        pass
    conn.commit()
    return conn


def _load_repos_from_env() -> List[str]:
    # Prefer process env, fallback to call/.env
    raw = os.environ.get("repos", "")
    if not raw:
        try:
            env_path = Path(__file__).resolve().parents[1] / ".env"
            if env_path.exists():
                text = env_path.read_text(encoding="utf-8", errors="ignore")
                # naive parse: repos=agent, prompt
                for line in text.splitlines():
                    if line.strip().startswith("repos="):
                        raw = line.split("=", 1)[1].strip()
                        break
        except Exception:
            raw = ""
    toks = [t.strip().lower() for t in re.split(r"[;,\s]+", raw) if t.strip()]
    # Only supported tokens for now
    return [t for t in toks if t in {"agent", "prompt"}]


def _upsert_row(cur: sqlite3.Cursor, *, target: str, project: str, agent: str, prompt: str, path: str, state: str | None = None, engine: str | None = None, orchestration: str | None = None) -> None:
    try:
        # Merge strategy: preserve existing non-empty project/agent when new values are empty.
        # Prefer new path/state/engine/orchestration when provided (non-empty), otherwise keep old.
        cur.execute("SELECT project, agent, prompt, path, state, engine, orchestration FROM repo WHERE target = ?", (target,))
        old = cur.fetchone()
        if old is not None:
            old_project, old_agent, old_prompt, old_path, old_state, old_engine, old_orch = [x or "" for x in old]
            eff_project = project or old_project
            eff_agent = agent or old_agent
            eff_prompt = prompt or old_prompt
            # Path preference: for prompt rows, prefer existing prompt file path over agent.yaml/md
            new_path = path or ""
            eff_path = new_path or old_path
            try:
                if (eff_prompt or old_prompt):  # prompt row
                    old_is_prompt_file = isinstance(old_path, str) and (old_path.lower().endswith(('.md', '.yaml', '.yml'))) and (('\\prompt\\' in old_path.lower()) or ('/prompt/' in old_path.lower()))
                    new_is_agent_card = isinstance(new_path, str) and (new_path.lower().endswith(('agent.yaml', 'agent.md')))
                    if old_is_prompt_file and new_is_agent_card:
                        eff_path = old_path
            except Exception:
                pass
            eff_state = (state or "") or old_state
            eff_engine = (engine or "") or old_engine
            eff_orch = (orchestration or "") or old_orch
            if old_path and eff_path and (old_path != eff_path):
                debug_print("[repo.scan] overwrite", f"target={target}", f"old={old_path}", f"new={eff_path}")
            cur.execute(
                "REPLACE INTO repo (target, project, agent, prompt, path, state, engine, orchestration) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target, eff_project, eff_agent, eff_prompt, eff_path, eff_state, eff_engine, eff_orch),
            )
        else:
            cur.execute(
                "REPLACE INTO repo (target, project, agent, prompt, path, state, engine, orchestration) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (target, project, agent, prompt, path, state or "", engine or "", orchestration or ""),
            )
    except Exception as e:
        debug_print("[repo.scan] upsert failed", f"target={target}", str(e))


def _scan_agent_repo(cur: sqlite3.Cursor) -> int:
    scanned = 0
    try:
        arepo = discover_agent_repo()
    except Exception as e:
        debug_print("[repo.scan] agent repo not found", str(e))
        return scanned

    # Enumerate projects
    try:
        projects = load_projects_index(arepo)
    except Exception:
        projects = []

    for pname in projects:
        pdir = Path(arepo) / pname
        if not pdir.exists():
            continue
        # Project-level definition: MD-only
        proj_md   = pdir / "project.md"
        eng = ""; orch = ""
        if proj_md.exists():
            try:
                text = proj_md.read_text(encoding="utf-8")
                y0 = text.index("<!-- METADATA:START -->")
                y1 = text.index("```yaml", y0) + len("```yaml")
                y2 = text.index("```", y1)
                import yaml as _yaml
                meta = _yaml.safe_load(text[y1:y2]) or {}
                eng = str(meta.get("engine") or "")
                orch = str(meta.get("orchestration") or "")
            except Exception:
                pass
            # Target for project rows: project name only
            _upsert_row(cur, target=pname, project=pname, agent="", prompt="", path=str(proj_md), state="", engine=eng, orchestration=orch)
            scanned += 1

        # Root project agent (optional, MD-only)
        for fname in ("agent.md",):
            f = pdir / fname
            if f.exists():
                ag_name = _read_agent_name(f, default=pname)
                eng = ""; orch = ""
                prompts_list: list[str] = []
                try:
                    text = f.read_text(encoding="utf-8")
                    y0 = text.index("<!-- METADATA:START -->")
                    y1 = text.index("```yaml", y0) + len("```yaml")
                    y2 = text.index("```", y1)
                    import yaml as _yaml
                    meta = _yaml.safe_load(text[y1:y2]) or {}
                    eng = str(meta.get("engine") or "")
                    orch = str(meta.get("orchestration") or "")
                    pv = meta.get("prompts") or []
                    if isinstance(pv, dict):
                        prompts_list = [str(k) for k in pv.keys()]
                    elif isinstance(pv, builtins.list):
                        prompts_list = [str(k) for k in pv]
                except Exception:
                    pass
                # Target for agent rows: agent name only
                _upsert_row(cur, target=ag_name, project=pname, agent=ag_name, prompt="", path=str(f), state="", engine=eng, orchestration=orch)
                scanned += 1
                # Also index declared prompts for this agent (merge with existing rows)
                for pr_id in (prompts_list or []):
                    try:
                        _upsert_row(cur, target=pr_id, project=pname, agent=ag_name, prompt=pr_id, path=str(f), state="", engine=eng, orchestration=orch)
                        scanned += 1
                    except Exception:
                        pass
                break

        # Per-agent subdirectories (MD-only)
        try:
            for child in pdir.iterdir():
                if not child.is_dir() or child.name.startswith('.'):
                    continue
                for fname in ("agent.md",):
                    f = child / fname
                    if f.exists():
                        ag_name = _read_agent_name(f, default=child.name)
                        eng = ""; orch = ""
                        prompts_list: list[str] = []
                        try:
                            text = f.read_text(encoding="utf-8")
                            y0 = text.index("<!-- METADATA:START -->")
                            y1 = text.index("```yaml", y0) + len("```yaml")
                            y2 = text.index("```", y1)
                            import yaml as _yaml
                            meta = _yaml.safe_load(text[y1:y2]) or {}
                            eng = str(meta.get("engine") or "")
                            orch = str(meta.get("orchestration") or "")
                            pv = meta.get("prompts") or []
                            if isinstance(pv, dict):
                                prompts_list = [str(k) for k in pv.keys()]
                            elif isinstance(pv, builtins.list):
                                prompts_list = [str(k) for k in pv]
                        except Exception:
                            pass
                        # Target for agent rows: agent name only
                        _upsert_row(cur, target=ag_name, project=pname, agent=ag_name, prompt="", path=str(f), state="", engine=eng, orchestration=orch)
                        scanned += 1
                        # Also index declared prompts for this agent (merge with existing rows)
                        for pr_id in (prompts_list or []):
                            try:
                                _upsert_row(cur, target=pr_id, project=pname, agent=ag_name, prompt=pr_id, path=str(f), state="", engine=eng, orchestration=orch)
                                scanned += 1
                            except Exception:
                                pass
                        break
        except Exception:
            pass
    return scanned


def _read_agent_name(path: Path, *, default: str) -> str:
    name = default
    try:
        if str(path).lower().endswith((".yaml", ".yml")):
            import yaml
            y = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            cand = (y.get("id") or y.get("name") or "").strip()
            if cand:
                name = str(cand)
        elif str(path).lower().endswith((".md", ".markdown")):
            # Try to parse METADATA fenced block if present
            text = path.read_text(encoding="utf-8")
            try:
                y0 = text.index("<!-- METADATA:START -->")
                y1 = text.index("```yaml", y0) + len("```yaml")
                y2 = text.index("```", y1)
                import yaml
                meta = yaml.safe_load(text[y1:y2]) or {}
                cand = (meta.get("id") or meta.get("name") or meta.get("title") or "").strip()
                if cand:
                    name = str(cand)
            except Exception:
                pass
    except Exception:
        pass
    return name


def _scan_prompt_repo(cur: sqlite3.Cursor) -> int:
    scanned = 0
    try:
        prepo = discover_prompt_repo()
    except Exception as e:
        debug_print("[repo.scan] prompt repo not found", str(e))
        return scanned

    roots = [Path(prepo) / "ready", Path(prepo) / "draft"]
    for root in roots:
        if not root.exists():
            continue
        try:
            try:
                # Recurse: some repos may nest files or keep helper folders
                md_list = builtins.list(root.rglob("*.md"))
            except Exception as ge:
                debug_print("[repo.scan] prompt glob error", f"root={root}", str(ge))
                md_list = []
            files = md_list
            debug_print("[repo.scan] prompt root", f"path={root}", f"files={len(files)}")
            for p in files:
                proj = ""
                agent = ""
                pr_id = ""
                eng = ""; orch = ""
                try:
                    # MD-only
                    meta = _read_prompt_metadata(p) or {}
                    # Warn when METADATA is missing or empty
                    if not meta:
                        debug_print("[repo.scan]", "[WARN]", f"Prompt MD missing METADATA: {p}")
                    pr_id = str(meta.get("id") or p.stem)
                    proj = str(meta.get("project") or "")
                    agent = str(meta.get("agent") or "")
                    eng = str(meta.get("engine") or "")
                    orch = str(meta.get("orchestration") or "")
                    # Warn when project/agent are not provided in metadata
                    if (not proj) or (not agent):
                        debug_print("[repo.scan]", "[WARN]", f"Prompt MD missing project/agent: {p}")
                except Exception:
                    pr_id = p.stem
                # Target for prompt rows: prompt id/name only
                target = pr_id
                state = "draft" if ("draft" in str(p).lower()) else "ready"
                _upsert_row(cur, target=target, project=proj, agent=agent, prompt=pr_id, path=str(p), state=state, engine=eng, orchestration=orch)
                scanned += 1
        except Exception:
            continue
    return scanned


def scan(repos: Optional[List[str]] = None) -> Dict[str, object]:
    """Scan configured repos and update the repo.db index.

    Returns: { ok: bool, scanned: <int> }
    """
    conn = _ensure_db()
    cur = conn.cursor()

    repos = (repos or []) or _load_repos_from_env()
    if not repos:
        # Default to scanning both if unspecified
        repos = ["agent", "prompt"]

    scanned = 0
    try:
        # Backward-compat cleanup: remove legacy prefixed targets
        try:
            cur.execute("DELETE FROM repo WHERE target LIKE 'p:%' OR target LIKE 'a:%' OR target LIKE 'r:%'")
        except Exception:
            pass
        for r in repos:
            if r == "agent":
                scanned += _scan_agent_repo(cur)
            elif r == "prompt":
                scanned += _scan_prompt_repo(cur)
        # Prune stale prompt rows (file deleted or moved)
        try:
            cur.execute("SELECT target, path, prompt FROM repo")
            stale: list[str] = []
            for tgt, pth, pr in cur.fetchall():
                if pr and pth and not Path(str(pth)).exists():
                    stale.append(str(tgt))
            for tgt in stale:
                cur.execute("DELETE FROM repo WHERE target = ?", (tgt,))
        except Exception:
            pass
        conn.commit()
        debug_print("[repo.scan] done", f"repos={repos}", f"scanned={scanned}")
        return {"ok": True, "scanned": scanned}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR", "scanned": scanned}
    finally:
        cur.close(); conn.close()


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
    sql = "SELECT project, agent, prompt, path, state, target FROM repo WHERE " + " AND ".join(where)
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()
    cur.close(); conn.close()

    # Filter in Python for simplicity
    items: List[Tuple[str, str, str, str, str, str]] = []
    for prj, ag, pr, path, st, tgt in rows:
        # project/agent/prompt
        # target (applied last)
        if rx_t and not (tgt and rx_t.match(tgt)):
            continue
        # prompt filter: keep agent-level rows (pr == ""), and only include prompt rows that match
        if rx_p and pr and not rx_p.match(pr):
            continue
        items.append((prj or "", ag or "", pr or "", path or "", st or "", tgt or ""))

    # Build hierarchy: project -> agents[] (name/path/prompts[])
    proj_map: Dict[str, Dict[str, object]] = {}
    for prj, ag, pr, path, st, tgt in items:
        # Ensure project bucket
        if prj not in proj_map:
            proj_map[prj] = {"name": prj, "type": "project", "agents": []}
        agents_list: List[Dict[str, object]] = proj_map[prj]["agents"]  # type: ignore
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
            if not isinstance(prompts, builtins.list):
                prompts = []
                agent_entry["prompts"] = prompts  # type: ignore
            if pr not in prompts:
                prompts.append(pr)
        # Update path for agent rows when pr is empty (agent-level record)
        if ag and not pr and path:
            agent_entry["path"] = path

    # If project filter without wildcard, return only that project
    out = builtins.list(proj_map.values())
    if project and "*" not in project:
        out = [x for x in out if x.get("name") == project]
    return out


def list_prompts(*, project: Optional[str] = None, agent: Optional[str] = None, state: Optional[str] = None, target: Optional[str] = None, prompt: Optional[str] = None) -> List[Dict[str, str]]:
    """Return a flat list of prompt rows from the index with fields {project, agent, prompt, path, state, target}.

    Wildcards: '*' supported in project/agent/prompt/state/target. All filters are ANDed, with target applied last."""
    conn = _ensure_db(); cur = conn.cursor()
    try:
        rx_t = _rx(target)
        where, params = _build_where_and_params(project, agent, prompt, state)
        # Ensure we only select prompt rows
        where = ["prompt != ''"] + [w for w in where if w != "1=1"]
        sql = "SELECT project, agent, prompt, path, state, target, engine, orchestration FROM repo WHERE " + " AND ".join(where)
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        for prj, ag, pr, path, st, tgt, eng, orch in rows:
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
        sql = "SELECT project, agent, path, target FROM repo WHERE " + " AND ".join(where)
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        for prj, ag, path, tgt in rows:
            if rx_t and not (tgt and rx_t.match(tgt)):
                continue
            out.append({"project": prj or "", "agent": ag or "", "path": path or "", "target": tgt or f"a:{prj}/{ag}"})
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
        sql = "SELECT project, path, target FROM repo WHERE " + " AND ".join(where)
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        out: List[Dict[str, str]] = []
        for prj, path, tgt in rows:
            if rx_t and not (tgt and rx_t.match(tgt)):
                continue
            out.append({"project": prj or "", "path": path or "", "target": tgt or f"p:{prj}"})
        return out
    finally:
        cur.close(); conn.close()
