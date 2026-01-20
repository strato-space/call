import io
import os
from pathlib import Path

from call.lib.utils import parse_metadata_and_prompt


def test_agentfab_project_agents_keys_present():
    # Load project.md
    prompt_repo = Path(os.environ.get("PROMPT_REPO", "")).expanduser()
    if not prompt_repo.exists():
        raise AssertionError("PROMPT_REPO is not set or does not exist")
    proj_md = prompt_repo / "AgentFab_v1" / "project.md"
    if not proj_md.exists():
        raise AssertionError(f"project.md not found at {proj_md}")
    text = proj_md.read_text(encoding="utf-8")
    meta = parse_metadata_and_prompt(text)
    assert isinstance(meta, dict), "METADATA must be a YAML mapping"
    agents = meta.get("agents")
    assert isinstance(agents, dict), "METADATA.agents must be a mapping"
    expected = {"49-BusinessAnalyticAgent", "50-DiscoveryAgent", "StratoFormatter"}
    missing = expected.difference(set(agents.keys()))
    assert not missing, f"Missing agents in project.md METADATA.agents: {missing}"
