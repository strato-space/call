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


def _mod():
    _ensure_test_env()
    return importlib.import_module('call.app.call')


def test_special_case_agentfab_points_to_root_yaml():
    m = _mod()
    p = m.discover_agent_yaml("@AgentFab")
    assert p is not None, "AgentFab root agent.yaml should be found"
    assert Path(p).name == "agent.yaml"
    # ensure it's the root card, not a sub-agent
    assert Path(p).parent.name == "AgentFab"


# Alias @Default has been removed by policy (Sep 12, 2025). Test deleted.


def test_agents_index_name_and_alias_for_ainewsaggr():
    # Direct name
    m = _mod()
    p1 = m.discover_agent_yaml("AiNewsAggr")
    assert p1 is not None, "AiNewsAggr should resolve via agents index"
    assert Path(p1).parent.name == "AiNewsAggr"
    # Alias from agents/agents.yaml
    p2 = m.discover_agent_yaml("ai-news")
    assert p2 is not None, "Alias ai-news should resolve via agents index"
    assert Path(p2).parent.name == "AiNewsAggr"


def test_agents_index_alias_for_stratoslav():
    m = _mod()
    for alias in ("@Stratoslav", "StratoSlav", "Стратослав"):
        p = m.discover_agent_yaml(alias)
        assert p is not None, f"Alias {alias} should resolve via agents index"
        assert Path(p).parent.name == "Stratoslav"


def test_fallback_scan_still_works_for_known_agent_dirs():
    # Pick an agent that exists under prompt/agents/ to validate fallback path shape
    m = _mod()
    repo = m.discover_prompt_repo()
    candidate = repo / "agents" / "DialogChunk" / "agent.yaml"
    if candidate.exists():
        p = m.discover_agent_yaml("dialogchunk")  # lower-case to force normalization
        assert p is not None
        assert Path(p) == candidate
