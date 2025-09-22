#!/usr/bin/env python3
"""
Migrate legacy model params keys in prompt/ Markdown cards to canonical hyphenated forms.

- From legacy (to be removed):
  - model_params, modelParams (generic)
  - model_params_<model>, modelParams<model> (model-suffixed)
- To canonical:
  - model-params (generic)
  - model-params-<model> (model-suffixed)

Features:
- Dry-run by default: prints a report of proposed changes.
- --write to apply in-place edits (safe overwrite).
- --project <Name> to restrict to a specific project subtree (optional).

Usage:
  python -m call.tools.migrate_model_params --dry-run
  python -m call.tools.migrate_model_params --write

Notes:
- Only edits the METADATA fenced YAML inside <!-- METADATA:START --> ```yaml ... ``` blocks.
- Preserves ordering and other keys. Only renames keys; values are unchanged.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, Tuple, List

ROOT = Path(__file__).resolve().parents[2]  # repo root
PROMPT_DIR = ROOT / "prompt"

META_START = "<!-- METADATA:START -->"
META_FENCE = "```yaml"
FENCE_CLOSE = "```"

LEGACY_GENERIC = ("model_params", "modelParams")


def _extract_metadata_range(text: str) -> Tuple[int, int] | None:
    try:
        s = text.index(META_START)
        y0 = text.index(META_FENCE, s) + len(META_FENCE)
        y1 = text.index(FENCE_CLOSE, y0)
        return y0, y1
    except ValueError:
        return None


def _rename_keys_in_yaml(yaml_text: str) -> Tuple[str, List[str]]:
    """
    Rename top-level keys only:
      - model_params -> model-params
      - modelParams -> model-params
      - model_params_<model> -> model-params-<model>
      - modelParams<model> -> model-params-<model>
    """
    lines = yaml_text.splitlines(keepends=False)
    changed: List[str] = []
    out: List[str] = []

    def repl_key(line: str) -> str:
        # Match a top-level key: 'key:' with optional spaces
        m = re.match(r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_\-\.]+)\s*:\s*(.*)$", line)
        if not m:
            return line
        key = m.group("key")
        indent = m.group("indent")
        # Generic legacy -> canonical
        if key in LEGACY_GENERIC:
            changed.append(f"{key} -> model-params")
            return indent + "model-params:" + line[m.end(0)-len(": ") :]
        # model_params_<model>
        if key.startswith("model_params_"):
            suffix = key[len("model_params_") :]
            if suffix:
                changed.append(f"{key} -> model-params-{suffix}")
                return f"{indent}model-params-{suffix}:" + line[m.end(0)-len(": ") :]
        # modelParams<model>
        if key.startswith("modelParams") and len(key) > len("modelParams"):
            suffix = key[len("modelParams") :]
            changed.append(f"{key} -> model-params-{suffix}")
            return f"{indent}model-params-{suffix}:" + line[m.end(0)-len(": ") :]
        return line

    for ln in lines:
        out.append(repl_key(ln))
    return "\n".join(out), changed


def process_file(path: Path, write: bool = False) -> Tuple[bool, List[str]]:
    text = path.read_text(encoding="utf-8")
    meta_rng = _extract_metadata_range(text)
    if not meta_rng:
        return False, []
    y0, y1 = meta_rng
    yaml_text = text[y0:y1]
    new_yaml, changes = _rename_keys_in_yaml(yaml_text)
    if not changes:
        return False, []
    if write:
        updated = text[:y0] + new_yaml + text[y1:]
        path.write_text(updated, encoding="utf-8")
    return True, changes


essential_exts = {".md", ".markdown"}


def main():
    ap = argparse.ArgumentParser(description="Migrate legacy model params keys to canonical hyphenated form")
    ap.add_argument("--write", action="store_true", help="Apply changes in-place (default is dry-run)")
    ap.add_argument("--project", default="", help="Restrict to prompt/<Project>/ subdir")
    args = ap.parse_args()

    base = PROMPT_DIR
    if args.project:
        base = base / args.project
    if not base.exists():
        print(f"No such directory: {base}")
        return 2

    total_examined = 0
    total_changed = 0
    report: Dict[str, List[str]] = {}

    for p in base.rglob("*.md"):
        try:
            total_examined += 1
            changed, notes = process_file(p, write=args.write)
            if changed:
                total_changed += 1
                report[str(p.relative_to(ROOT))] = notes
        except Exception as e:
            print(f"Error processing {p}: {e}")

    mode = "WRITE" if args.write else "DRY-RUN"
    print(f"[{mode}] examined={total_examined} changed_files={total_changed}")
    if report:
        print("\nChanges:")
        for rel, notes in sorted(report.items()):
            print(f"- {rel}")
            for n in notes:
                print(f"  * {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
