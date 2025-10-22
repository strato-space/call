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


def _read_card_text(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        try:
            debug_print("[repo.scan] read_card_text failed", str(path), str(exc))
        except Exception:
            pass
        return ""


def _load_repos_from_env() -> List[str]:
    raw = os.environ.get("repos", "")
    toks = [t.strip().lower() for t in raw.replace(";", ",").split(",") if t.strip()]
    return [t for t in toks if t in {"agent", "prompt"}]


# todo - add othes repos of any structurue 0 not jusy agent and prompt
def _validate_env_repos(repos: List[str]) -> None:
    tokens = [str(r).strip().lower() for r in repos if str(r).strip()]
    missing: set[str] = set()
    for token in tokens:
        try:
            if token == "agent":
                discover_agent_repo()
            elif token == "prompt":
                discover_prompt_repo()
            else:
                # Unknown tokens are ignored silently (custom repos)
                continue
        except Exception:
            missing.add(token)
    if missing:
        message = (
            "Configured 'repos' entries not found: "
            + ", ".join(sorted(missing))
            + ". Ensure call/tools/repos.sh cloned them or update .env"
        )
        try:
            debug_print("[repo.fs]", message)
        except Exception:
            pass
        raise FileNotFoundError(message)


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
        cur.execute(
            "REPLACE INTO repo (target, project, agent, prompt, path, state, engine, orchestration, type, rel_path, url, goal, card) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                target,
                project or "",
                agent or "",
                prompt or "",
                abs_path or "",
                state or "",
                engine or "",
                orchestration or "",
                type or "",
                rel_path or "",
                url or "",
                goal or "",
                card or "",
            ),
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
                cand = (
                    meta.get("id") or meta.get("name") or meta.get("title") or ""
                ).strip()
                if not cand:
                    cand = path.stem
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
        projects = [
            d.name
            for d in Path(arepo).iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]
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
        url = (
            f"{GITHUB_ORG}/agent/blob/{GITHUB_BRANCH}/{rel_inside}"
            if (GITHUB_ORG and rel_inside)
            else ""
        )
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
        eng = ""
        orch = ""
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
                card=_read_card_text(proj_md),
            )
            scanned += 1

        # Root agent.md
        f = pdir / "agent.md"
        if f.exists():
            ag_name = _read_agent_name(f, default=pname)
            eng = ""
            orch = ""
            prompts_list: list[str] = []
            meta = _read_meta(f)
            eng = str(meta.get("engine") or "")
            orch = str(meta.get("orchestration") or "")
            goal = str(meta.get("goal") or meta.get("purpose") or "")
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
                card=_read_card_text(f),
            )
            scanned += 1
            per_project_agents += 1
        # Per-agent subdirs
        try:
            for child in pdir.iterdir():
                try:
                    if not child.is_dir() or child.name.startswith("."):
                        continue
                    f = child / "agent.md"
                    if not f.exists():
                        continue
                    ag_name = _read_agent_name(f, default=child.name)
                    eng = ""
                    orch = ""
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
                        goal=str(
                            (meta.get("goal") if isinstance(meta, dict) else "")
                            or (meta.get("purpose") if isinstance(meta, dict) else "")
                            or ""
                        ),
                        card=_read_card_text(f),
                    )
                    scanned += 1
                    per_project_agents += 1
                except Exception:
                    continue
        except Exception:
            pass
        # Record per-project stats
        try:
            directories.append(
                {"project": pname, "path": str(pdir), "agents": int(per_project_agents)}
            )
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
    # Aggregate counts per root (ready/draft) for directory summary
    per_root_counts: dict[str, int] = {"ready": 0, "draft": 0}
    # Additional aggregations for output
    per_project_agents_prompt_repo: dict[str, int] = {}
    per_project_has_project_card: set[str] = set()
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
        url = (
            f"{GITHUB_ORG}/prompt/blob/{GITHUB_BRANCH}/{rel_inside}"
            if (GITHUB_ORG and rel_inside)
            else ""
        )
        return rel_with_repo, url

    # Phase 1A: scan top-level project directories in prompt repo for project.md (hierarchical)
    try:
        for child in Path(prepo).iterdir():
            if (
                not child.is_dir()
                or child.name.startswith(".")
                or child.name in ("ready", "draft")
            ):
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
                card_text = _read_card_text(proj_md)
                # Insert project card (type=project)
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
                    card=card_text,
                )
                scanned += 1
                # Also insert as executable prompt (project -> prompt without agent)
                _upsert_row(
                    cur,
                    target=proj_name,
                    project=proj_name,
                    agent="",
                    prompt=proj_name,
                    abs_path=str(proj_md),
                    state="ready",
                    engine=eng,
                    orchestration=orch,
                    type="prompt",
                    rel_path=relp,
                    url=url,
                    goal=goal,
                    card=card_text,
                )
                scanned += 1
                try:
                    per_project_has_project_card.add(proj_name)
                except Exception:
                    pass
    except Exception:
        pass

    # Phase 1B: scan hierarchical agent cards under prompt/<Project>/**/agent.md (exclude ready/draft)
    try:
        for agent_md in Path(prepo).rglob("agent.md"):
            try:
                # Skip ready/draft trees
                parts = agent_md.resolve().parts
                if any((p.lower() == "ready" or p.lower() == "draft") for p in parts):
                    continue
                # Derive project from first segment under prepo
                rel = agent_md.resolve().relative_to(Path(prepo).resolve())
                proj_name = rel.parts[0] if len(rel.parts) >= 1 else ""
            except Exception:
                proj_name = ""
            # Read agent name and metadata
            ag_name = _read_agent_name(agent_md, default=agent_md.parent.name)
            meta = _read_prompt_metadata(agent_md) or {}
            eng = str(meta.get("engine") or "")
            orch = str(meta.get("orchestration") or "")
            goal = str(meta.get("goal") or meta.get("purpose") or "")
            prompts_list: list[str] = []
            try:
                pv = meta.get("prompts") or []
                if isinstance(pv, dict):
                    prompts_list = [str(k) for k in pv.keys()]
                elif isinstance(pv, builtins.list):
                    prompts_list = [str(k) for k in pv]
            except Exception:
                prompts_list = []
            relp, url = _rel_url(agent_md)
            _upsert_row(
                cur,
                target=ag_name,
                project=str(meta.get("project") or proj_name or ""),
                agent=ag_name,
                prompt="",
                abs_path=str(agent_md),
                state="",
                engine=eng,
                orchestration=orch,
                type="agent",
                rel_path=relp,
                url=url,
                goal=goal,
                card=_read_card_text(agent_md),
            )
            scanned += 1
            try:
                _pname = str(meta.get("project") or proj_name or "")
                if _pname:
                    per_project_agents_prompt_repo[_pname] = (
                        int(per_project_agents_prompt_repo.get(_pname, 0)) + 1
                    )
            except Exception:
                pass
            # Insert placeholder prompts declared on the agent card
            for pr_id in prompts_list or []:
                try:
                    _upsert_row(
                        cur,
                        target=str(pr_id),
                        project=str(meta.get("project") or proj_name or ""),
                        agent=ag_name,
                        prompt=str(pr_id),
                        abs_path=str(agent_md),
                        state="",
                        engine=eng,
                        orchestration=orch,
                        type="prompt",
                        rel_path=relp,
                        url=url,
                        goal="",
                        card=_read_card_text(agent_md),
                    )
                    scanned += 1
                except Exception:
                    pass
    except Exception:
        pass

    # Phase 2: scan flat trees ready/ and draft/ for prompt cards ONLY (policy)
    for root in roots:
        if not root.exists():
            continue
        try:
            try:
                files = builtins.list(root.rglob("*.md"))
            except Exception as ge:
                debug_print("[repo.scan] prompt glob error", f"root={root}", str(ge))
                files = []
            debug_print(
                "[repo.scan] prompt root", f"path={root}", f"files={len(files)}"
            )
            for p in files:
                proj = ""
                agent = ""
                pr_id = ""
                eng = ""
                orch = ""
                goal = ""
                # Treat every file in ready/draft as a prompt card
                try:
                    meta = _read_prompt_metadata(p) or {}
                    # Determine state first for warnings
                    state_name = root.name.lower()
                    # Warn about missing metadata only for ready files
                    if not meta and state_name == "ready":
                        debug_print(
                            "[repo.scan]", "[WARN]", f"Prompt MD missing METADATA (ready state): {p}"
                        )
                    pr_id = str(meta.get("id") or p.stem)
                    proj = str(meta.get("project") or "")
                    agent = str(meta.get("agent") or "")
                    eng = str(meta.get("engine") or "")
                    orch = str(meta.get("orchestration") or "")
                    goal = str(meta.get("goal") or meta.get("purpose") or "")
                    # Only project is required for ready; agent is optional; draft can be without project
                    if not proj and state_name == "ready":
                        debug_print(
                            "[repo.scan]",
                            "[WARN]",
                            f"Prompt MD missing project (ready state): {p}",
                        )
                except Exception:
                    pr_id = p.stem
                target = pr_id
                # Determine state based on root directory name
                try:
                    state = root.name.lower()
                    if state not in ("ready", "draft"):
                        state = (
                            "ready"
                            if ("ready" in str(root).lower())
                            else (
                                "draft" if ("draft" in str(root).lower()) else "ready"
                            )
                        )
                except Exception:
                    state = "ready" if ("ready" in str(p).lower()) else "draft"
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
                    goal=(goal if "goal" in locals() else ""),
                    card=_read_card_text(p),
                )
                scanned += 1
                # Aggregate per-root counts (ready/draft)
                try:
                    if state in per_root_counts:
                        per_root_counts[state] = int(per_root_counts.get(state, 0)) + 1
                except Exception:
                    pass
                # Aggregate per-project counts
                try:
                    if proj:
                        d = per_project.setdefault(proj, {"ready": 0, "draft": 0})
                        d[state] = int(d.get(state, 0)) + 1
                except Exception:
                    pass
        except Exception:
            continue
    # Emit directories summary for REAL directories under prompt root only (no virtual)
    try:
        all_projects: set[str] = (
            set(per_project.keys())
            | set(per_project_agents_prompt_repo.keys())
            | set(per_project_has_project_card)
        )
        for proj in sorted(all_projects):
            try:
                pdir = Path(prepo) / proj
                if not pdir.exists() or not pdir.is_dir():
                    continue
                counts = per_project.get(proj, {})
                total_prompts = int(counts.get("ready", 0)) + int(
                    counts.get("draft", 0)
                )
                entry = {
                    "project": proj,
                    "path": str(pdir),
                    "prompts": total_prompts,
                }
                # Indicate presence of project card (project.md) in this directory
                has_proj_card = proj in per_project_has_project_card
                if has_proj_card:
                    entry["project_card"] = True
                    # Include agents count discovered hierarchically under prompt/<Project>/** if > 0
                    agc = int(per_project_agents_prompt_repo.get(proj, 0))
                    if agc > 0:
                        entry["agents_project"] = agc
                directories.append(entry)
            except Exception:
                continue
        # Also include ready/ and draft/ directories with total prompt counts if they exist
        for state_name in ("ready", "draft"):
            try:
                sdir = Path(prepo) / state_name
                if not sdir.exists() or not sdir.is_dir():
                    continue
                directories.append(
                    {
                        "project": state_name,
                        "path": str(sdir),
                        "prompts": int(per_root_counts.get(state_name, 0)),
                    }
                )
            except Exception:
                continue
    except Exception:
        pass
    return scanned, directories


