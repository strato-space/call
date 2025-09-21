import importlib
from pathlib import Path


def test_mcp_config_yaml_present_and_parsable():
    call_mod = importlib.import_module("call.app.call")
    cfg_path = Path(__file__).resolve().parents[2] / "mcp_config.yaml"
    assert cfg_path.exists(), "call/mcp_config.yaml should exist"
    data = call_mod._load_yaml(cfg_path)
    assert isinstance(data, dict)
    assert "mcpServers" in data
