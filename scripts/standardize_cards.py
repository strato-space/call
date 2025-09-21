#!/usr/bin/env python3
"""
Standardize card files (prompt .md, agent/project .yaml) across the repo:
- Convert META:START/END to METADATA:START/END in Markdown prompt files.
- Ensure a YAML fenced block (```yaml ... ```) inside METADATA block when missing.
- Remove a stray '---' line at the end of META blocks if present.
- Strip trailing empty lines and ensure exactly one trailing newline at EOF.
- Optionally print last git change for each modified file.

Usage:
  python -m call.scripts.standardize_cards --fix [--roots c:/path1 c:/path2] [--blame]
Defaults:
  roots = [c:/home/strato-space/prompt, c:/home/strato-space/agent]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from pathlib import Path
from typing import List, Tuple

DEFAULT_ROOTS = [
    Path("c:/home/strato-space/prompt"),
    Path("c:/home/strato-space/agent"),
]

META_BLOCK_RE = re.compile(r"<!--\s*(METADATA|META):START\s*-->(.*?)<!--\s*(METADATA|META):END\s*-->", re.S)


def _strip_trailing_blank_lines(text: str) -> Tuple[str, bool]:
    if text is None:
        return "", False
    # Normalize newlines
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = s.split("\n")
    changed = False
    # Remove trailing blank lines
    while lines and (lines[-1].strip() == ""):
        lines.pop()
        changed = True
    # Ensure exactly one final newline (POSIX style) if content is non-empty
    if lines:
        s2 = "\n".join(lines) + "\n"
    else:
        s2 = ""
    if s2 != text:
        changed = True
    return s2, changed


def _standardize_meta_block(md: str) -> Tuple[str, bool]:
    changed = False

    def _fix(match: re.Match) -> str:
        nonlocal changed
        kind_start = match.group(1)  # METADATA or META
        body = match.group(2)
        changed = True if kind_start != "METADATA" else changed
        # Upgrade tags to METADATA
        start_tag = "<!-- METADATA:START -->"
        end_tag = "<!-- METADATA:END -->"
        mid = body
        # Remove a trailing '---' line within meta block
        mid_lines = mid.splitlines()
        # Trim leading/trailing empty lines inside the block
        while mid_lines and not mid_lines[0].strip():
            mid_lines.pop(0)
        while mid_lines and not mid_lines[-1].strip():
            mid_lines.pop()
        if mid_lines and mid_lines[-1].strip() == "---":
            mid_lines.pop()
        mid = "\n".join(mid_lines).strip("\n")
        # Ensure fenced yaml
        if "```yaml" not in mid:
            mid_fixed = "```yaml\n" + mid + "\n```"
            changed_local = True
        else:
            mid_fixed = mid
            changed_local = False
        return f"{start_tag}\n{mid_fixed}\n{end_tag}"

    # Pre-normalize malformed tags and unify META -> METADATA in-place
    pre = md
    # Fix broken start tag missing '>' (e.g., '<!-- META:START --')
    pre = re.sub(r"<!--\s*(META|METADATA):START\s*--\s*\n", "<!-- METADATA:START -->\n", pre)
    pre = re.sub(r"<!--\s*(META|METADATA):START\s*--\s*", "<!-- METADATA:START -->", pre)
    # Unify proper tags to METADATA
    pre = re.sub(r"<!--\s*(META|METADATA):START\s*-->", "<!-- METADATA:START -->", pre)
    pre = re.sub(r"<!--\s*(META|METADATA):END\s*-->", "<!-- METADATA:END -->", pre)

    new_md = META_BLOCK_RE.sub(_fix, pre)
    if new_md != md:
        changed = True
    return new_md, changed


def _process_md(path: Path) -> Tuple[bool, List[str]]:
    try:
        orig = path.read_text(encoding="utf-8")
    except Exception:
        return False, ["read_error"]
    changed_any = False
    messages: List[str] = []

    # 1) Standardize META -> METADATA and ensure fenced yaml
    std, ch1 = _standardize_meta_block(orig)
    if ch1:
        changed_any = True
        messages.append("meta_fixed")
    # 2) Strip trailing blank lines and ensure single trailing newline
    trimmed, ch2 = _strip_trailing_blank_lines(std)
    if ch2:
        changed_any = True
        messages.append("trimmed_eof")

    if changed_any:
        try:
            path.write_text(trimmed, encoding="utf-8", newline="\n")
        except Exception:
            return False, messages + ["write_error"]
    return changed_any, messages


def _process_yaml(path: Path) -> Tuple[bool, List[str]]:
    try:
        orig = path.read_text(encoding="utf-8")
    except Exception:
        return False, ["read_error"]
    trimmed, ch = _strip_trailing_blank_lines(orig)
    if ch:
        try:
            path.write_text(trimmed, encoding="utf-8", newline="\n")
        except Exception:
            return False, ["write_error"]
    return ch, (["trimmed_eof"] if ch else [])


def _git_last_change(path: Path) -> str:
    try:
        # Return short one-line for last change
        out = subprocess.check_output(["git", "log", "-n", "1", "--pretty=%h %an %ad %s", "--", str(path)], cwd=str(path.parent), stderr=subprocess.DEVNULL)
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="Write changes to files")
    ap.add_argument("--roots", nargs="*", default=[str(p) for p in DEFAULT_ROOTS], help="Roots to scan")
    ap.add_argument("--blame", action="store_true", help="Print last git change for modified files")
    args = ap.parse_args()

    roots = [Path(p) for p in args.roots]
    modified: List[Path] = []

    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            # Prompt/agent/project cards
            if p.suffix.lower() == ".md":
                changed, msg = _process_md(p)
            elif p.suffix.lower() in (".yaml", ".yml"):
                changed, msg = _process_yaml(p)
            else:
                continue
            if changed:
                modified.append(p)
                blame = _git_last_change(p) if args.blame else ""
                info = f"[FIX] {p} {';'.join(msg)}" + (f" | last: {blame}" if blame else "")
                print(info)

    print(f"Done. Modified {len(modified)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
