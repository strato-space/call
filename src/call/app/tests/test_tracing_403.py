import pytest

from types import SimpleNamespace

from call.lib import api as call_api


@pytest.mark.asyncio
async def test_call_async_tracing_403_error_json(monkeypatch):
    cfg = SimpleNamespace(
        id="FakeAgent",
        type="agent",
        project="Proj",
        agent="FakeAgent",
        prompt=None,
        path="agent/Proj/FakeAgent/agent.md",
        url=None,
        goal=None,
        instructions="Test instructions",
        model="gpt-4.1-mini",
        attributes={},
    )
    cfg.input = None
    cfg.tools = []

    def fake_build_config(**kwargs):
        return cfg, None

    monkeypatch.setattr(
        call_api, "build_runnable_instructions_config", fake_build_config, raising=False
    )

    runtime = __import__("call.app.runtime", fromlist=["run_cfg_turn"])

    async def fatal_run_cfg_turn(*, cfg, conversation_id, input_text):
        raise RuntimeError(
            'request_forbidden: blocked by tracing {"error": {"type": "request_forbidden"}}'
        )

    monkeypatch.setattr(runtime, "run_cfg_turn", fatal_run_cfg_turn, raising=True)

    result = await call_api.call_async(project="Proj", agent="FakeAgent", input="Hello")

    assert result.get("ok") is False
    assert result.get("error_code") == 403
    assert result.get("code") == "REQUEST_FORBIDDEN"
    details = result.get("details") or {}
    assert isinstance(details, dict)
    inner_error = details.get("error") or {}
    assert isinstance(inner_error, dict)
    assert inner_error.get("type") == "request_forbidden"
