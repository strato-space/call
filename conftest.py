"""Test configuration for call.

Sets repo roots and a temporary repo.db to keep tests self-contained.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
import pytest

_REPO_ROOT = Path(__file__).resolve().parent
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

if "CALL_REPO_ROOT" not in os.environ:
    os.environ["CALL_REPO_ROOT"] = str(_REPO_ROOT)
if "CALL_WORKSPACE_ROOT" not in os.environ:
    os.environ["CALL_WORKSPACE_ROOT"] = str(_REPO_ROOT.parent)


def _has_any(root: Path, rel_paths: list[Path]) -> bool:
    return any((root / rel_path).exists() for rel_path in rel_paths)


def _pick_repo(
    env_key: str,
    candidates: list[Path],
    sentinels: list[Path],
) -> Path | None:
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        env_path = Path(env_value)
        if env_path.exists() and (not sentinels or _has_any(env_path, sentinels)):
            return env_path
    for cand in candidates:
        if cand.exists() and (not sentinels or _has_any(cand, sentinels)):
            return cand
    if env_value:
        env_path = Path(env_value)
        if env_path.exists():
            return env_path
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def _ensure_env_path(
    env_key: str,
    repo_path: Path | None,
    sentinels: list[Path],
) -> None:
    if not repo_path:
        return
    current = os.environ.get(env_key, "").strip()
    if not current:
        os.environ[env_key] = str(repo_path)
        return
    current_path = Path(current)
    if not current_path.exists():
        os.environ[env_key] = str(repo_path)
        return
    if sentinels and not _has_any(current_path, sentinels):
        os.environ[env_key] = str(repo_path)


_AGENT_SENTINELS = [
    Path("UxFab"),
    Path("FanFab"),
]
_PROMPT_SENTINELS = [
    Path("AgentFab_v1/project.md"),
    Path("StratoProject/project.md"),
    Path("AgentFab/project.md"),
]


_agent_repo = _pick_repo(
    "AGENT_REPO",
    [
        _REPO_ROOT.parent / "agent",
        _REPO_ROOT / "agent",
        Path("/home/backup/leagcy-2026-01-13/agent"),
        Path("/home/strato-space/agent"),
    ],
    _AGENT_SENTINELS,
)
_prompt_repo = _pick_repo(
    "PROMPT_REPO",
    [
        _REPO_ROOT.parent / "prompt",
        _REPO_ROOT / "prompt",
        Path("/home/backup/leagcy-2026-01-13/prompt"),
        Path("/home/strato-space/prompt"),
    ],
    _PROMPT_SENTINELS,
)

_ensure_env_path("AGENT_REPO", _agent_repo, _AGENT_SENTINELS)
_ensure_env_path("PROMPT_REPO", _prompt_repo, _PROMPT_SENTINELS)

if "DB_PATH" not in os.environ:
    db_root = Path(tempfile.mkdtemp(prefix="call-repo-db-"))
    os.environ["DB_PATH"] = str(db_root / "repo.db")
_TEST_DB_PATH = os.environ["DB_PATH"]

try:
    from call.lib import repo_db, repo_fs

    repo_db.DB_PATH = os.environ["DB_PATH"]
    repo_fs.repo_db.DB_PATH = os.environ["DB_PATH"]

    if _agent_repo or _prompt_repo:
        repo_fs.reload(repos=["agent", "prompt"], full_form=False)
except Exception:
    pass


@pytest.fixture(autouse=True)
def _sync_env_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_PATH", _TEST_DB_PATH)
    if _agent_repo:
        monkeypatch.setenv("AGENT_REPO", str(_agent_repo))
    if _prompt_repo:
        monkeypatch.setenv("PROMPT_REPO", str(_prompt_repo))
