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


def _upsert_row(
    cur,
    *,
    target: str,
    project: str,
    agent: str,
    prompt: str,
    abs_path: str,
    state: str | None = None,
    engine: str | None = None,
    orchestration: str | None = None,
    type: str | None = None,
    rel_path: str | None = None,
    url: str | None = None,
    goal: str | None = None,
    card: str | None = None,
) -> None:
    try:
        cur.execute("SELECT project, agent, prompt, path, state, engine, orchestration, type, rel_path, url, goal, card FROM repo WHERE target = ?", (target,))
        old = cur.fetchone()
        if old is not None:
            old_project, old_agent, old_prompt, old_path, old_state, old_engine, old_orch, old_type, old_rel, old_url, old_goal, old_card = [x or "" for x in old]
            # Enforce precedence: project > agent > prompt. Do not let lower-precedence overwrite higher-precedence rows.
            try:
                new_type = str(type or "")
                if (old_type == "project") and (new_type in ("agent", "prompt")):
                    return  # keep project row
                if (old_type == "agent") and (new_type == "prompt"):
                    return  # keep agent row
            except Exception:
                pass
            eff_project = project or old_project
            eff_agent = agent or old_agent
            eff_prompt = prompt or old_prompt
            new_path = abs_path or ""
            eff_path = new_path or old_path
            new_card = (card or "")
            eff_card = new_card or old_card
            try:
                if (eff_prompt or old_prompt):  # prompt row
                    old_is_prompt_file = isinstance(old_path, str) and old_path.lower().endswith(('.md',)) and (("\\prompt\\" in old_path.lower()) or ('/prompt/' in old_path.lower()))
                    new_is_agent_card = isinstance(new_path, str) and (new_path.lower().endswith(('agent.md',)))
                    if old_is_prompt_file and new_is_agent_card:
                        eff_path = old_path
                        # Preserve prompt card if replacing with agent card placeholder
                        eff_card = old_card or eff_card
            except Exception:
                pass
            eff_state = (state or "") or old_state
            eff_engine = (engine or "") or old_engine
            eff_orch = (orchestration or "") or old_orch
            eff_type = (type or "") or old_type
            eff_rel = (rel_path or "") or old_rel
            eff_url = (url or "") or old_url
            eff_goal = (goal or "") or old_goal
            if old_path and eff_path and (old_path != eff_path):
                debug_print("[repo.scan] overwrite", f"target={target}", f"old={old_path}", f"new={eff_path}")
            cur.execute(
                "REPLACE INTO repo (target, project, agent, prompt, path, state, engine, orchestration, type, rel_path, url, goal, card) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (target, eff_project, eff_agent, eff_prompt, eff_path, eff_state, eff_engine, eff_orch, eff_type, eff_rel, eff_url, eff_goal, eff_card),
            )
        else:
            cur.execute(
                "REPLACE INTO repo (target, project, agent, prompt, path, state, engine, orchestration, type, rel_path, url, goal, card) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (target, project, agent, prompt, abs_path, state or "", engine or "", orchestration or "", (type or ""), (rel_path or ""), (url or ""), (goal or ""), (card or "")),
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


def _scan_agent_repo(cur) -> tuple[int, list[dict]]:
    scanned = 0
    directories: list[dict] = []
    try:
        arepo = discover_agent_repo()
    except Exception as e:
        debug_print("[repo.scan] agent repo not found", str(e))
        return scanned, []

    # Enumerate projects (filesystem only in sync)
    try:
        projects = [d.name for d in Path(arepo).iterdir() if d.is_dir() and not d.name.startswith('.')]
    except Exception:
        projects = []

    # Helpers for path/url composition
    import os as _os
    GITHUB_ORG = _os.environ.get("GITHUB_REMOTE_ORGANIZATION_URL", "").rstrip("/")
    GITHUB_BRANCH = _os.environ.get("GITHUB_BRANCH", "main").strip()

    def _rel_url(abs_path: Path) -> tuple[str, str]:
        try:
            rel_inside = abs_path.relative_to(Path(arepo)).as_posix()
        except Exception:
            rel_inside = abs_path.name
        rel_with_repo = f"agent/{rel_inside}"
        url = f"{GITHUB_ORG}/agent/blob/{GITHUB_BRANCH}/{rel_inside}" if (GITHUB_ORG and rel_inside) else ""
        return rel_with_repo, url

    def _read_meta(md_path: Path) -> dict:
        try:
            text = md_path.read_text(encoding="utf-8")
            y0 = text.index("<!-- METADATA:START -->")
            y1 = text.index("```yaml", y0) + len("```yaml")
            y2 = text.index("```", y1)
            import yaml as _yaml
            meta = _yaml.safe_load(text[y1:y2]) or {}
            return meta if isinstance(meta, dict) else {}
        except Exception:
            return {}

    for pname in projects:
        pdir = Path(arepo) / pname
        if not pdir.exists():
            continue
        per_project_agents = 0
        # Project-level MD
        proj_md = pdir / "project.md"
        eng = ""; orch = ""
        if proj_md.exists():
            meta = _read_meta(proj_md)
            eng = str(meta.get("engine") or "")
            orch = str(meta.get("orchestration") or "")
            goal = str(meta.get("goal") or meta.get("purpose") or "")
            relp, url = _rel_url(proj_md)
            _upsert_row(
                cur,
                target=pname,
                project=pname,
                agent="",
                prompt="",
                abs_path=str(proj_md),
                state="",
                engine=eng,
                orchestration=orch,
                type="project",
                rel_path=relp,
                url=url,
                goal=goal,
                card=relp,
            )
            scanned += 1

        # Root agent.md
        f = pdir / "agent.md"
        if f.exists():
            ag_name = _read_agent_name(f, default=pname)
            eng = ""; orch = ""; prompts_list: list[str] = []
            meta = _read_meta(f)
            eng = str(meta.get("engine") or "")
            orch = str(meta.get("orchestration") or "")
            goal = str(meta.get("goal") or meta.get("purpose") or "")
            pv = meta.get("prompts") or []
            if isinstance(pv, dict):
                prompts_list = [str(k) for k in pv.keys()]
            elif isinstance(pv, builtins.list):
                prompts_list = [str(k) for k in pv]
            relp, url = _rel_url(f)
            _upsert_row(
                cur,
                target=ag_name,
                project=pname,
                agent=ag_name,
                prompt="",
                abs_path=str(f),
                state="",
                engine=eng,
                orchestration=orch,
                type="agent",
                rel_path=relp,
                url=url,
                goal=goal,
                card=relp,
            )
            scanned += 1
            per_project_agents += 1
            for pr_id in (prompts_list or []):
                try:
                    # Prompt declared in agent metadata; use agent path as placeholder
                    _upsert_row(
                        cur,
                        target=pr_id,
                        project=pname,
                        agent=ag_name,
                        prompt=pr_id,
                        abs_path=str(f),
                        state="",
                        engine=eng,
                        orchestration=orch,
                        type="prompt",
                        rel_path=relp,
                        url=url,
                        goal="",
                        card="",
                    )
                    scanned += 1
                except Exception:
                    pass

        # Per-agent subdirs
        try:
            for child in pdir.iterdir():
                try:
                    if not child.is_dir() or child.name.startswith('.'):
                        continue
                    f = child / "agent.md"
                    if not f.exists():
                        continue
                    ag_name = _read_agent_name(f, default=child.name)
                    eng = ""; orch = ""; prompts_list: list[str] = []
                    meta = {}
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
                        meta = {}
                    relp, url = _rel_url(f)
                    _upsert_row(
                        cur,
                        target=ag_name,
                        project=pname,
                        agent=ag_name,
                        prompt="",
                        abs_path=str(f),
                        state="",
                        engine=eng,
                        orchestration=orch,
                        type="agent",
                        rel_path=relp,
                        url=url,
                        goal=str((meta.get("goal") if isinstance(meta, dict) else "") or (meta.get("purpose") if isinstance(meta, dict) else "") or ""),
                        card=relp,
                    )
                    scanned += 1
                    per_project_agents += 1
                    for pr_id in (prompts_list or []):
                        try:
                            _upsert_row(
                                cur,
                                target=pr_id,
                                project=pname,
                                agent=ag_name,
                                prompt=pr_id,
                                abs_path=str(f),
                                state="",
                                engine=eng,
                                orchestration=orch,
                                type="prompt",
                                rel_path=relp,
                                url=url,
                                goal="",
                                card="",
                            )
                            scanned += 1
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception:
            pass
        # Record per-project stats
        try:
            directories.append({"project": pname, "path": str(pdir), "agents": int(per_project_agents)})
        except Exception:
            pass
    return scanned, directories


def _scan_prompt_repo(cur) -> tuple[int, list[dict]]:
    scanned = 0
    directories: list[dict] = []
    try:
        prepo = discover_prompt_repo()
    except Exception as e:
        debug_print("[repo.scan] prompt repo not found", str(e))
        return scanned, []

    roots = [Path(prepo) / "ready", Path(prepo) / "draft"]
    # Aggregate counts per project
    per_project: dict[str, dict] = {}
    import os as _os
    GITHUB_ORG = _os.environ.get("GITHUB_REMOTE_ORGANIZATION_URL", "").rstrip("/")
    GITHUB_BRANCH = _os.environ.get("GITHUB_BRANCH", "main").strip()

    def _rel_url(abs_path: Path) -> tuple[str, str]:
        try:
            # rel path inside prompt repo (ready/... or draft/...)
            rel_inside = abs_path.relative_to(Path(prepo)).as_posix()
        except Exception:
            rel_inside = abs_path.name
        rel_with_repo = f"prompt/{rel_inside}"
        url = f"{GITHUB_ORG}/prompt/blob/{GITHUB_BRANCH}/{rel_inside}" if (GITHUB_ORG and rel_inside) else ""
        return rel_with_repo, url
    # Also scan top-level project directories in prompt repo for project.md
    try:
        for child in Path(prepo).iterdir():
            if not child.is_dir() or child.name.startswith('.') or child.name in ("ready", "draft"):
                continue
            proj_name = child.name
            proj_md = child / "project.md"
            if proj_md.exists():
                try:
                    meta = _read_prompt_metadata(proj_md) or {}
                except Exception:
                    meta = {}
                eng = str(meta.get("engine") or "")
                orch = str(meta.get("orchestration") or "")
                goal = str(meta.get("goal") or meta.get("purpose") or "")
                relp, url = _rel_url(proj_md)
                _upsert_row(
                    cur,
                    target=proj_name,
                    project=proj_name,
                    agent="",
                    prompt="",
                    abs_path=str(proj_md),
                    state="",
                    engine=eng,
                    orchestration=orch,
                    type="project",
                    rel_path=relp,
                    url=url,
                    goal=goal,
                    card=relp,
                )
                scanned += 1
    except Exception:
        pass

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
                    goal = str(meta.get("goal") or meta.get("purpose") or "")
                    if (not proj) or (not agent):
                        debug_print("[repo.scan]", "[WARN]", f"Prompt MD missing project/agent: {p}")
                except Exception:
                    pr_id = p.stem
                target = pr_id
                state = "draft" if ("draft" in str(p).lower()) else "ready"
                relp, url = _rel_url(p)
                _upsert_row(
                    cur,
                    target=target,
                    project=proj,
                    agent=agent,
                    prompt=pr_id,
                    abs_path=str(p),
                    state=state,
                    engine=eng,
                    orchestration=orch,
                    type="prompt",
                    rel_path=relp,
                    url=url,
                    goal=(goal if 'goal' in locals() else ""),
                    card=relp,
                )
                scanned += 1
                # Aggregate per-project counts
                try:
                    if proj:
                        d = per_project.setdefault(proj, {"ready": 0, "draft": 0})
                        d[state] = int(d.get(state, 0)) + 1
                except Exception:
                    pass
        except Exception:
            continue
    # Emit directories summary for projects we saw
    try:
        for proj, counts in per_project.items():
            try:
                directories.append({
                    "project": proj,
                    "path": str(Path(prepo) / proj),
                    "ready": int(counts.get("ready", 0)),
                    "draft": int(counts.get("draft", 0)),
                    "prompts": int(counts.get("ready", 0)) + int(counts.get("draft", 0)),
                })
            except Exception:
                continue
    except Exception:
        pass
    return scanned, directories


def reload(repos: Optional[List[str]] = None) -> Dict[str, object]:
    """Scan configured repos and update the repo.db index. (Filesystem only)"""
    conn = repo_db._ensure_db()
    cur = conn.cursor()

    repos = (repos or []) or _load_repos_from_env()
    if not repos:
        repos = ["agent", "prompt"]

    scanned = 0
    repos_out: list[dict] = []
    try:
        for r in repos:
            if r == "agent":
                s_count, dirs = _scan_agent_repo(cur)
                scanned += s_count
                # Try to include repo root for convenience
                try:
                    arepo = discover_agent_repo()
                    repos_out.append({"name": "agent", "root": str(arepo), "directories": dirs})
                except Exception:
                    repos_out.append({"name": "agent", "directories": dirs})
            elif r == "prompt":
                s_count, dirs = _scan_prompt_repo(cur)
                scanned += s_count
                try:
                    prepo = discover_prompt_repo()
                    repos_out.append({"name": "prompt", "root": str(prepo), "directories": dirs})
                except Exception:
                    repos_out.append({"name": "prompt", "directories": dirs})
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
        return {"ok": True, "scanned": scanned, "repos": repos_out}
    except Exception as e:
        conn.rollback()
        return {"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR", "scanned": scanned}
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
