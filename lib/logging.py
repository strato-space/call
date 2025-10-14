"""
Lightweight debug logging utilities for the call subsystem.

- Single source of truth for debug_print() used by app and library layers
- Gated only by CALL_DEBUG to keep behavior simple (KISS)
 - Integrates with Python's logging module for optional routing/formatting
"""

from __future__ import annotations

import os
import logging
import sys


# Tracks whether configure_logging() has successfully attached handlers
_configured_logging = False


def _env_true(name: str) -> bool:
    try:
        v = str(os.environ.get(name, "")).strip().lower()
        return v in ("1", "true", "yes", "on")
    except Exception:
        return False


_LOGGER_NAME = "call"
_logger = logging.getLogger(_LOGGER_NAME)


def configure_logging(level: int | None = None, *, json: bool = False) -> None:
    """Configure base logging once.

    - Default level: INFO (DEBUG when CALL_DEBUG=1)
    - Output: stderr with a concise formatter (timestamp level name message)
    - json flag is reserved for future JSON formatting (kept False now to avoid deps)
    """
    global _configured_logging
    try:
        # If logging already has handlers, don't reconfigure
        if _logger.handlers:
            _configured_logging = True
            return
        eff_level = (
            level
            if level is not None
            else (logging.DEBUG if _env_true("CALL_DEBUG") else logging.INFO)
        )
        _logger.setLevel(eff_level)
        # Force UTF-8 encoding for stderr to avoid encoding issues on Windows
        import sys

        if hasattr(sys.stderr, "reconfigure"):
            try:
                sys.stderr.reconfigure(encoding="utf-8")
            except Exception:
                pass
        # Set wider terminal width for better log formatting in Claude Desktop
        # This affects how Python's logging formats output
        if "COLUMNS" not in os.environ:
            os.environ["COLUMNS"] = "100"  # Optimal width for Claude Desktop logs

        handler = logging.StreamHandler()
        # Env toggle for JSON logs takes precedence unless explicit json param is given True/False
        json_env = _env_true("CALL_LOG_JSON")
        use_json = json or json_env

        if not use_json:
            # Use compact single-line format for better readability in Claude Desktop
            # Claude Desktop wraps long lines, so we keep format minimal
            fmt = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
            # Disable line wrapping by setting a very large width (if handler supports it)
            if hasattr(handler, "terminator"):
                # Keep default terminator but ensure no extra formatting
                pass
        else:

            class JSONFormatter(logging.Formatter):
                def format(self, record: logging.LogRecord) -> str:
                    try:
                        import json as _json

                        payload = {
                            "time": self.formatTime(
                                record, datefmt="%Y-%m-%dT%H:%M:%S"
                            ),
                            "level": record.levelname,
                            "logger": record.name,
                            "message": record.getMessage(),
                        }
                        return _json.dumps(payload, ensure_ascii=False)
                    except Exception:
                        # Fallback to basic formatting on any error
                        return f"{self.formatTime(record)} {record.levelname} {record.name}: {record.getMessage()}"

            fmt = JSONFormatter()
        handler.setFormatter(fmt)
        _logger.addHandler(handler)
        _configured_logging = True

        # Optional file handler
        try:
            logfile = os.environ.get("CALL_LOG_FILE", "").strip()
            if logfile:
                # Ensure parent directory exists
                import os as _os

                parent = _os.path.dirname(logfile)
                if parent:
                    _os.makedirs(parent, exist_ok=True)
                fh = logging.FileHandler(logfile, encoding="utf-8")
                fh.setFormatter(fmt)
                _logger.addHandler(fh)
        except Exception:
            pass
    except Exception:
        # Never raise on logging setup
        pass


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a module logger under the 'call' namespace."""
    try:
        if name and name != _LOGGER_NAME:
            return logging.getLogger(f"{_LOGGER_NAME}.{name}")
        return _logger
    except Exception:
        return _logger


def debug_print(*parts: str) -> None:
    """Print a debug message only when CALL_DEBUG is enabled.

    Enabled values (case-insensitive): 1, true, yes, on.
    Each call prints on a single line, prefixed with [DEBUG].
    """
    try:
        if not _env_true("CALL_DEBUG"):
            return
        # Identify module prefix token if present (e.g., "[app]", "[bot]", "[discovery]")
        prefix_token = None
        for p in parts:
            s = str(p) if p is not None else ""
            if len(s) >= 3 and s.startswith("[") and "]" in s:
                inside = s[1 : s.index("]")].strip()
                if inside and all(ch not in inside for ch in (" ", "\t", "/", "\\")):
                    prefix_token = inside
                    break

        # Choose appropriate logger based on prefix token
        logger = _logger
        try:
            if prefix_token in ("app", "bot", "discovery"):
                logger = get_logger(prefix_token)
        except Exception:
            logger = _logger

        msg = " ".join(str(p) for p in parts if p is not None)
        # Route through stdlib logging only; do not emit a separate console line
        try:
            logger.debug(msg)
        except Exception:
            pass
    except Exception:
        # Never raise from debug logging
        pass
