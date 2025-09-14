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


def _load_agents_index(index_path: Path, base_dir: Path) -> dict[str, Path]:
    """Load agents index file which may contain 'agents' and optional 'aliases'.

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
    """Scan a directory for subfolders with agent.yaml.

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


def _ensure_indices(rep: Path) -> None:
    """Create minimal indices agents.yaml for all known projects (from projects.yaml) and legacy 'agents/'.

    Structure:
      name: <string>
      agents: { AgentName: <short description or empty> }
      aliases: { AgentName: [Alias1, Alias2] }
    """
    import yaml

    # Collect projects from projects.yaml (if present) + legacy folders
    projects: list[Path] = []
    # From projects.yaml
    try:
        proj_idx = rep / 'projects.yaml'
        if proj_idx.exists():
            data = load_yaml(proj_idx) or {}
            for pname in (data.get('projects') or {}).keys():
                p = rep / str(pname)
                if p.exists():
                    projects.append(p)
    except Exception:
        pass
    # Always include legacy folders
    for legacy in ('AgentFab', 'agents'):
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
        # Special enrichment from AgentFab root agent.yaml if base is AgentFab and no items were found
        if not agents_map and base.name == 'AgentFab':
            af = rep / 'AgentFab' / 'agent.yaml'
            if af.exists():
                try:
                    data = load_yaml(af) or {}
                    section = data.get('agents') or {}
                    if isinstance(section, dict):
                        for group_val in section.values():
                            if isinstance(group_val, dict):
                                for nm, desc in group_val.items():
                                    agents_map[to_pascal_case(str(nm))] = str(desc or '')
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


def discover_agent_yaml(agent_name: str) -> Path | None:
    """Discover agent YAML with index-first strategy and fallbacks.

    Priority:
    0) Special-case AgentFab -> prompt/AgentFab/agent.yaml
    1) Index lookup in AgentFab/agents.yaml (by name or alias)
    2) Index lookup in agents/agents.yaml (by name or alias)
    3) Directory scan in AgentFab/<AgentName>/agent.yaml
    4) Directory scan in agents/<AgentName>/agent.yaml
    """
    if not agent_name:
        return None
    repo = discover_prompt_repo()
    query_raw = str(agent_name).strip().lstrip('@')
    query_norm = to_pascal_case(query_raw)

    # 0) Special-case: AgentFab root card and its aliases listed in AgentFab/agent.yaml
    root_yaml = repo / 'AgentFab' / 'agent.yaml'
    if root_yaml.exists():
        if query_norm.lower() == 'agentfab':
            return root_yaml
        # Consider aliases from root card, e.g., "Agent Fab", "Factory", and custom entries like "AgentFabBot"
        try:
            data = load_yaml(root_yaml) or {}
            root_aliases = data.get('aliases') or []
            # Normalize and compare
            for al in root_aliases:
                if to_pascal_case(str(al)) == query_norm:
                    return root_yaml
            # Also accept a few common derived handles
            if query_norm in {to_pascal_case('Agent Fab'), to_pascal_case('Factory'), to_pascal_case('AgentFabBot')}:
                return root_yaml
        except Exception:
            pass

    # Ensure indices exist (best-effort) for all known projects
    _ensure_indices(repo)

    # 1) Index lookup across all project indices (projects.yaml + legacy)
    index_candidates: list[tuple[Path, Path]] = []
    # from projects.yaml
    try:
        proj_idx = repo / 'projects.yaml'
        if proj_idx.exists():
            data = load_yaml(proj_idx) or {}
            for pname in (data.get('projects') or {}).keys():
                base = repo / str(pname)
                index_candidates.append((base / 'agents.yaml', base))
    except Exception:
        pass
    # legacy indices
    for legacy in ('AgentFab', 'agents'):
        base = repo / legacy
        index_candidates.append((base / 'agents.yaml', base))

    for idx_path, base in index_candidates:
        m = _load_agents_index(idx_path, base)
        if query_norm in m:
            return m[query_norm]

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
    # legacy fallback order
    search_bases += [repo / 'AgentFab', repo / 'agents']

    for base in search_bases:
        p = find_in_dir(base)
        if p:
            return p
    return None
