"""
Shared discovery and YAML helper utilities for the call subsystem.

This module centralizes functions that were previously implemented in
`call/app/call.py` to avoid circular imports and duplication across the
library API, CLI, and Telegram bot layers.

Provided helpers:
- to_pascal_case(name: str) -> str
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

# Keep the same default used previously in app layer so callers can reuse it
# for samples/memory root resolution when needed by the pipeline.
default_samples_dir: str = str(Path(__file__).resolve().parents[2])


def _debug_print(*parts: str) -> None:
    """Lightweight debug print gated by CALL_DEBUG to avoid importing app layer."""
    try:
        flag = str(os.environ.get("CALL_DEBUG", "")).strip().lower()
        if flag in ("1", "true", "yes", "on"):
            try:
                msg = " ".join(str(p) for p in parts if p is not None)
                print(f"[DEBUG][discovery] {msg}")
            except Exception:
                pass
    except Exception:
        pass


def to_pascal_case(name: str) -> str:
    """Normalize agent name to PascalCase as per process-agents.md (case-insensitive input)."""
    if not name:
        return ""
    # strip leading '@' and split by non-alnum and separators like ':' and '/'
    raw = name.strip().lstrip('@')
    # only take the AgentName part before ':' if provided
    raw = raw.split(':', 1)[0]
    parts: List[str] = []
    token = ''
    for ch in raw:
        if ch.isalnum():
            token += ch
        else:
            if token:
                parts.append(token)
                token = ''
    if token:
        parts.append(token)
    # Preserve existing internal capitalization within tokens.
    # Only uppercase the first character of each token; do not lowercase the remainder.
    def _cap_preserve(t: str) -> str:
        if not t:
            return ''
        return t[:1].upper() + t[1:]
    return ''.join(_cap_preserve(p) for p in parts)


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


def load_yaml(path: Path) -> dict:
    """Simple YAML loader."""
    import yaml
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_projects_index(repo: Path | None = None) -> list[str]:
    """Return list of project names from prompt/projects.yaml (exact, case-sensitive).

    Strict policy:
    - If projects.yaml exists: require a non-empty top-level 'projects' mapping; otherwise raise ValueError with guidance.
    - If projects.yaml is missing: fall back to scanning repo root for plausible project directories.
    """
    if repo is None:
        repo = discover_prompt_repo()
    index = repo / 'projects.yaml'
    if index.exists():
        try:
            data = load_yaml(index) or {}
        except Exception as e:
            raise ValueError(f"Failed to parse {index}: {e}")
        pr = data.get('projects')
        if not isinstance(pr, dict) or not pr:
            keys = ", ".join(sorted(list(data.keys()))) if isinstance(data, dict) else "<non-dict>"
            example = "projects:\n  UxFab: { description: ... }\n  FanFab: { description: ... }\n"
            raise ValueError(
                f"Malformed {index}: expected a non-empty top-level 'projects' mapping. Found keys: [{keys}].\n"
                f"Please correct the schema to include a 'projects' mapping, e.g.:\n{example}"
            )
        return list(pr.keys())
    # Fallback: scan repo root
    names: list[str] = []
    try:
        for child in repo.iterdir():
            if not child.is_dir() or child.name.startswith('.'):
                continue
            if (child / 'project.yaml').exists():
                names.append(child.name)
                continue
            try:
                has_agent = any((p.is_dir() and (p / 'agent.yaml').exists()) for p in child.iterdir())
            except Exception:
                has_agent = False
            if has_agent:
                names.append(child.name)
    except Exception:
        pass
    return names


def _load_agents_index(index_path: Path, base_dir: Path) -> dict[str, Path]:
    """Load per-project agents index which may contain 'agents' and optional 'aliases'.

    Returns a mapping from agent name and all aliases (PascalCase) to full agent.yaml Path.
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
                name_pc = to_pascal_case(str(name))
                # resolve to actual directory casing if present
                agent_dir = _resolve_dir_case(base_dir, name_pc)
                path = (agent_dir / 'agent.yaml')
                if path.exists():
                    mapping[name_pc] = path
                # bind aliases
                if isinstance(aliases_map, dict):
                    for alias in (aliases_map.get(name) or aliases_map.get(name_pc) or []):
                        alias_pc = to_pascal_case(str(alias))
                        if alias_pc and path.exists():
                            mapping[alias_pc] = path
    except Exception:
        # Non-fatal: fallback to directory scan later
        return {}
    return mapping


