import json
import importlib

import pytest

pytestmark = pytest.mark.anyio("asyncio")


def test_api_normalize_selector():
    api = importlib.import_module("call.lib.api")
    assert api.normalize_selector("@Agent.md") == "Agent"
    assert api.normalize_selector("name.markdown") == "name"
    assert api.normalize_selector("@UxFab") == "UxFab"
    assert api.normalize_selector("") == ""
    assert api.normalize_selector(None) is None


def test_interpret_target_projects_agentfab_uxfab():
    """Test project targets resolve to type=project."""
    api = importlib.import_module("call.lib.api")
    row = api.interpret_target(project=None, agent=None, prompt=None, target="AgentFab")
    assert row.project == "AgentFab"
    # Projects are indexed as type=project
    assert row.type == "project"
    row = api.interpret_target(project=None, agent=None, prompt=None, target="UxFab")
    assert row.project == "UxFab"
    assert row.type == "project"


def test_build_cfg_project_via_target_preview_has_project_card():
    """Test that executable projects (with PROMPT section) can be run directly."""
    api = importlib.import_module("call.lib.api")
    cfg, err = api.build_runnable_instructions_config(
        project=None, agent=None, prompt=None, target="AgentFab", input=None
    )
    assert err is None and cfg is not None
    assert cfg.type == "project"
    # Repo-relative path from DB (prompt repo in this workspace)
    assert isinstance(cfg.path, str) and cfg.path.startswith(
        "prompt/AgentFab/project.md"
    )


def test_build_cfg_executable_project_stratoproj():
    """Test that StratoProject (executable project) can be run via target."""
    api = importlib.import_module("call.lib.api")
    cfg, err = api.build_runnable_instructions_config(
        project=None, agent=None, prompt=None, target="StratoProject", input="test"
    )
    assert err is None and cfg is not None
    assert cfg.type == "project"
    assert cfg.project == "StratoProject"
    # Should have prompt/instructions text
    assert bool(cfg.card_text or cfg.prompt_text or cfg.instructions)
    # Path should point to project.md
    assert isinstance(cfg.path, str) and "StratoProject/project.md" in cfg.path


def test_build_cfg_agents_via_target_fanfab():
    api = importlib.import_module("call.lib.api")
    # Vasil3 in FanFab
    cfg, err = api.build_runnable_instructions_config(
        project="FanFab", agent=None, prompt=None, target="Vasil3", input="hi"
    )
    assert err is None and cfg is not None
    assert cfg.type == "agent" and cfg.id == "Vasil3"
    assert isinstance(cfg.path, str) and "/Vasil3/agent.md" in cfg.path
    # AiNewsAggr in FanFab
    cfg, err = api.build_runnable_instructions_config(
        project="FanFab", agent=None, prompt=None, target="AiNewsAggr", input="hi"
    )
    assert err is None and cfg is not None
    assert cfg.type == "agent" and cfg.id == "AiNewsAggr"
    assert isinstance(cfg.path, str) and "/AiNewsAggr/agent.md" in cfg.path


def test_build_cfg_prompts_via_target():
    api = importlib.import_module("call.lib.api")
    # 11-ExtractUserPain under UxFab
    cfg, err = api.build_runnable_instructions_config(
        project="UxFab",
        agent=None,
        prompt=None,
        target="11-ExtractUserPain",
        input="hi",
    )
    assert err is None and cfg is not None
    assert cfg.type == "prompt" and cfg.id == "11-ExtractUserPain"
    assert isinstance(cfg.path, str) and cfg.path.endswith(
        "prompt/draft/11-ExtractUserPain.md"
    )
    # 10-SelfReflection under AgentFab
    cfg, err = api.build_runnable_instructions_config(
        project="AgentFab",
        agent=None,
        prompt=None,
        target="10-SelfReflection",
        input="hi",
    )
    assert err is None and cfg is not None
    assert cfg.type == "prompt" and cfg.id == "10-SelfReflection"
    assert isinstance(cfg.path, str) and cfg.path.endswith(
        "prompt/draft/10-SelfReflection.md"
    )
    # 50-DiscoveryAgent under AgentFab
    cfg, err = api.build_runnable_instructions_config(
        project="AgentFab",
        agent=None,
        prompt=None,
        target="50-DiscoveryAgent",
        input="hi",
    )
    assert err is None and cfg is not None
    assert cfg.type == "prompt" and cfg.id == "50-DiscoveryAgent"
    assert isinstance(cfg.path, str) and cfg.path.endswith(
        "prompt/draft/50-Discoveryagent.md"
    )
