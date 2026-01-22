from __future__ import annotations

import os
from pathlib import Path


def _resolve_env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def repo_root() -> Path:
    """Return the call repo root (contains pyproject.toml)."""
    env_root = _resolve_env_path("CALL_REPO_ROOT")
    if env_root:
        return env_root

    start = Path(__file__).resolve()
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd().resolve()


def workspace_root() -> Path:
    """Return the workspace root that contains sibling repos (prompt/agent)."""
    env_root = _resolve_env_path("CALL_WORKSPACE_ROOT")
    if env_root:
        return env_root
    repo_parent = repo_root().parent

    def _is_workspace(candidate: Path) -> bool:
        return (candidate / "agent").exists() and (candidate / "prompt").exists()

    if _is_workspace(repo_parent):
        return repo_parent

    fallback = Path("/home/strato-space")
    if _is_workspace(fallback):
        return fallback

    return repo_parent


def default_env_candidates() -> list[Path]:
    root = repo_root()
    workspace = workspace_root()
    return [root / ".env", workspace / ".env"]


def default_mcp_config_path() -> Path:
    return repo_root() / "mcp_config.yaml"


def default_cache_dir() -> Path:
    value = os.environ.get("CALL_CACHE_DIR", "").strip()
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            return (workspace_root() / path).resolve()
        return path.resolve()
    return repo_root() / ".cache" / "call"


def _legacy_candidates(paths: list[Path]) -> list[Path]:
    return [p for p in paths if p is not None]


def legacy_event_db_path() -> Path:
    candidates = _legacy_candidates(
        [
            repo_root() / "var" / "legacy-call.db",
            repo_root() / "var" / "legacy-call-data" / "call.db",
            repo_root() / "call" / "call.db",
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def legacy_repo_db_path() -> Path:
    candidates = _legacy_candidates(
        [
            repo_root() / "var" / "legacy-repo.db",
            repo_root() / "var" / "legacy-call-data" / "repo.db",
            repo_root() / "call" / "repo.db",
        ]
    )
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def default_event_db_path() -> Path:
    legacy = legacy_event_db_path()
    if legacy.exists():
        return legacy
    return default_cache_dir() / "call.db"


def default_repo_db_path() -> Path:
    legacy = legacy_repo_db_path()
    if legacy.exists():
        return legacy
    fallback = repo_root() / "repo.db"
    if fallback.exists():
        return fallback
    return default_cache_dir() / "repo.db"