def _scan_agents_dir(base_dir: Path) -> dict[str, tuple[Path, list[str]]]:
    """Scan a project directory for subfolders with agent.yaml.

    Returns mapping: AgentName -> (agent_yaml_path, aliases[])
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
                name = to_pascal_case(str(y.get('id') or y.get('name') or child.name))
                aliases: list[str] = []
                raw_aliases = y.get('aliases') or []
                if isinstance(raw_aliases, list):
                    aliases = [to_pascal_case(str(a)) for a in raw_aliases if str(a).strip()]
                result[name] = (ay, aliases)
            except Exception:
                result[child.name] = (ay, [])
    return result


def scan_project_agents(project_dir) -> list[dict]:
    """Scan a project directory for agents and extract aliases and prompts from agent.yaml.

    Behavior mirrors the previous api._scan_project_agents to avoid regressions:
    - Prefer unified project.yaml when present (include root project agent entry + per-agent entries from 'agents' section).
    - Fall back to legacy layout: include root agent.yaml if present, plus all subdirs containing agent.yaml.
    Returns a list of dicts with keys: type, id, name, aliases, prompts, path.
    """
    from pathlib import Path as _Path
    import builtins as _builtins
    out: list[dict] = []
    if not _Path(project_dir).exists():
        return out
    # 0) Prefer new unified project.yaml schema when present
    try:
        proj_yaml = _Path(project_dir) / 'project.yaml'
        if proj_yaml.exists():
            try:
                y = load_yaml(proj_yaml) or {}
            except Exception:
                y = {}
            # Root project agent
            root_block = {}
            if isinstance(y.get('project'), dict):
                root_block = y.get('project') or {}
            name = str(root_block.get('name') or y.get('name') or _Path(project_dir).name)
            # aliases may be at top-level, under project, or under root
            aliases_val = root_block.get('aliases', y.get('aliases', []))
            aliases = [str(a).strip() for a in (aliases_val or [])] if isinstance(aliases_val, _builtins.list) else []
            # prompts may be mapping or list; accept both
            prompts_val = root_block.get('prompts', y.get('prompts', {}))
            if isinstance(prompts_val, dict):
                prompts_list = [str(k) for k in prompts_val.keys()]
            elif isinstance(prompts_val, _builtins.list):
                prompts_list = [str(k) for k in prompts_val]
            else:
                prompts_list = []
            out.append({
                "type": "agent",
                "id": "",
                "name": name,
                "aliases": aliases,
                "prompts": prompts_list,
                "path": str(proj_yaml),
            })
            # Agents section: dict of name -> (desc | {aliases, prompts, desc})
            agents_section = root_block.get('agents', y.get('agents', {}))
            if isinstance(agents_section, dict):
                for nm, spec in agents_section.items():
                    ag_name = str(nm)
                    ag_aliases: list[str] = []
                    ag_prompts: list[str] = []
                    if isinstance(spec, dict):
                        av = spec.get('aliases', [])
                        if isinstance(av, _builtins.list):
                            ag_aliases = [str(a).strip() for a in av if str(a).strip()]
                        pv = spec.get('prompts', {})
                        if isinstance(pv, dict):
                            ag_prompts = [str(k) for k in pv.keys()]
                        elif isinstance(pv, _builtins.list):
                            ag_prompts = [str(k) for k in pv]
                    # Resolve path: prefer subdir/agent.yaml when present; else project.yaml as definition source
                    ay = _Path(project_dir) / ag_name / 'agent.yaml'
                    path_str = str(ay) if ay.exists() else str(proj_yaml)
                    out.append({
                        "type": "agent",
                        "id": "",
                        "name": ag_name,
                        "aliases": ag_aliases,
                        "prompts": ag_prompts,
                        "path": path_str,
                    })
            return out
    except Exception:
        # best-effort; fall back to legacy layout
        pass
    # 1) Legacy: include root project agent if present (e.g., AgentFab/agent.yaml)
    try:
        root_ay = _Path(project_dir) / 'agent.yaml'
        if root_ay.exists():
            try:
                y = load_yaml(root_ay) or {}
            except Exception:
                y = {}
            name = _Path(project_dir).name
            try:
                id_or_name = y.get('id') or y.get('name')
                if isinstance(id_or_name, str) and id_or_name.strip():
                    name = id_or_name.strip()
            except Exception:
                pass
            aliases: list[str] = []
            raw_aliases = y.get('aliases') or []
            if isinstance(raw_aliases, _builtins.list):
                aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]
            prompts_list: list[str] = []
            raw_prompts = y.get('prompts') or {}
            if isinstance(raw_prompts, dict):
                prompts_list = [str(k) for k in raw_prompts.keys()]
            out.append({
                "type": "agent",
                "id": "",
                "name": name,
                "aliases": aliases,
                "prompts": prompts_list,
                "path": str(root_ay),
            })
    except Exception:
        # best-effort only
        pass
    for child in _Path(project_dir).iterdir():
        if not child.is_dir():
            continue
        ay = child / 'agent.yaml'
        if not ay.exists():
            continue
        try:
            y = load_yaml(ay) or {}
        except Exception:
            y = {}
        name = child.name
        try:
            id_or_name = y.get('id') or y.get('name')
            if isinstance(id_or_name, str) and id_or_name.strip():
                name = id_or_name.strip()
        except Exception:
            pass
        aliases: list[str] = []
        raw_aliases = y.get('aliases') or []
        if isinstance(raw_aliases, _builtins.list):
            aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]
        prompts_list: list[str] = []
        raw_prompts = y.get('prompts') or {}
        if isinstance(raw_prompts, dict):
            prompts_list = [str(k) for k in raw_prompts.keys()]
        out.append({
            "type": "agent",
            "id": "",
            "name": name,
            "aliases": aliases,
            "prompts": prompts_list,
            "path": str(ay),
        })
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
                            agents_map[to_pascal_case(str(nm))] = '' if not isinstance(spec, str) else str(spec or '')
                        # Try to enrich aliases_map from nested specs
                        for nm, spec in section.items():
                            if isinstance(spec, dict):
                                av = spec.get('aliases') or []
                                if isinstance(av, list) and av:
                                    aliases_map[to_pascal_case(str(nm))] = [to_pascal_case(str(a)) for a in av if str(a).strip()]
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
    """Discover agent YAML with index-first strategy and fallbacks.

    Priority:
    0) Special-case AgentFab -> prompt/AgentFab/agent.yaml
    1) Index lookup in per-project agents.yaml and AgentFab/agents.yaml (by name or alias)
    2) Directory scan in AgentFab/<AgentName>/agent.yaml
    3) Directory scan in <Project>/<AgentName>/agent.yaml across known projects
    """
    if not agent_name:
        return None
    repo = discover_prompt_repo()
    query_raw = str(agent_name).strip().lstrip('@')
    query_norm = to_pascal_case(query_raw)
    _debug_print("discover_agent_yaml:", f"agent={query_norm}", f"project={(project or '')}")

    # 0) Special-case: AgentFab root card; support agent.yaml or project.yaml (new schema)
    for root_file in [repo / 'AgentFab' / 'agent.yaml', repo / 'AgentFab' / 'project.yaml']:
        if root_file.exists():
            if query_norm.lower() == 'agentfab':
                return root_file
            # Consider aliases from root card under either top-level or 'project' block
            try:
                data = load_yaml(root_file) or {}
                root_block = data.get('project') or {}
                aliases = root_block.get('aliases') or data.get('aliases') or []
                for al in (aliases or []):
                    if to_pascal_case(str(al)) == query_norm:
                        return root_file
                # Also accept a few common derived handles
                if query_norm in {to_pascal_case('Agent Fab'), to_pascal_case('Factory'), to_pascal_case('AgentFabBot')}:
                    return root_file
            except Exception:
                pass

    # Ensure indices exist (best-effort) for all known projects
    _ensure_indices(repo)

    # 1) Index lookup across all project indices (projects.yaml + AgentFab)
    index_candidates: list[tuple[Path, Path]] = []
    try:
        names = load_projects_index(repo)
    except Exception:
        names = []
    for pname in names:
        base = repo / str(pname)
        index_candidates.append((base / 'agents.yaml', base))
    # AgentFab index (creator area)
    for legacy in ('AgentFab',):
        base = repo / legacy
        index_candidates.append((base / 'agents.yaml', base))

    for idx_path, base in index_candidates:
        try:
            if project and base.name != project:
                continue
            m = _load_agents_index(idx_path, base)
            if query_norm in m:
                _debug_print("index hit:", f"base={base.name}", f"path={m[query_norm]}")
                return m[query_norm]
        except Exception:
            pass

    # 2) Fallback directory scan with case-insensitive match across all projects
    def find_in_dir(base: Path) -> Path | None:
        if not base.exists():
            return None
        # Try exact
        direct = base / query_norm / 'agent.yaml'
        if direct.exists():
            return direct
        # Case-insensitive directory match
        for child in base.iterdir():
            if child.is_dir() and child.name.lower() == query_norm.lower():
                cand = child / 'agent.yaml'
                if cand.exists():
                    return cand
        return None

    # Search through all project directories
    search_bases: list[Path] = []
    try:
        proj_idx = repo / 'projects.yaml'
        if proj_idx.exists():
            data = load_yaml(proj_idx) or {}
            for pname in (data.get('projects') or {}).keys():
                search_bases.append(repo / str(pname))
    except Exception:
        pass
    # include AgentFab as a special creator base
    search_bases += [repo / 'AgentFab']

    # Broad fallback: include all top-level directories under repo as potential projects
    # This allows discovery in folders like 'FanFab', 'MediaGenFab', 'UxFab', etc.
    try:
        for child in repo.iterdir():
            if child.is_dir() and child not in search_bases:
                search_bases.append(child)
    except Exception:
        pass

    # If project is set, restrict the search to that single project directory only
    if project:
        proj_dir = repo / project
        search_bases = [proj_dir]
        _debug_print("project restricted search:", f"base={proj_dir}")

    for base in search_bases:
        try:
            p = find_in_dir(base)
            if p:
                _debug_print("fallback hit:", f"base={base.name}", f"path={p}")
                return p
        except Exception:
            pass
    return None