def reload(
    repos: Optional[List[str]] = None, *, full_form: bool = True
) -> Dict[str, object]:
    """Scan configured repos and update the repo.db index. (Filesystem only)"""
    conn = repo_db._ensure_db()
    cur = conn.cursor()

    if repos:
        repos = [str(r).strip().lower() for r in repos if str(r).strip()]
    if not repos:
        repos = _load_repos_from_env()
    if not repos:
        repos = ["agent", "prompt"]

    _validate_env_repos(repos)

    scanned = 0
    repos_out: list[dict] = []
    try:
        for r in repos:
            if r == "agent":
                s_count, dirs = _scan_agent_repo(cur)
                scanned += s_count
                if full_form:
                    try:
                        arepo = discover_agent_repo()
                        repos_out.append(
                            {"name": "agent", "root": str(arepo), "directories": dirs}
                        )
                    except Exception:
                        repos_out.append({"name": "agent", "directories": dirs})
                else:
                    repos_out.append({"name": "agent"})
            elif r == "prompt":
                s_count, dirs = _scan_prompt_repo(cur)
                scanned += s_count
                if full_form:
                    try:
                        prepo = discover_prompt_repo()
                        repos_out.append(
                            {"name": "prompt", "root": str(prepo), "directories": dirs}
                        )
                    except Exception:
                        repos_out.append({"name": "prompt", "directories": dirs})
                else:
                    repos_out.append({"name": "prompt"})
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
        return {
            "ok": False,
            "error_code": 500,
            "description": str(e),
            "code": "INTERNAL_ERROR",
            "scanned": scanned,
        }
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
