"""
Pytest configuration for running tests inside the `call/` folder.
- Ensures repository root is importable so `import call.*` works
- Loads call/.env for integration tests
- Performs a repo scan once before the test session to populate repo.db
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
    except Exception:
        pass

# Defaults for repos if not set
pr = _repo_root / "prompt"
ar = _repo_root / "agent"
if not os.getenv("PROMPT_REPO") and pr.exists():
    os.environ["PROMPT_REPO"] = str(pr)
if not os.getenv("AGENT_REPO") and ar.exists():
    os.environ["AGENT_REPO"] = str(ar)

# Populate repo.db once per session
try:
    from call.lib import repo as _repo
    _repo.scan()
except Exception:
    # Non-fatal; individual tests may monkeypatch DB access
    pass
