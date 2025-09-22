import json
import types

from call.cli import main as cli_main
from call.lib.api import build_input_payload, normalize_selector


def test_normalize_selector_strips_prefix_suffix():
    assert normalize_selector('@Agent.md') == 'Agent'
    assert normalize_selector('name.markdown') == 'name'
    assert normalize_selector('@UxFab') == 'UxFab'
    assert normalize_selector('') == ''
    assert normalize_selector(None) is None


def test_build_input_payload_uses_db_prompt_name(monkeypatch):
    from call.lib import api as call_api

    def fake_list_prompts(project=None, agent=None, prompt=None, state=None, target=None):
        return [{
            "prompt": "50-DiscoveryAgent",
            "project": "AgentFab",
            "agent": "DiscoveryAgent",
            "rel_path": "prompt/draft/50-Discoveryagent.md",
        }]

    monkeypatch.setattr(call_api, "list_prompts", fake_list_prompts, raising=True)

    payload_json, payload_dict = build_input_payload(target="AgentFab", main_text="50-DiscoveryAgent", download=False)

    obj = json.loads(payload_json)
    ctx = obj.get("context") or []
    assert isinstance(ctx, list) and len(ctx) == 1
    item = ctx[0]
    assert item["name"] == "50-DiscoveryAgent"
    assert item["path"].endswith("prompt/draft/50-Discoveryagent.md")


def test_cli_echo_includes_resolved_when_flag(monkeypatch, capsys):
    from call.lib import api as call_api

    class DummyCfg:
        project = "AgentFab"
        name = "DiscoveryAgent"
        prompt_override = None
        type = "project"
        path = "agent/AgentFab/DiscoveryAgent/agent.md"
        url = "https://example.com/agent"

    def fake_build_runnable_instructions_config(**kwargs):
        return DummyCfg(), None

    def fake_build_input_payload(**kwargs):
        return (json.dumps({"target": kwargs.get("target") or "AgentFab", "input": kwargs.get("main_text") or ""}, ensure_ascii=False), {})

    monkeypatch.setattr(call_api, "build_runnable_instructions_config", fake_build_runnable_instructions_config, raising=True)
    monkeypatch.setattr(call_api, "build_input_payload", fake_build_input_payload, raising=True)

    args = types.SimpleNamespace(
        project="AgentFab",
        agent="",
        prompt="",
        target="AgentFab",
        input="hello",
        parse_input="",
        session_id="",
        download_context=False,
        echo=True,
        resolved=True,
        print_instructions=False,
        merge=False,
        trace=0,
        trace_file="",
    )

    # Invoke
    rc = cli_main.cmd_call(args)
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    # Flattened payload: top-level keys
    assert data.get("target") == "AgentFab"
    assert data.get("input") == "hello"
    assert "resolved" in data
    assert data["resolved"]["project"] == "AgentFab"
    # When type is 'project', resolved.agent must be null per new behavior
    assert data["resolved"].get("agent") is None
