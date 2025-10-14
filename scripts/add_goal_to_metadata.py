import argparse
import re
from pathlib import Path
from typing import Optional, Tuple, List

META_START = "<!-- METADATA:START -->"
META_END = "<!-- METADATA:END -->"
PROMPT_START = "<!-- PROMPT:START -->"
PROMPT_END = "<!-- PROMPT:END -->"


def extract_blocks(
    text: str,
) -> Tuple[
    Optional[Tuple[int, int]],
    Optional[Tuple[int, int]],
    Optional[Tuple[int, int]],
    Optional[Tuple[int, int]],
]:
    """Return (meta_start_idx, meta_end_idx, prompt_start_idx, prompt_end_idx) as (start, end) pairs of slice indices.
    Indices point to the start of the marker lines themselves.
    """
    ms = text.find(META_START)
    me = text.find(META_END)
    ps = text.find(PROMPT_START)
    pe = text.find(PROMPT_END)
    return (
        (ms, me) if (ms != -1 and me != -1 and me > ms) else None,
        (ps, pe) if (ps != -1 and pe != -1 and pe > ps) else None,
        None,
        None,
    )


def extract_metadata_yaml(text: str) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    """Extract YAML code block inside METADATA, return (yaml_body, slice_indices_of_yaml_body).
    The slice refers to the inside of the ```yaml ... ``` content only.
    """
    ms = text.find(META_START)
    me = text.find(META_END)
    if ms == -1 or me == -1 or me <= ms:
        return None, None
    meta_body = text[ms:me]
    # Find ```yaml ... ```
    m = re.search(r"```yaml\s*\n(.*?)```", meta_body, flags=re.DOTALL)
    if not m:
        return None, None
    yaml_body = m.group(1)
    # Compute absolute indices for yaml_body slice
    y_start_rel, y_end_rel = m.span(1)
    y_abs_start = ms + y_start_rel
    y_abs_end = ms + y_end_rel
    return yaml_body, (y_abs_start, y_abs_end)


def extract_prompt_text(text: str) -> Optional[str]:
    ps = text.find(PROMPT_START)
    pe = text.find(PROMPT_END)
    if ps == -1 or pe == -1 or pe <= ps:
        return None
    return text[ps + len(PROMPT_START) : pe]


def extract_goal_from_prompt(prompt_text: str) -> Optional[str]:
    if not prompt_text:
        return None
    lines = prompt_text.splitlines()

    # Normalize heading variations: '# Goal', '## Goal', '#Goal'
    goal_line_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*#{1,3}\s*Goal\s*$", line):
            goal_line_idx = i
            break
    # If no explicit Goal heading, take first non-empty paragraph at start
    if goal_line_idx is None:
        # Find first non-empty line
        start = 0
        while start < len(lines) and lines[start].strip() == "":
            start += 1
        if start >= len(lines):
            return None
        # Collect until blank line
        out: List[str] = []
        for j in range(start, len(lines)):
            if lines[j].strip() == "":
                break
            out.append(lines[j].rstrip())
        goal = "\n".join(out).strip()
        return goal or None

    # Collect lines after heading until next heading or blank-blank break before another Hx
    out: List[str] = []
    i = goal_line_idx + 1
    # skip leading blanks after heading
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    for j in range(i, len(lines)):
        # Stop at next atx heading (#, ##, ###)
        if re.match(r"^\s*#{1,6}\s+", lines[j]):
            break
        out.append(lines[j].rstrip())
    goal = "\n".join(out).strip()
    return goal or None


