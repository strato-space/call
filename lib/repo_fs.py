"""
Filesystem-only sync for repository scanning (MD-only cards).

Policy:
- ALL filesystem operations (walking directories, reading Markdown) live here.
- Runtime API must not import this module; it should only use `call.lib.repo_db` for DB-only queries.

Functions:
- scan(repos?: list[str]) -> dict { ok: bool, scanned: int }
"""
from __future__ import annotations

import builtins
import os
from pathlib import Path
from typing import Dict, List, Optional

from call.lib.logging import debug_print
from call.lib.discovery import (
    discover_agent_repo,
    discover_prompt_repo,
    _read_prompt_metadata,
)
from call.lib import repo_db


def _load_repos_from_env() -> List[str]:
    raw = os.environ.get("repos", "")
    toks = [t.strip().lower() for t in raw.replace(";", ",").split(",") if t.strip()]
    return [t for t in toks if t in {"agent", "prompt"}]


def _upsert_row(cur, *, target: str, project: str, agent: str, prompt: str, path: str, state: str | None = None, engine: str | None = None, orchestration: str | None = None) -> None:
    try:
        cur.execute("SELECT project, agent, prompt, path, state, engine, orchestration FROM repo WHERE target = ?", (target,))
        old = cur.fetchone()
        if old is not None:
            old_project, old_agent, old_prompt, old_path, old_state, old_engine, old_orch = [x or "" for x in old]
            eff_project = project or old_project
            eff_agent = agent or old_agent
            eff_prompt = prompt or old_prompt
            new_path = path or ""
            eff_path = new_path or old_path
            try:
                if (eff_prompt or old_prompt):  # prompt row
                    old_is_prompt_file = isinstance(old_path, str) and old_path.lower().endswith(('.md',)) and (('\\prompt\\' in old_path.lower()) or ('/prompt/' in old_path.lower()))
                    new_is_agent_card = isinstance(new_path, str) and (new_path.lower().endswith(('agent.md',)))
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


def _read_agent_name(path: Path, *, default: str) -> str:
    name = default
    try:
        if str(path).lower().endswith((".md", ".markdown")):
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


def _scan_agent_repo(cur) -> int:
    scanned = 0
    try:
        arepo = discover_agent_repo()
    except Exception as e:
        debug_print("[repo.scan] agent repo not found", str(e))
        return scanned

    # Enumerate projects (filesystem only in sync)
    try:
        projects = [d.name for d in Path(arepo).iterdir() if d.is_dir() and not d.name.startswith('.')]
    except Exception:
        projects = []

    for pname in projects:
        pdir = Path(arepo) / pname
        if not pdir.exists():
            continue
        # Project-level MD
        proj_md = pdir / "project.md"
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
            _upsert_row(cur, target=pname, project=pname, agent="", prompt="", path=str(proj_md), state="", engine=eng, orchestration=orch)
            scanned += 1

        # Root agent.md
        f = pdir / "agent.md"
        if f.exists():
            ag_name = _read_agent_name(f, default=pname)
            eng = ""; orch = ""; prompts_list: list[str] = []
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
            _upsert_row(cur, target=ag_name, project=pname, agent=ag_name, prompt="", path=str(f), state="", engine=eng, orchestration=orch)
            scanned += 1
            for pr_id in (prompts_list or []):
                try:
                    _upsert_row(cur, target=pr_id, project=pname, agent=ag_name, prompt=pr_id, path=str(f), state="", engine=eng, orchestration=orch)
                    scanned += 1
                except Exception:
                    pass

        # Per-agent subdirs
        try:
            for child in pdir.iterdir():
                if not child.is_dir() or child.name.startswith('.'):
                    continue
                f = child / "agent.md"
                if not f.exists():
                    continue
                ag_name = _read_agent_name(f, default=child.name)
                eng = ""; orch = ""; prompts_list: list[str] = []
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
                _upsert_row(cur, target=ag_name, project=pname, agent=ag_name, prompt="", path=str(f), state="", engine=eng, orchestration=orch)
                scanned += 1
                for pr_id in (prompts_list or []):
                    try:
                        _upsert_row(cur, target=pr_id, project=pname, agent=ag_name, prompt=pr_id, path=str(f), state="", engine=eng, orchestration=orch)
                        scanned += 1
                    except Exception:
                        pass
        except Exception:
            pass
    return scanned


def _scan_prompt_repo(cur) -> int:
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
                files = builtins.list(root.rglob("*.md"))
            except Exception as ge:
                debug_print("[repo.scan] prompt glob error", f"root={root}", str(ge))
                files = []
            debug_print("[repo.scan] prompt root", f"path={root}", f"files={len(files)}")
            for p in files:
                proj = ""; agent = ""; pr_id = ""; eng = ""; orch = ""
                try:
                    meta = _read_prompt_metadata(p) or {}
                    if not meta:
                        debug_print("[repo.scan]", "[WARN]", f"Prompt MD missing METADATA: {p}")
                    pr_id = str(meta.get("id") or p.stem)
                    proj = str(meta.get("project") or "")
                    agent = str(meta.get("agent") or "")
                    eng = str(meta.get("engine") or "")
                    orch = str(meta.get("orchestration") or "")
                    if (not proj) or (not agent):
                        debug_print("[repo.scan]", "[WARN]", f"Prompt MD missing project/agent: {p}")
                except Exception:
                    pr_id = p.stem
                target = pr_id
                state = "draft" if ("draft" in str(p).lower()) else "ready"
                _upsert_row(cur, target=target, project=proj, agent=agent, prompt=pr_id, path=str(p), state=state, engine=eng, orchestration=orch)
                scanned += 1
        except Exception:
            continue
    return scanned


def scan(repos: Optional[List[str]] = None) -> Dict[str, object]:
    """Scan configured repos and update the repo.db index. (Filesystem only)"""
    conn = repo_db._ensure_db()
    cur = conn.cursor()

    repos = (repos or []) or _load_repos_from_env()
    if not repos:
        repos = ["agent", "prompt"]

    scanned = 0
    try:
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
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
