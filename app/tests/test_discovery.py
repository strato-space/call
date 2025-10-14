from pathlib import Path
import os
import importlib


def _ensure_test_env():
    # Minimal env required by call.app.call on import
    os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "-2710557620")
    os.environ.setdefault("TELEGRAM_SECOND_CHAT_ID", "-2710557620")
    os.environ.setdefault("TELEGRAM_THREAD_ID", "10")
    os.environ.setdefault("TELEGRAPH_TOKEN", "test-telegraph")
    os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
    # Ensure discovery uses the workspace prompt repo regardless of cwd
    os.environ.setdefault(
        "PROMPT_REPO", str(Path(__file__).resolve().parents[3] / "prompt")
    )


def _mod():
    _ensure_test_env()
    return importlib.import_module("call.app.call")


def test_db_only_discover_agent_yaml_direct_names(monkeypatch):
    m = _mod()
    repo_mod = importlib.import_module("call.lib.repo_db")
    # Monkeypatch DB query
    monkeypatch.setattr(
        repo_mod,
        "find_agents",
        lambda **kw: (
            [
                {
                    "project": "AgentFab",
                    "agent": "AgentFab",
                    "path": str(
                        Path(m.discover_prompt_repo()) / "AgentFab" / "project.md"
                    ),
                }
            ]
            if (kw.get("agent") == "AgentFab")
            else []
        ),
        raising=True,
    )

    p = m.discover_agent_yaml("AgentFab")
    assert p is not None
    assert Path(p).name in {"agent.md", "project.md"}
    assert Path(p).parent.name == "AgentFab"


def test_discover_agent_yaml_stratoslav_direct_only(monkeypatch):
    m = _mod()
    repo_mod = importlib.import_module("call.lib.repo_db")
    # Only exact direct name works (no aliases)
    monkeypatch.setattr(
        repo_mod,
        "find_agents",
        lambda **kw: (
            [
                {
                    "project": "AgentFab",
                    "agent": "Stratoslav",
                    "path": str(
                        Path(m.discover_prompt_repo())
                        / "AgentFab"
                        / "Stratoslav"
                        / "agent.yaml"
                    ),
                }
            ]
            if (kw.get("agent") == "Stratoslav")
            else []
        ),
        raising=True,
    )

    p = m.discover_agent_yaml("Stratoslav")
    assert p is not None
    assert Path(p).parent.name == "Stratoslav"


def test_discover_agent_yaml_known_project_dir_if_db_row_present(monkeypatch):
    m = _mod()
    repo = m.discover_prompt_repo()
    candidate = repo / "UxFab" / "StratoSummarizer2" / "agent.md"
    if candidate.exists():
        repo_mod = importlib.import_module("call.lib.repo_db")
        monkeypatch.setattr(
            repo_mod,
            "find_agents",
            lambda **kw: (
                [
                    {
                        "project": "UxFab",
                        "agent": "StratoSummarizer2",
                        "path": str(candidate),
                    }
                ]
                if (kw.get("agent") == "StratoSummarizer2")
                else []
            ),
            raising=True,
        )
        p = m.discover_agent_yaml("StratoSummarizer2")
        assert p is not None
        assert Path(p) == candidate
