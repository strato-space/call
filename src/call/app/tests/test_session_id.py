import importlib
import json

from agents.model_settings import ModelSettings
from call.lib.api import RunnableConfig


def _setup_resolve_single(monkeypatch, name="NewsAggr", project="UxFab"):
    api = importlib.import_module("call.lib.api")
    app_call = importlib.import_module("call.app.call")

    async def _noop_notify(**_kwargs):
        return None

    monkeypatch.setattr(app_call, "_notify_digest_if_applicable", _noop_notify, raising=False)
    monkeypatch.setattr(
        api,
        "resolve_agent",
        lambda **kwargs: {
            "ok": True,
            "resolved": {
                "project": kwargs.get("project") or project,
                "name": kwargs.get("agent") or name,
                "path": "/p/%s/%s/agent.md" % (project, name),
                "aliases": [],
                "prompts": ["Default"],
            },
        },
        raising=True,
    )
    monkeypatch.setattr(
        api,
        "build_runnable_instructions_config",
        lambda **kwargs: (
            RunnableConfig(
                id=name,
                type="agent",
                project=project,
                agent=name,
                prompt=None,
                path=f"agent/{project}/{name}/agent.md",
                goal=None,
                instructions="",
                model="gpt-4.1-mini",
            ),
            None,
        ),
        raising=True,
    )
    return api


def test_session_id_in_success_response(monkeypatch):
    api = _setup_resolve_single(monkeypatch)
    runtime = importlib.import_module("call.app.runtime")

    async def fake_run_cfg_turn(*, cfg, conversation_id, input_text):
        return importlib.import_module("types").SimpleNamespace(output_text="ok")

    monkeypatch.setattr(runtime, "run_cfg_turn", fake_run_cfg_turn, raising=True)

    res = api.call(project="UxFab", agent="NewsAggr", input="hello", chat_id=-100123, thread_id=10)
    assert res.get("ok") is True
    assert res.get("session_id") == "-100123:10"


def test_session_id_override_parsed_and_used(monkeypatch):
    api = _setup_resolve_single(monkeypatch)
    runtime = importlib.import_module("call.app.runtime")

    async def fake_run_cfg_turn(*, cfg, conversation_id, input_text):
        return importlib.import_module("types").SimpleNamespace(output_text="ok")

    monkeypatch.setattr(runtime, "run_cfg_turn", fake_run_cfg_turn, raising=True)

    override = "-100888:77"
    res = api.call(
        project="UxFab", agent="NewsAggr", input="hello", session_id=override
    )
    assert res.get("ok") is True
    assert res.get("session_id") == override


def test_no_session_without_routing(monkeypatch):
    api = _setup_resolve_single(monkeypatch)
    runtime = importlib.import_module("call.app.runtime")

    async def fake_run_cfg_turn(*, cfg, conversation_id, input_text):
        return importlib.import_module("types").SimpleNamespace(output_text="ok")

    monkeypatch.setattr(runtime, "run_cfg_turn", fake_run_cfg_turn, raising=True)

    res = api.call(project="UxFab", agent="NewsAggr", input="hello")
    assert res.get("ok") is True
    # When no chat/thread and no override, session_id should be omitted
    assert "session_id" not in res
