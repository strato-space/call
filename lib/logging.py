"""
Lightweight debug logging utilities for the call subsystem.

- Single source of truth for debug_print() used by app and library layers
- Gated only by CALL_DEBUG to keep behavior simple (KISS)
"""
from __future__ import annotations

import os


def _env_true(name: str) -> bool:
    try:
        v = str(os.environ.get(name, "")).strip().lower()
        return v in ("1", "true", "yes", "on")
    except Exception:
        return False


def debug_print(*parts: str) -> None:
    """Print a debug message only when CALL_DEBUG is enabled.

    Enabled values (case-insensitive): 1, true, yes, on.
    Each call prints on a single line, prefixed with [DEBUG].
    """
    try:
        if not _env_true("CALL_DEBUG"):
            return
        msg = " ".join(str(p) for p in parts if p is not None)
        print(f"[DEBUG] {msg}")
    except Exception:
        # Never raise from debug logging
        pass
