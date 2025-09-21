"""call.lib package initializer.

Keep this file minimal to avoid import-time failures. Do not eagerly import
submodules that pull heavier dependencies.

Submodules:
- call.lib.api   — public high-level API (call, call_async, list, resolve_agent, ...)
- call.lib.repo  — repo indexer (scan/list)
- call.lib.discovery — discovery helpers (projects index, agent scanning, prompts)
"""

__all__ = [
    # Submodules are available via `from call.lib import api, repo_db, repo_fs, discovery`
    "api",
    "repo_db",
    "repo_fs",
    "discovery",
]

# Lazy attribute loader for submodules (PEP 562 style)
def __getattr__(name):  # type: ignore[override]
    import importlib as _importlib
    # Backwards-compat alias: 'repo' maps to 'repo_db'
    if name == "repo":
        return _importlib.import_module("call.lib.repo_db")
    if name in ("api", "repo_db", "repo_fs", "discovery"):
        return _importlib.import_module(f"call.lib.{name}")
    raise AttributeError(name)
