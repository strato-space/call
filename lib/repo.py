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
    # Schema: minimal now, extensible later
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS repo (
            target  TEXT PRIMARY KEY,
            project TEXT,
            agent   TEXT,
            prompt  TEXT,
            path    TEXT
        )
        """
    )
    # Helpful indices for filters
    cur.execute("CREATE INDEX IF NOT EXISTS idx_repo_project ON repo(project)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_repo_agent   ON repo(agent)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_repo_prompt  ON repo(prompt)")
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


def _upsert_row(cur: sqlite3.Cursor, *, target: str, project: str, agent: str, prompt: str, path: str) -> None:
    try:
        cur.execute("SELECT path FROM repo WHERE target = ?", (target,))
        row = cur.fetchone()
        if row is not None and row[0] != path:
            debug_print("[repo.scan] overwrite", f"target={target}", f"old={row[0]}", f"new={path}")
        cur.execute(
            "REPLACE INTO repo (target, project, agent, prompt, path) VALUES (?, ?, ?, ?, ?)",
            (target, project, agent, prompt, path),
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
        # Project-level definition
        proj_yaml = pdir / "project.yaml"
        proj_md   = pdir / "project.md"
        proj_path = str(proj_yaml if proj_yaml.exists() else (proj_md if proj_md.exists() else pdir))
        _upsert_row(cur, target=f"p:{pname}", project=pname, agent="", prompt="", path=proj_path)
        scanned += 1

        # Root project agent (optional)
        for fname in ("agent.yaml", "agent.md"):
            f = pdir / fname
            if f.exists():
                ag_name = _read_agent_name(f, default=pname)
                _upsert_row(cur, target=f"a:{pname}/{ag_name}", project=pname, agent=ag_name, prompt="", path=str(f))
                scanned += 1
                break

        # Per-agent subdirectories
        try:
            for child in pdir.iterdir():
                if not child.is_dir() or child.name.startswith('.'):
                    continue
                for fname in ("agent.yaml", "agent.md"):
                    f = child / fname
                    if f.exists():
                        ag_name = _read_agent_name(f, default=child.name)
                        _upsert_row(cur, target=f"a:{pname}/{ag_name}", project=pname, agent=ag_name, prompt="", path=str(f))
                        scanned += 1
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
            for p in list(root.glob("*.md")) + list(root.glob("*.yaml")) + list(root.glob("*.yml")):
                proj = ""
                agent = ""
                pr_id = ""
                try:
                    if p.suffix.lower() == ".md":
                        meta = _read_prompt_metadata(p) or {}
                        pr_id = str(meta.get("id") or p.stem)
                        proj = str(meta.get("project") or "")
                        agent = str(meta.get("agent") or "")
                    else:
                        import yaml
                        y = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                        pr_id = str(y.get("id") or p.stem)
                        proj = str(y.get("project") or "")
                        agent = str(y.get("agent") or "")
                except Exception:
                    pr_id = p.stem
                target = f"r:{proj}/{agent}/{pr_id}" if (proj or agent) else f"r::{pr_id}"
                _upsert_row(cur, target=target, project=proj, agent=agent, prompt=pr_id, path=str(p))
                scanned += 1
        except Exception:
            continue
    return scanned


def scan() -> Dict[str, object]:
    """Scan configured repos and update the repo.db index.

    Returns: { ok: bool, scanned: <int> }
    """
    conn = _ensure_db()
    cur = conn.cursor()

    repos = _load_repos_from_env()
    if not repos:
        # Default to scanning both if unspecified
        repos = ["agent", "prompt"]

    scanned = 0
    try:
        for r in repos:
            if r == "agent":
                scanned += _scan_agent_repo(cur)
            elif r == "prompt":
                scanned += _scan_prompt_repo(cur)
        conn.commit()
        debug_print("[repo.scan] done", f"repos={repos}", f"scanned={scanned}")
        return {"ok": True, "scanned": scanned}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error": str(e), "scanned": scanned}
    finally:
        cur.close(); conn.close()


def list(*, project: Optional[str] = None, agent: Optional[str] = None, prompt: Optional[str] = None) -> List[Dict[str, object]]:
    """Query the repo.db index and return a hierarchical structure (projects -> agents -> prompts).

    Wildcards: '*' supported in filters (case-insensitive, full-token match).
    """
    conn = _ensure_db()
    cur = conn.cursor()

    def _rx(pat: Optional[str]):
        if not pat:
            return None
        return re.compile("^" + re.escape(pat).replace("\\*", ".*") + "$", re.IGNORECASE)

    rx_p = _rx(project)
    rx_a = _rx(agent)
    rx_r = _rx(prompt)

    cur.execute("SELECT project, agent, prompt, path FROM repo")
    rows = cur.fetchall()
    cur.close(); conn.close()

    # Filter in Python for simplicity
    items: List[Tuple[str, str, str, str]] = []
    for prj, ag, pr, path in rows:
        if rx_p and not (prj and rx_p.match(prj)):
            # Allow project-less prompts to pass only when no project filter
            continue
        if rx_a and not ((ag and rx_a.match(ag)) or (not ag and agent is None)):
            continue
        if rx_r and not ((pr and rx_r.match(pr)) or (not pr and prompt is None)):
            continue
        items.append((prj or "", ag or "", pr or "", path or ""))

    # Build hierarchy: project -> agents[] (name/path/prompts[])
    proj_map: Dict[str, Dict[str, object]] = {}
    for prj, ag, pr, path in items:
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
            if pr not in prompts:
                prompts.append(pr)
        # Update path for agent rows when pr is empty (agent-level record)
        if ag and not pr and path:
            agent_entry["path"] = path

    # If project filter without wildcard, return only that project
    out = list(proj_map.values())
    if project and "*" not in project:
        out = [x for x in out if x.get("name") == project]
    return out
