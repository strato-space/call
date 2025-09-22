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
    api = importlib.import_module("call.lib.api")
    pj, ag, pr, err = api.interpret_target(project=None, agent=None, prompt=None, target="AgentFab")
    assert err is None and pj == "AgentFab" and ag is None and pr is None
    pj, ag, pr, err = api.interpret_target(project=None, agent=None, prompt=None, target="UxFab")
    assert err is None and pj == "UxFab" and ag is None and pr is None


def test_build_cfg_project_via_target_preview_has_project_card():
    api = importlib.import_module("call.lib.api")
    cfg, err = api.build_runnable_instructions_config(project=None, agent=None, prompt=None, target="AgentFab", input=None, merge=False)
    assert err is None and cfg is not None
    assert cfg.type == "project"
    # Repo-relative path from DB (prompt repo in this workspace)
    assert isinstance(cfg.path, str) and cfg.path.startswith("prompt/AgentFab/project.md")


def test_build_cfg_agents_via_target_fanfab():
    api = importlib.import_module("call.lib.api")
    # Vasil3 in FanFab
    cfg, err = api.build_runnable_instructions_config(project="FanFab", agent=None, prompt=None, target="Vasil3", input="hi", merge=False)
    assert err is None and cfg is not None
    assert cfg.type == "agent" and cfg.name == "Vasil3"
    assert isinstance(cfg.path, str) and "/Vasil3/agent.md" in cfg.path
    # AiNewsAggr in FanFab
    cfg, err = api.build_runnable_instructions_config(project="FanFab", agent=None, prompt=None, target="AiNewsAggr", input="hi", merge=False)
    assert err is None and cfg is not None
    assert cfg.type == "agent" and cfg.name == "AiNewsAggr"
    assert isinstance(cfg.path, str) and "/AiNewsAggr/agent.md" in cfg.path


def test_build_cfg_prompts_via_target():
    api = importlib.import_module("call.lib.api")
    # 11-ExtractUserPain under UxFab
    cfg, err = api.build_runnable_instructions_config(project="UxFab", agent=None, prompt=None, target="11-ExtractUserPain", input="hi", merge=False)
    assert err is None and cfg is not None
    assert cfg.type == "prompt" and cfg.name == "11-ExtractUserPain"
    assert isinstance(cfg.path, str) and cfg.path.endswith("prompt/draft/11-ExtractUserPain.md")
    # 10-SelfReflection under AgentFab
    cfg, err = api.build_runnable_instructions_config(project="AgentFab", agent=None, prompt=None, target="10-SelfReflection", input="hi", merge=False)
    assert err is None and cfg is not None
    assert cfg.type == "prompt" and cfg.name == "10-SelfReflection"
    assert isinstance(cfg.path, str) and cfg.path.endswith("prompt/draft/10-SelfReflection.md")
    # 50-DiscoveryAgent under AgentFab
    cfg, err = api.build_runnable_instructions_config(project="AgentFab", agent=None, prompt=None, target="50-DiscoveryAgent", input="hi", merge=False)
    assert err is None and cfg is not None
    assert cfg.type == "prompt" and cfg.name == "50-DiscoveryAgent"
    assert isinstance(cfg.path, str) and cfg.path.endswith("prompt/draft/50-Discoveryagent.md")