def ensure_goal_first_in_yaml(orig_yaml: str, goal_text: str) -> str:
    """Insert or replace goal as the first key in YAML block, using block scalar |- format."""
    # Remove any existing top-level goal block
    yaml_lines = orig_yaml.splitlines()
    new_lines: List[str] = []

    def is_top_key(line: str) -> bool:
        return bool(re.match(r"^[A-Za-z0-9_-]+\s*:\s*", line))

    # Locate existing 'goal' block (top-level)
    idx = 0
    while idx < len(yaml_lines):
        line = yaml_lines[idx]
        if re.match(r"^goal\s*:\s*", line):
            # Skip the goal block including any indented continuation
            idx += 1
            while idx < len(yaml_lines):
                nxt = yaml_lines[idx]
                if nxt.startswith(" ") or nxt.startswith("\t"):
                    idx += 1
                    continue
                # also treat list continuation at top-level as new top-key if not indented
                if is_top_key(nxt) or (
                    nxt and not (nxt.startswith(" ") or nxt.startswith("\t"))
                ):
                    break
                idx += 1
            break
        idx += 1

    # Rebuild without the old goal block
    if idx == 0:
        # no goal found; keep original
        cleaned = yaml_lines
    else:
        # idx points to end of goal block; reconstruct
        # Need to find start of goal block
        g_start = None
        for i, line in enumerate(yaml_lines):
            if re.match(r"^goal\s*:\s*", line):
                g_start = i
                break
        if g_start is not None:
            cleaned = yaml_lines[:g_start] + yaml_lines[idx:]
        else:
            cleaned = yaml_lines

    # Build new goal block
    goal_block = ["goal: |- "]
    for gl in goal_text.splitlines() or [""]:
        goal_block.append("  " + gl)

    # Insert at top (as first attribute)
    # Keep any leading comments or empty lines at the very top of YAML body
    k = 0
    while k < len(cleaned) and (
        cleaned[k].strip() == "" or cleaned[k].lstrip().startswith("#")
    ):
        k += 1
    new_yaml_lines = (
        cleaned[:k]
        + goal_block
        + ([""] if (k < len(cleaned) and cleaned[k].strip() != "") else [])
        + cleaned[k:]
    )
    return "\n".join(new_yaml_lines).rstrip("\n") + "\n"


def process_text(text: str) -> Tuple[str, bool, Optional[str]]:
    # Extract YAML block and PROMPT
    yaml_body, yaml_slice = extract_metadata_yaml(text)
    prompt_text = extract_prompt_text(text)
    if yaml_body is None or yaml_slice is None or not prompt_text:
        return text, False, None

    goal = extract_goal_from_prompt(prompt_text)
    if not goal:
        return text, False, None

    # Build new YAML with goal first
    new_yaml = ensure_goal_first_in_yaml(yaml_body, goal)

    # Splice back
    start, end = yaml_slice
    new_text = text[:start] + new_yaml + text[end:]
    changed = new_text != text
    return new_text, changed, goal


def should_process(path: Path) -> bool:
    if path.suffix.lower() != ".md":
        return False
    try:
        txt = path.read_text(encoding="utf-8")
    except Exception:
        return False
    return (META_START in txt) and (PROMPT_START in txt)


def main():
    ap = argparse.ArgumentParser(
        description="Add 'goal' as first METADATA attribute by extracting from PROMPT"
    )
    ap.add_argument(
        "--repos",
        nargs="*",
        default=["agent", "prompt"],
        help="Top-level repos to scan",
    )
    ap.add_argument(
        "--apply", action="store_true", help="Write changes (otherwise dry-run)"
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]
    targets = [root / r for r in args.repos]

    changed_files: List[Path] = []
    skipped_files: List[Tuple[Path, str]] = []

    for base in targets:
        if not base.exists():
            skipped_files.append((base, "repo not found"))
            continue
        for path in base.rglob("*.md"):
            # skip .git or other hidden dirs just in case
            if any(part.startswith(".") for part in path.parts):
                continue
            if not should_process(path):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e:
                skipped_files.append((path, f"read error: {e}"))
                continue
            new_text, changed, goal = process_text(text)
            if changed:
                changed_files.append(path)
                if args.apply:
                    try:
                        path.write_text(new_text, encoding="utf-8")
                    except Exception as e:
                        skipped_files.append((path, f"write error: {e}"))

    # Report
    print(
        {
            "ok": True,
            "apply": args.apply,
            "changed_count": len(changed_files),
            "changed_files": [str(p) for p in changed_files],
            "skipped": [(str(p), reason) for p, reason in skipped_files],
        }
    )


if __name__ == "__main__":
    main()
