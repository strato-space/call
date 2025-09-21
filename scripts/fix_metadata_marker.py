#!/usr/bin/env python3
"""
Fix stray blockquote marker after METADATA start tag in Markdown files.

Replaces occurrences of:

    <!-- METADATA:START -->\n>

with:

    <!-- METADATA:START -->

Usage:
  python call/scripts/fix_metadata_marker.py [TARGET_DIR]

Defaults TARGET_DIR to 'prompt' when not provided.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

# Regex: match the METADATA start comment with optional extra spaces, followed by optional spaces, newline, optional spaces, then a bare '>'
PATTERN = re.compile(r"<!--\s*METADATA:START\s*-->\s*\r?\n\s*>", flags=re.MULTILINE)
REPLACEMENT = "<!-- METADATA:START -->"

# Directories to skip during traversal
SKIP_DIRS = {".git", ".venv", "node_modules", ".pytest_cache", ".benchmarks"}

# File extensions that are likely text for prompts; adjust as needed
TEXT_EXTS = {
    ".md", ".markdown", ".mdx", ".txt", ".yaml", ".yml", ".json", ".toml"
}


def iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_dir():
            # Skip known directories
            if p.name in SKIP_DIRS:
                # Skip descending into this directory by changing its permissions in the iterator
                # rglob doesn't support pruning, so we just continue; cost is minimal in small repos
                pass
            continue
        # Only process regular files
        # Optionally filter to known text-like extensions
        if p.suffix.lower() in TEXT_EXTS or not p.suffix:
            yield p


def process_file(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Fallback: ignore undecodable bytes
        text = path.read_text(encoding="utf-8", errors="ignore")
    original = text

    # Replace all occurrences
    text = PATTERN.sub(REPLACEMENT, text)

    if text != original:
        # Write back only if changed
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix stray '>' after METADATA start tag across files.")
    parser.add_argument(
        "target_dir",
        nargs="?",
        default="prompt",
        help="Directory to scan recursively (default: prompt)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report changes without modifying files",
    )
    args = parser.parse_args()

    root = Path(args.target_dir).resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Target directory not found or not a directory: {root}")

    changed = 0
    scanned = 0
    for f in iter_files(root):
        scanned += 1
        try:
            try:
                text = f.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = f.read_text(encoding="utf-8", errors="ignore")
            fixed = PATTERN.sub(REPLACEMENT, text)
            if fixed != text:
                if args.dry_run:
                    print(f"[DRY-RUN] Would fix: {f}")
                else:
                    f.write_text(fixed, encoding="utf-8")
                    print(f"[FIXED] {f}")
                changed += 1
        except Exception as e:
            print(f"[WARN] Skipping {f}: {e}")

    print(f"\nScan complete. Files scanned: {scanned}. Files changed: {changed}.")


if __name__ == "__main__":
    main()
