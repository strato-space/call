#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rebuild role and goal metadata for Markdown prompt files.

Rules:
- role: take from the first line that starts with "Ты — " or "Ты - " in PROMPT body
  (capture the text after the dash/em-dash)
- goal: take all text after the first occurrence of "Твоя цель:" in PROMPT body
  until the next section header (##) or end of PROMPT block.

Edits:
- Remove existing 'goal' in METADATA and set new 'goal' from PROMPT
- Set 'role' from PROMPT
- Preserve other metadata keys; dump YAML with unicode, no key sorting

Usage:
  python call/scripts/rebuild_role_goal_from_prompt.py --root "agent/prompt_flow_engine/prompt_repository"

Defaults to the repository path above when --root is omitted.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Tuple, Dict, Optional

import yaml

META_START = "<!-- METADATA:START -->"
PROMPT_START = "<!-- PROMPT:START -->"
PROMPT_END = "<!-- PROMPT:END -->"


def extract_metadata_region(text: str) -> Tuple[Optional[Tuple[int, int]], Dict]:
    """Return ((y_start, y_end), meta_dict). y_start/y_end are slice indices of YAML payload only."""
    try:
        y0 = text.index(META_START)
        y1 = text.index("```yaml", y0) + len("```yaml")
        y2 = text.index("```", y1)
        raw = text[y1:y2]
        meta = yaml.safe_load(raw) or {}
        if not isinstance(meta, dict):
            meta = {}
        return (y1, y2), meta
    except Exception:
        return None, {}


def extract_prompt_body(text: str) -> str:
    try:
        p0 = text.index(PROMPT_START) + len(PROMPT_START)
        p1 = text.index(PROMPT_END, p0)
        body = text[p0:p1].strip()
        # Strip leading/trailing fenced code blocks of any backtick-length
        body = re.sub(r"^`{3,}\w*\n", "", body)  # leading fence with optional lang
        body = re.sub(r"\n`{3,}\s*$", "", body)  # trailing fence
        return body.strip()
    except Exception:
        return ""


def extract_role(prompt_text: str) -> Optional[str]:
    # Match first line starting with Ты — or Ты - ; accept whitespace around dash
    for line in prompt_text.splitlines():
        s = line.strip()
        # Russian: "Ты — ..." or "Ты - ..."
        m = re.match(r"^\s*Ты\s*[—-]\s*(.+)$", s)
        if not m:
            # English: "You are ..." (case-insensitive)
            m = re.match(r"^\s*You\s+are\s+(.+)$", s, flags=re.IGNORECASE)
        if m:
            role = m.group(1).strip()
            # Drop trailing punctuation
            role = role.rstrip(".。！!?")
            return role
    return None


def extract_goal(prompt_text: str) -> Optional[str]:
    # Find 'Твоя цель:' or 'Your goal:' (case-insensitive). Capture until next major section or end
    m = re.search(r"(Твоя\s+цель|Your\s+goal)\s*:\s*", prompt_text, flags=re.IGNORECASE)
    if not m:
        return None
    start = m.end()
    tail = prompt_text[start:].strip()
    # Cut at the first blank line, or other common section markers
    cut = re.search(
        r"\n\s*\n"  # first blank line terminates goal
        r"|\n\s*##\s+"  # Markdown header
        r"|\n\s*Твоя\s+задача\s*:\s*"  # Alternate Russian section
        r"|\n\s*Задачи\s*:\s*"  # Generic tasks section
        r"|\n\s*Инструкция\s*:?"  # Russian 'Instruction:' marker
        r"|\n\s*Instructions?\s*:?"  # English 'Instruction(s):'
        r"|\n\s*Важные\s+требования[^\n]*:"  # 'Important requirements'
        r"|\n\s*Формат\s+вывода[^\n]*"  # 'Output format'
        r"|\n\s*\[Формат\s+вывода\]"  # bracketed label
        r"|\n\s*```"  # code fence
        , tail, flags=re.IGNORECASE)
    if cut:
        tail = tail[: cut.start()].rstrip()
    return tail if tail else None


def update_metadata_block(text: str, meta_slice: Tuple[int, int], new_meta: Dict) -> str:
    y1, y2 = meta_slice
    dumped = yaml.safe_dump(new_meta, allow_unicode=True, sort_keys=False)
    # Ensure a newline right after the ```yaml fence so content doesn't concatenate
    needs_nl = True
    try:
        ch = text[y1:y1+1]
        if ch in ("\n", "\r"):
            needs_nl = False
    except Exception:
        needs_nl = True
    prefix = "\n" if needs_nl else ""
    return text[:y1] + prefix + dumped + text[y2:]


def process_file(path: Path, write: bool = True) -> Tuple[bool, str]:
    src = path.read_text(encoding="utf-8")
    # Parse regions
    meta_region, meta = extract_metadata_region(src)
    prompt = extract_prompt_body(src)
    if not meta_region or not prompt:
        return False, "skip:no_meta_or_prompt"

    role = extract_role(prompt)
    goal = extract_goal(prompt)
    if not role and not goal:
        return False, "skip:no_role_goal_found"

    changed = False
    meta_out = dict(meta) if isinstance(meta, dict) else {}
    # Remove existing goal
    if "goal" in meta_out:
        del meta_out["goal"]
        changed = True
    # Set new role/goal if found
    if role:
        if meta_out.get("role") != role:
            meta_out["role"] = role
            changed = True
    if goal:
        if meta_out.get("goal") != goal:
            meta_out["goal"] = goal
            changed = True

    if not changed:
        return False, "ok:no_change"

    dst = update_metadata_block(src, meta_region, meta_out)
    if write:
        path.write_text(dst, encoding="utf-8")
    return True, "ok:updated"


def main():
    parser = argparse.ArgumentParser(description="Rebuild role/goal from PROMPT body and write to METADATA")
    parser.add_argument("--root", default=str(Path("agent") / "prompt_flow_engine" / "prompt_repository"), help="Root directory to scan recursively for .md files")
    parser.add_argument("--dry", action="store_true", help="Dry run: do not write files, only print changes")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        print(f"Root not found: {root}")
        return 2

    total = 0
    touched = 0
    skipped = 0
    for p in root.rglob("*.md"):
        total += 1
        try:
            ok, msg = process_file(p, write=(not args.dry))
            if ok:
                touched += 1
                print(f"UPDATED {p}")
            else:
                skipped += 1
                # print(f"SKIP {p}: {msg}")
        except Exception as e:
            skipped += 1
            print(f"ERROR {p}: {e}")
    print(f"Done. total={total} updated={touched} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
