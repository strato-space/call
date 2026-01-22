"""
Pytest configuration for running tests inside the `call/` folder.
Ensures repository root is importable as a package root so `import call.*` works,
and loads call/.env for integration tests which rely on TELEGRAM_* variables.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

def _find_repo_root(start: Path) -> Path:
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return start.parent


# Ensure src root on sys.path so `import call.*` works when running from repo root
_here = Path(__file__).resolve()
_repo_root = _find_repo_root(_here)
_src_root = _repo_root / "src"
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

if "CALL_REPO_ROOT" not in os.environ:
    os.environ["CALL_REPO_ROOT"] = str(_repo_root)
if "CALL_WORKSPACE_ROOT" not in os.environ:
    os.environ["CALL_WORKSPACE_ROOT"] = str(_repo_root.parent)

# Best-effort environment loader from call/.env
_dotenv = _repo_root / ".env"
if _dotenv.exists():
    try:
        for line in _dotenv.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            k, v = s.split("=", 1)
            key = k.strip()
            val = v.strip().strip('"')
            if key and key not in os.environ:
                os.environ[key] = val
        # Normalize TELEGRAM_TOKEN -> TELEGRAM_BOT_TOKEN for tests/tools that expect the latter
        if not os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_TOKEN"):
            os.environ["TELEGRAM_BOT_TOKEN"] = os.environ["TELEGRAM_TOKEN"]
    except Exception:
        pass
