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
from dotenv import load_dotenv

# Ensure repo root on sys.path so `import call.app...` works when running from call/
_here = Path(__file__).resolve()
_call_dir = _here.parent
_repo_root = _call_dir.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Environment loader via python-dotenv
# Load call/.env first (preferred for call subsystem), then repo-root .env for fallbacks.
# Do not override existing OS/CI environment variables.
try:
    load_dotenv(dotenv_path=str(_call_dir / ".env"), override=False)
    load_dotenv(dotenv_path=str(_repo_root / ".env"), override=False)
except Exception:
    # best-effort only
    pass

# Defaults for repos if not set
pr = _repo_root / "prompt"
ar = _repo_root / "agent"
if not os.getenv("PROMPT_REPO") and pr.exists():
    os.environ["PROMPT_REPO"] = str(pr)
if not os.getenv("AGENT_REPO") and ar.exists():
    os.environ["AGENT_REPO"] = str(ar)

# Populate repo.db once per session via filesystem sync facade
try:
    from call.lib import repo_fs as _repo_fs
    _repo_fs.scan()
except Exception:
    # Non-fatal; individual tests may monkeypatch DB access
    pass
