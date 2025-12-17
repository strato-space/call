import io
import os
from pathlib import Path

from call.lib.utils import parse_metadata_and_prompt


def test_agentfab_project_agents_keys_present():
    # Load project.md
    root = Path(__file__).resolve().parents[3]
    proj_md = root / "prompt" / "AgentFab_v1" / "project.md"
    assert proj_md.exists(), f"project.md not found at {proj_md}"
    text = proj_md.read_text(encoding="utf-8")
    meta = parse_metadata_and_prompt(text)
    assert isinstance(meta, dict), "METADATA must be a YAML mapping"
    agents = meta.get("agents")
    assert isinstance(agents, dict), "METADATA.agents must be a mapping"
    expected = {"49-BusinessAnalyticAgent", "50-DiscoveryAgent", "StratoFormatter"}
    missing = expected.difference(set(agents.keys()))
    assert not missing, f"Missing agents in project.md METADATA.agents: {missing}"
