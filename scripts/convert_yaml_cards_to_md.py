#!/usr/bin/env python3
"""
Convert agent/project YAML cards to Markdown with standardized METADATA/PROMPT sections.

- Scans given root directories recursively for:
  - project.yaml -> project.md (next to it)
  - agent.yaml   -> agent.md (next to it)
- Copies most YAML keys into METADATA fenced YAML
- Moves textual instruction fields (instructions|goal|description) into PROMPT block (when present)
- Optionally deletes the source YAML files after successful conversion

Usage:
  python -m call.scripts.convert_yaml_cards_to_md --fix --delete-yaml --roots <dir1> <dir2> [...]

Notes:
- Idempotent: running again will skip if target .md already exists (unless --force)
- Safe defaults: without --fix it's a dry run
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, Any, List, Tuple

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


def _read_yaml(p: Path) -> Dict[str, Any]:
    try:
        if yaml is None:
            return {}
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _split_yaml_for_md(data: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    """Return (metadata, body_text) for MD output.

    Heuristics:
    - Keep all top-level keys except textual instruction keys in metadata
    - Recognize common instruction keys: instructions, goal, description
    - Preserve unknown keys in metadata for forward-compatibility
    """
    meta = dict(data)
    body = ""
    for k in ("instructions", "goal", "description"):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            body = v.strip()
            # remove from metadata copy
            if k in meta:
                meta.pop(k, None)
            break
    # Normalize prompts in metadata to a simple list of strings
    pv = meta.get("prompts")
    if isinstance(pv, dict):
        meta["prompts"] = [str(x) for x in pv.keys()]
    elif isinstance(pv, list):
        meta["prompts"] = [str(x) for x in pv]
    return meta, body


def _render_md(meta: Dict[str, Any], body: str) -> str:
    try:
        meta_yaml = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) if yaml else ""
    except Exception:
        meta_yaml = ""
    # Ensure trailing newline in fenced blocks
    meta_yaml = meta_yaml.rstrip() + "\n"
    parts: List[str] = []
    parts.append("<!-- METADATA:START -->\n```yaml\n" + meta_yaml + "```\n<!-- METADATA:END -->\n")
    # Only include PROMPT block if non-empty
    if body and body.strip():
        parts.append("\n<!-- PROMPT:START -->\n" + body.strip() + "\n<!-- PROMPT:END -->\n")
    return "".join(parts)


def convert_file(yaml_path: Path, *, fix: bool, force: bool, delete_yaml: bool) -> Tuple[bool, str, Path | None]:
    kind = None
    if yaml_path.name.lower() == "project.yaml":
        kind = "project"
        md_target = yaml_path.with_name("project.md")
    elif yaml_path.name.lower() == "agent.yaml":
        kind = "agent"
        md_target = yaml_path.with_name("agent.md")
    else:
        return False, "skip_non_card", None

    if md_target.exists() and not force:
        return False, "skip_exists", md_target

    data = _read_yaml(yaml_path)
    meta, body = _split_yaml_for_md(data)

    # Minimal metadata normalization
    if "id" not in meta and "name" in meta:
        meta["id"] = str(meta.get("name"))
    if "title" not in meta and "name" in meta:
        meta["title"] = str(meta.get("name"))

    md_text = _render_md(meta, body)

    if not fix:
        return True, "dry_run", md_target

    md_target.write_text(md_text, encoding="utf-8", newline="\n")
    if delete_yaml:
        try:
            yaml_path.unlink()
        except Exception:
            pass
    return True, "converted", md_target


def walk_and_convert(root: Path, *, fix: bool, force: bool, delete_yaml: bool) -> List[Tuple[Path, str]]:
    out: List[Tuple[Path, str]] = []
    if not root.exists():
        return out
    for p in root.rglob("*.yaml"):
        # Only project.yaml / agent.yaml
        if p.name.lower() not in ("project.yaml", "agent.yaml"):
            continue
        changed, msg, target = convert_file(p, fix=fix, force=force, delete_yaml=delete_yaml)
        if changed:
            out.append((target or p, msg))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="Write changes to disk (default is dry-run)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing .md files if present")
    ap.add_argument("--delete-yaml", action="store_true", help="Delete the source YAML files after successful conversion")
    ap.add_argument("--roots", nargs="*", default=[], help="Directories to scan (agent/prompt roots)")
    args = ap.parse_args()

    roots = [Path(p) for p in (args.roots or [])]
    if not roots:
        # sensible defaults
        roots = [
            Path("c:/home/strato-space/agent"),
            Path("c:/home/strato-space/prompt/AgentFab"),
        ]
    total: List[Tuple[Path, str]] = []
    for r in roots:
        total += walk_and_convert(r, fix=args.fix, force=args.force, delete_yaml=args.delete_yaml)

    print(f"Converted/updated: {len(total)} files")
    for t, msg in total:
        print(f"[{msg}] {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
