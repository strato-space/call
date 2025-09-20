"""
Pytest configuration for running tests inside the `call/` folder.
Ensures repository root is importable as a package root so `import call.*` works,
and loads call/.env for integration tests which rely on TELEGRAM_* variables.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure repo root on sys.path so `import call.app...` works when running from call/
_here = Path(__file__).resolve()
_call_dir = _here.parent
_repo_root = _call_dir.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Best-effort environment loader from call/.env
_dotenv = _call_dir / ".env"
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
