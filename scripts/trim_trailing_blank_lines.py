#!/usr/bin/env python3
"""
Trim trailing blank lines at the end of files for Markdown and YAML.

- Removes all empty/whitespace-only lines at EOF.
- Ensures the file ends with exactly one newline (LF) for consistency.

Usage:
  python call/scripts/trim_trailing_blank_lines.py [TARGET_DIR ...] [--dry-run]

Examples:
  # Preview changes in agent/ and prompt/
  python call/scripts/trim_trailing_blank_lines.py agent prompt --dry-run

  # Apply changes
  python call/scripts/trim_trailing_blank_lines.py agent prompt
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

# File extensions to process
EXTS = {".md", ".markdown", ".mdx", ".yaml", ".yml"}

SKIP_DIRS = {".git", ".venv", "node_modules", ".pytest_cache", ".benchmarks"}


def iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if p.suffix.lower() in EXTS:
            yield p


def trim_eof_blank_lines(text: str) -> str:
    # Normalize newlines to \n in-memory for processing
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Split keeping line endings? We only care about content lines; simpler splitlines(True) not needed
    lines = text.split("\n")

    # If the file is empty, return as-is
    if len(lines) == 0:
        return text

    # Remove trailing blank/whitespace-only lines at the end
    i = len(lines) - 1
    while i >= 0 and (lines[i].strip() == ""):
        i -= 1

    # Keep lines up to i (inclusive), then ensure single trailing newline
    kept = lines[: i + 1]
    new_text = "\n".join(kept)

    # Ensure exactly one trailing newline if file not empty
    if new_text != "" and not new_text.endswith("\n"):
        new_text += "\n"

    return new_text


def process_file(path: Path, dry_run: bool = False) -> bool:
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        original = path.read_text(encoding="utf-8", errors="ignore")

    fixed = trim_eof_blank_lines(original)

    if fixed != original:
        if dry_run:
            print(f"[DRY-RUN] Would trim: {path}")
        else:
            path.write_text(fixed, encoding="utf-8")
            print(f"[TRIMMED] {path}")
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Trim trailing blank lines at EOF for Markdown and YAML files.")
    parser.add_argument("targets", nargs="*", default=["agent", "prompt"], help="Target directories to scan")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying files")
    args = parser.parse_args()

    # Resolve and validate targets
    roots: list[Path] = []
    for t in args.targets:
        p = Path(t).resolve()
        if p.exists() and p.is_dir():
            roots.append(p)
        else:
            print(f"[WARN] Skipping missing or non-directory target: {p}")

    scanned = 0
    changed = 0

    for root in roots:
        for file in iter_files(root):
            # Skip files inside unwanted dirs by checking any parent name
            if any(parent.name in SKIP_DIRS for parent in file.parents):
                continue
            scanned += 1
            try:
                if process_file(file, dry_run=args.dry_run):
                    changed += 1
            except Exception as e:
                print(f"[WARN] Skipping {file}: {e}")

    print(f"\nScan complete. Files scanned: {scanned}. Files changed: {changed}.")


if __name__ == "__main__":
    main()
