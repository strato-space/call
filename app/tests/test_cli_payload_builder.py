import json
import types
import argparse
import pytest

pytestmark = pytest.mark.anyio("asyncio")


def _ns(**kwargs):
    # Minimal Namespace for cmd_call
    defaults = dict(
        project="",
        agent="",
        prompt="",
        target="",
        input="",
        session_id="",
        echo=False,
        print_instructions=False,
        trace=0,
        trace_file="",
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_cli_call_parse_input_uses_shared_payload_builder(monkeypatch, capsys):
    from call.cli.main import cmd_call
    from call.lib import api as call_api

    # Arrange: stub builder and call()
    built_json = json.dumps({
        "target": "AgentFab",
        "input": "@3-OnlineChunkSummarization",
        "context": [],
    }, ensure_ascii=False)

    def fake_build_input_payload(*, target, main_text, extra_context=None, reply_text=None):
        assert target == "AgentFab"
        assert main_text == "@3-OnlineChunkSummarization"
        return built_json, {"target": target, "input": main_text, "context": []}

    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "echo": {"used_cli": True}}

    monkeypatch.setattr(call_api, "build_input_payload", fake_build_input_payload, raising=True)
    monkeypatch.setattr(call_api, "call", fake_call, raising=True)

    # Act
    rc = cmd_call(_ns(target="AgentFab", parse_input="@3-OnlineChunkSummarization"))

    # Assert
    assert rc == 0
    assert json.loads(capsys.readouterr().out).get("ok") is True
    assert captured.get("input") == built_json
    assert captured.get("target") == "AgentFab"


def test_cli_call_raw_input_is_passed_as_is(monkeypatch, capsys):
    from call.cli.main import cmd_call
    from call.lib import api as call_api

    # Arrange: ensure builder is not called for --input
    called = {"builder": 0}

    def fake_build_input_payload(**kwargs):
        called["builder"] += 1
        return "{}", {}

    captured = {}

    def fake_call(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(call_api, "build_input_payload", fake_build_input_payload, raising=True)
    monkeypatch.setattr(call_api, "call", fake_call, raising=True)

    # Act
    rc = cmd_call(_ns(target="AgentFab", input="as is text input"))

    # Assert
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("ok") is True
    assert captured.get("input") == "as is text input"
    assert called["builder"] == 0
