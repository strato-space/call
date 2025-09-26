"""
Shared discovery and YAML helper utilities for the call subsystem.

This module centralizes functions that were previously implemented in
`call/app/call.py` to avoid circular imports and duplication across the
library API, CLI, and Telegram bot layers.

Provided helpers:
- discover_prompt_repo() -> Path
- _load_agents_index(index_path: Path, base_dir: Path) -> dict[str, Path]
- _scan_agents_dir(base_dir: Path) -> dict[str, tuple[Path, list[str]]]
- _ensure_indices(rep: Path) -> None
- discover_agent_yaml(agent_name: str) -> Path | None
- load_yaml(path: Path) -> dict
- default_samples_dir: str
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from call.lib.logging import debug_print

# Keep the same default used previously in app layer so callers can reuse it
# for samples/memory root resolution when needed by the pipeline.
default_samples_dir: str = str(Path(__file__).resolve().parents[2])

    # KISS policy (2025-09-17): all lookups/use are case-sensitive. No normalization.


def discover_prompt_repo() -> Path:
    """Locate prompt repository root.
    Priority: env PROMPT_REPO -> sibling '../prompt' -> workspace default.
    """
    env_repo = os.environ.get('PROMPT_REPO')
    if env_repo and Path(env_repo).exists():
        return Path(env_repo)
    # try sibling 'prompt' at workspace root
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / 'prompt',  # repo_root/prompt
        here.parents[1] / 'prompt',  # call/prompt (if copied inside)
        Path('c:/Users/Leader/PycharmProjects/prompt'),
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("Prompt repository not found. Set PROMPT_REPO env to its path.")


def discover_agent_repo() -> Path:
    """Locate agent repository root (projects + agents).
    Priority: env AGENT_REPO -> sibling '../agent' -> workspace default.
    """
    env_repo = os.environ.get('AGENT_REPO')
    if env_repo and Path(env_repo).exists():
        return Path(env_repo)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / 'agent',  # repo_root/agent
        here.parents[1] / 'agent',  # call/agent (if copied inside)
    ]
    for p in candidates:
        if p.exists():
            return p
    # As a last resort, if there is no dedicated agent repo, fall back to prompt repo root
    try:
        return discover_prompt_repo()
    except Exception:
        raise FileNotFoundError("Agent repository not found. Set AGENT_REPO env to its path.")


def load_yaml(path: Path) -> dict:
    """Simple YAML loader."""
    import yaml
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_projects_index(repo: Path | None = None) -> list[str]:
    """DB-only projects index using call/repo.db.

    Returns exact project names discovered by the last sync (scan).
    No filesystem reads. If the DB is empty, returns an empty list.
    """
    try:
        from call.lib import repo_db as _repo_db
        rows = _repo_db.find_projects()
        return [r.get('project') for r in rows if isinstance(r, dict) and r.get('project')]
    except Exception:
        return []


def _load_agents_index(index_path: Path, base_dir: Path) -> dict[str, Path]:
    """Load per-project agents index which may contain 'agents' and optional 'aliases'.

    Returns a mapping from agent name and all aliases (exact, case-sensitive) to full agent.yaml Path.
    """
    mapping: dict[str, Path] = {}

    def _resolve_dir_case(parent: Path, name: str) -> Path:
        """Return a child path using the on-disk directory name casing if present.

        Performs a case-insensitive match among `parent` entries and returns the
        actual directory Path so that `Path(...).parent.name` reflects real casing.
        """
        try:
            target_lower = str(name).lower()
            for entry in parent.iterdir():
                if entry.is_dir() and entry.name.lower() == target_lower:
                    return entry
        except Exception:
            pass
        return parent / name

    try:
        if not index_path.exists():
            return mapping
        data = load_yaml(index_path) or {}
        agents_map = data.get('agents') or {}
        # Optional explicit aliases mapping: { AgentName: [alias1, alias2, ...] }
        aliases_map = data.get('aliases') or {}
        if isinstance(agents_map, dict):
            for name in agents_map.keys():
                name_key = str(name)
                # resolve to on-disk directory name if present (robustness only)
                agent_dir = _resolve_dir_case(base_dir, name_key)
                path = (agent_dir / 'agent.yaml')
                if path.exists():
                    mapping[name_key] = path
                # bind aliases from agents.yaml index (exact case)
                if isinstance(aliases_map, dict):
                    for alias in (aliases_map.get(name) or aliases_map.get(name_key) or []):
                        alias_key = str(alias)
                        if alias_key and path.exists():
                            mapping[alias_key] = path
                # Enrich aliases from the agent.yaml itself if present
                try:
                    if path.exists():
                        try:
                            y = load_yaml(path) or {}
                            raw_aliases = y.get('aliases') or []
                            if isinstance(raw_aliases, list):
                                for al in raw_aliases:
                                    alias_key = str(al)
                                    if alias_key:
                                        mapping[alias_key] = path
                        except Exception:
                            pass
                except Exception:
                    pass
    except Exception:
        # Non-fatal: fallback to directory scan later
        return {}
    return mapping


def _scan_agents_dir(base_dir: Path) -> dict[str, tuple[Path, list[str]]]:
    """Scan a project directory for subfolders with agent.yaml.

    Returns mapping: AgentName -> (card_path, aliases[])
    """
    result: dict[str, tuple[Path, list[str]]] = {}
    if not base_dir.exists():
        return result
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        ay = child / 'agent.yaml'
        if ay.exists():
            try:
                y = load_yaml(ay) or {}
                name = str(y.get('id') or y.get('name') or child.name)
                aliases: list[str] = []
                raw_aliases = y.get('aliases') or []
                if isinstance(raw_aliases, list):
                    aliases = [str(a) for a in raw_aliases if str(a).strip()]
                result[name] = (ay, aliases)
            except Exception:
                result[child.name] = (ay, [])
    return result


def scan_project_agents(project_dir) -> list[dict]:
    """MD-first scan of a project directory for agent cards and their prompts.

    Rules:
    - Prefer `agent.md` (with METADATA) in project root and per-agent subdirs.
    - If `project.md` exists, include a synthetic root agent from its METADATA.
    - Minimal backward-compat: if only `agent.yaml` exists, parse it with PyYAML when available.
    Returns a list of dicts with keys: type, id, name, aliases, prompts, path.
    """
    from pathlib import Path as _Path
    import builtins as _builtins
    out: list[dict] = []
    base = _Path(project_dir)
    if not base.exists():
        return out

    def _from_md(p: _Path, *, default_name: str) -> tuple[str, list[str], list[str]]:
        meta = _read_prompt_metadata(p) or {}
        name = str(meta.get('id') or meta.get('name') or meta.get('title') or default_name)
        aliases = [str(a).strip() for a in (meta.get('aliases') or [])] if isinstance(meta.get('aliases'), list) else []
        prompts_val = meta.get('prompts') or []
        if isinstance(prompts_val, dict):
            prompts = [str(k) for k in prompts_val.keys()]
        elif isinstance(prompts_val, list):
            prompts = [str(k) for k in prompts_val]
        else:
            prompts = []
        return name, aliases, prompts

    # Root project.md → synthetic agent entry
    try:
        pmd = base / 'project.md'
        if pmd.exists():
            name, aliases, prompts = _from_md(pmd, default_name=base.name)
            out.append({"type": "agent", "id": "", "name": name, "aliases": aliases, "prompts": prompts, "path": str(pmd)})
    except Exception:
        pass

    # Root agent.md
    try:
        amd = base / 'agent.md'
        if amd.exists():
            name, aliases, prompts = _from_md(amd, default_name=base.name)
            out.append({"type": "agent", "id": "", "name": name, "aliases": aliases, "prompts": prompts, "path": str(amd)})
    except Exception:
        pass

    # Per-agent subdirs
    try:
        for child in base.iterdir():
            if not child.is_dir() or child.name.startswith('.'):
                continue
            amd = child / 'agent.md'
            if amd.exists():
                try:
                    name, aliases, prompts = _from_md(amd, default_name=child.name)
                    out.append({"type": "agent", "id": "", "name": name, "aliases": aliases, "prompts": prompts, "path": str(amd)})
                except Exception:
                    continue
            else:
                # minimal YAML compat
                ay = child / 'agent.yaml'
                if ay.exists():
                    try:
                        y = load_yaml(ay) or {}
                        name = str(y.get('id') or y.get('name') or child.name)
                        aliases = [str(a).strip() for a in (y.get('aliases') or [])] if isinstance(y.get('aliases'), list) else []
                        pv = y.get('prompts') or []
                        if isinstance(pv, dict):
                            prompts = [str(k) for k in pv.keys()]
                        elif isinstance(pv, list):
                            prompts = [str(k) for k in pv]
                        else:
                            prompts = []
                        out.append({"type": "agent", "id": "", "name": name, "aliases": aliases, "prompts": prompts, "path": str(ay)})
                    except Exception:
                        continue
    except Exception:
        pass

    return out


def _ensure_indices(rep: Path) -> None:
    """Create minimal indices agents.yaml for all known projects (from projects.yaml) and AgentFab.

    Structure:
      name: <string>
      agents: { AgentName: <short description or empty> }
      aliases: { AgentName: [Alias1, Alias2] }
    """
    import yaml

    # Collect projects from projects.yaml (if present) + legacy folders
    projects: list[Path] = []
    try:
        names = load_projects_index(rep)
    except Exception:
        names = []
    for pname in names:
        p = rep / str(pname)
        if p.exists():
            projects.append(p)
    # Always include AgentFab
    for legacy in ('AgentFab',):
        p = rep / legacy
        if p.exists() and p not in projects:
            projects.append(p)

    for base in projects:
        index = base / 'agents.yaml'
        # Policy: do not auto-generate AgentFab/agents.yaml (maintained manually)
        if base.name == 'AgentFab':
            continue
        if index.exists():
            continue
        scanned = _scan_agents_dir(base)
        agents_map = {name: '' for name in scanned.keys()}
        aliases_map = {name: aliases for name, (_, aliases) in scanned.items() if aliases}
        # Enrichment from project.yaml when directory scan finds nothing
        if not agents_map:
            proj_yaml = base / 'project.yaml'
            fallback_yaml = (rep / 'AgentFab' / 'agent.yaml') if base.name == 'AgentFab' else None
            src = proj_yaml if proj_yaml.exists() else fallback_yaml
            if src and src.exists():
                try:
                    data = load_yaml(src) or {}
                    # agents may live under project.agents or top-level agents
                    root_block = data.get('project') or {}
                    section = root_block.get('agents', data.get('agents', {}))
                    if isinstance(section, dict):
                        # section may be { name: desc } or { name: { desc, aliases, prompts } }
                        for nm, spec in section.items():
                            agents_map[str(nm)] = '' if not isinstance(spec, str) else str(spec or '')
                        # Try to enrich aliases_map from nested specs
                        for nm, spec in section.items():
                            if isinstance(spec, dict):
                                av = spec.get('aliases') or []
                                if isinstance(av, list) and av:
                                    aliases_map[str(nm)] = [str(a) for a in av if str(a).strip()]
                except Exception:
                    pass
        content = {
            'name': f'{base.name} Agents Index',
            'agents': agents_map,
        }
        if aliases_map:
            content['aliases'] = aliases_map
        try:
            base.mkdir(parents=True, exist_ok=True)
            with open(index, 'w', encoding='utf-8') as f:
                yaml.safe_dump(content, f, allow_unicode=True, sort_keys=False)
        except Exception:
            # best-effort; ignore failures
            pass


def discover_agent_yaml(agent_name: str, project: str | None = None) -> Path | None:
    """Resolve agent card path strictly via repo.db without filesystem fallbacks.

    Rules:
    - Exact, case-sensitive match on agent name (after stripping leading '@').
    - Optional project filter narrows the search.
    - No alias resolution, no special-cases, no directory scans.
    - Returns the path only when exactly one row matches; otherwise returns None.
    """
    from pathlib import Path as _Path
    if not agent_name:
        return None
    name = str(agent_name).strip().lstrip("@")
    debug_print("[discovery] discover_agent_yaml (db-only):", f"agent={name}", f"project={(project or '')}")
    try:
        from call.lib import repo_db as _repo
        rows = _repo.find_agents(project=(project or None), agent=name, target=None)
    except Exception:
        rows = []
    if not rows:
        return None
    # If more than one row matches (should only happen without project filter), do not guess.
    if len(rows) != 1 and not project:
        return None
    row = rows[0] if rows else None
    p = (row or {}).get("path") if isinstance(row, dict) else None
    return _Path(p) if p else None


def _read_prompt_metadata(path: Path) -> dict:
    """Parse METADATA (or legacy META) YAML block from a Markdown file.

    Tolerances:
    - Accept both <!-- METADATA:START --> and <!-- META:START -->; also tolerate malformed '<!-- META:START --'.
    - If a fenced YAML block (```yaml ... ```) exists inside the METADATA block, parse it.
    - Otherwise, parse the raw block text as YAML.
    """
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return {}
    try:
        import re as _re
        import yaml as _yaml
        # Normalize obvious malformed start tags by inserting closing '>'
        t = _re.sub(r"<!--\s*(META|METADATA):START\s*--\s*", "<!-- METADATA:START -->", text)
        # Build a robust regex to capture the block
        rx = _re.compile(r"<!--\s*(?:METADATA|META):START\s*-->\s*(.*?)\s*<!--\s*(?:METADATA|META):END\s*-->", _re.S | _re.I)
        m = rx.search(t)
        block = ""
        if m:
            block = m.group(1) or ""
        else:
            # Fallback: '## <!--ANCHOR:METADATA--> METADATA' style — parse the next fenced YAML
            hdr = _re.search(r"^\s*##\s*<!--\s*ANCHOR:METADATA\s*-->.*$", text, _re.M)
            if hdr:
                after = text[hdr.end():]
                m3 = _re.search(r"```yaml\s*(.*?)\s*```", after, _re.S | _re.I)
                if m3:
                    ytxt = m3.group(1)
                    data = _yaml.safe_load(ytxt) or {}
                    return data if isinstance(data, dict) else {}
            return {}
        # Prefer fenced yaml inside the block
        m2 = _re.search(r"```yaml\s*(.*?)\s*```", block, _re.S | _re.I)
        ytxt = m2.group(1) if m2 else block
        # Remove possible trailing '---' line artifacts
        ytxt = _re.sub(r"\n\s*---\s*$", "\n", ytxt.strip(), flags=_re.S)
        data = _yaml.safe_load(ytxt) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _choose_best_prompt(paths: list[Path], *, project: str | None, agent: str | None) -> Path | None:
    """Pick the best prompt among candidates using metadata and policy."""
    if not paths:
        return None
    prio = {'AgentFab': 0, 'UxFab': 1, 'FanFab': 2, 'MediaGenFab': 3}

    def key(p: Path):
        m = _read_prompt_metadata(p)
        m_proj = str(m.get('project') or '')
        m_agent = str(m.get('agent') or '')
        proj_match = (project or '').lower() == m_proj.lower() if project else False
        agent_match = (agent or '').lower().replace(' ', '') == m_agent.lower().replace(' ', '') if agent else False
        return (
            0 if (proj_match and agent_match) else 1 if proj_match else 2 if agent_match else 3,
            prio.get(m_proj, 9),
            m_agent.lower(),
            p.name.lower(),
        )

    try:
        return sorted(paths, key=key)[0]
    except Exception:
        return sorted(paths)[0]


def resolve_prompt(name: str, *, project: str | None = None, agent: str | None = None, prefer_ready: bool = True, repo: Path | None = None) -> Path | None:
    """Resolve a prompt path by querying the repo DB only. No fallbacks.

    Behavior:
    - Filter by exact prompt name (id) and optional project/agent.
    - If prefer_ready is True, restrict to state='ready'.
    - Return a Path only when exactly one row matches; else return None.
    """
    if not name:
        return None
    try:
        from call.lib import repo_db as _repo_db
        rows = _repo_db.list_prompts(project=(project or None), agent=(agent or None), prompt=str(name).strip(), state=('ready' if prefer_ready else None))
    except Exception:
        rows = []
    if len(rows) != 1:
        return None
    from pathlib import Path as _Path
    p = rows[0].get('path')
    return _Path(p) if p else None


def github_blob_url(local_path: str | Path) -> str | None:
    """Best-effort GitHub blob URL from a local path.

    Environment options:
      - GITHUB_REMOTE_URL: full remote URL (ssh or https)
      - GITHUB_REMOTE_ORGANIZATION_URL: org URL; repo derived from top-level folder
      - GITHUB_BRANCH: defaults to 'master'
    """
    try:
        import os as _os
        p = Path(local_path)
        repo_root = Path(__file__).resolve().parents[2]
        try:
            rel = str(Path(p).resolve().relative_to(repo_root.resolve()).as_posix())
        except Exception:
            rel = p.name
        branch = _os.getenv("GITHUB_BRANCH", "master")
        remote = _os.getenv("GITHUB_REMOTE_URL", "").strip()
        if remote:
            url = remote
            if url.startswith("git@github.com:"):
                url = url.replace("git@github.com:", "https://github.com/")
            if url.endswith(".git"):
                url = url[:-4]
            if url.startswith("http"):
                return f"{url}/blob/{branch}/{rel}"
        org = _os.getenv("GITHUB_REMOTE_ORGANIZATION_URL", "").strip().rstrip("/")
        if org and rel and "/" in rel:
            top, sub = rel.split("/", 1)
            if top:
                return f"{org}/{top}/blob/{branch}/{sub}"
        return None
    except Exception:
        return None


def _read_yaml_prompt_metadata(path: Path) -> dict:
    """Read minimal prompt metadata from a YAML prompt file."""
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        if not isinstance(data, dict):
            return {}
        # normalize keys
        out = {
            'version': data.get('version'),
            'id': data.get('id'),
            'title': data.get('title') or data.get('name'),
            'project': data.get('project'),
            'agent': data.get('agent'),
            'tags': data.get('tags') if isinstance(data.get('tags'), list) else [],
        }
        return {k: v for k, v in out.items() if v is not None}
    except Exception:
        return {}


def iter_prompts(*, repo: Path | None = None, state: str | None = None):
    """Yield prompt descriptors from the repo DB only (no filesystem reads).

    Each item: { prompt_id, name, agent, project, state, url, path }
    - name equals prompt_id (DB does not store title)
    - url is derived from path using github_blob_url best-effort (may be None)
    """
    try:
        from call.lib import repo_db as _repo
        rows = _repo.list_prompts(state=(state or None))
    except Exception:
        rows = []
    for r in rows or []:
        p = r.get('path') or ''
        url = github_blob_url(p) if p else None
        yield {
            'prompt_id': r.get('prompt') or '',
            'name': r.get('prompt') or '',
            'agent': r.get('agent') or None,
            'project': r.get('project') or None,
            'state': r.get('state') or '',
            'url': url,
            'path': p,
        }


def prompts(*, project: str | None = None, agent: str | None = None, prompt: str | None = None, state: str | None = None, repo: Path | None = None) -> list[dict]:
    """Return prompt descriptors strictly from the repo DB (no filesystem reads)."""
    try:
        from call.lib import repo_db as _repo
        rows = _repo.list_prompts(project=(project or None), agent=(agent or None), prompt=(prompt or None), state=(state or None))
    except Exception:
        rows = []
    out: list[dict] = []
    for r in rows or []:
        p = r.get('path') or ''
        out.append({
            'prompt_id': r.get('prompt') or '',
            'name': r.get('prompt') or '',
            'agent': r.get('agent') or None,
            'project': r.get('project') or None,
            'state': r.get('state') or '',
            'url': github_blob_url(p) if p else None,
            'path': p,
        })
    return out
