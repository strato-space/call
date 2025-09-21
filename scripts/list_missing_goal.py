import argparse
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

META_START = "<!-- METADATA:START -->"
META_END = "<!-- METADATA:END -->"


def extract_metadata_yaml(text: str) -> Optional[str]:
    ms = text.find(META_START)
    me = text.find(META_END)
    if ms == -1 or me == -1 or me <= ms:
        return None
    meta_body = text[ms:me]
    m = re.search(r"```yaml\s*\n(.*?)```", meta_body, flags=re.DOTALL)
    if not m:
        return None
    return m.group(1)


def has_goal_top_level(yaml_body: str) -> bool:
    # quick check for top-level goal: (line starting with 'goal:')
    for line in yaml_body.splitlines():
        if re.match(r"^goal\s*:\s*", line):
            return True
        # stop scanning at first non-empty non-comment top-level key after a few lines? not needed
    return False


def should_scan(path: Path) -> bool:
    return path.suffix.lower() == ".md"


def main():
    ap = argparse.ArgumentParser(description="List .md files missing goal in METADATA YAML")
    ap.add_argument("--repos", nargs="*", default=["agent", "prompt"], help="Top-level repos to scan")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[2]  # c:\home\strato-space
    targets = [root / r for r in args.repos]

    missing: List[str] = []
    no_meta: List[str] = []

    for base in targets:
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            # ignore hidden directories
            if any(p.startswith(".") for p in path.parts):
                continue
            if not should_scan(path):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            yaml_body = extract_metadata_yaml(text)
            if yaml_body is None:
                no_meta.append(str(path))
                continue
            if not has_goal_top_level(yaml_body):
                missing.append(str(path))

    out: Dict[str, Any] = {
        "ok": True,
        "missing_goal_count": len(missing),
        "missing_goal": missing,
        "no_metadata_count": len(no_meta),
        "no_metadata": no_meta,
    }
    import json
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
