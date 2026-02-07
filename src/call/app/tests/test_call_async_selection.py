import importlib
import types


def test_call_no_data_found(monkeypatch):
    api = importlib.import_module("call.lib.api")
    repo_mod = importlib.import_module("call.lib.repo_db")

    # No agents in project
    monkeypatch.setattr(repo_mod, "find_agents", lambda **kw: [], raising=True)

    res = api.call(project="UxFab", agent=None, prompt=None, input="hi")
    assert isinstance(res, dict)
    assert res.get("ok") is False
    assert res.get("code") == "NO_DATA_FOUND"
    assert res.get("error_code") == 404


def test_call_too_many_rows(monkeypatch):
    api = importlib.import_module("call.lib.api")
    repo_mod = importlib.import_module("call.lib.repo_db")

    rows = [
        {"project": "UxFab", "agent": "A1", "path": "/p/UxFab/A1/agent.yaml"},
        {"project": "UxFab", "agent": "A2", "path": "/p/UxFab/A2/agent.yaml"},
    ]
    monkeypatch.setattr(repo_mod, "find_agents", lambda **kw: rows, raising=True)

    res = api.call(project="UxFab", agent=None, prompt=None, input="hi")
    assert isinstance(res, dict)
    assert res.get("ok") is False
    assert res.get("code") == "TOO_MANY_ROWS"
    assert isinstance(res.get("options"), list) and len(res["options"]) == 2


def test_call_success_with_prompt_override(monkeypatch):
    api = importlib.import_module("call.lib.api")

    # Patch middleware runtime to avoid network/engine execution.
    runtime = importlib.import_module("call.app.runtime")
    app_call = importlib.import_module("call.app.call")

    async def _noop_notify(**_kwargs):
        return None

    monkeypatch.setattr(app_call, "_notify_digest_if_applicable", _noop_notify, raising=False)

    async def fake_run_cfg_turn(*, cfg, conversation_id, input_text):
        return types.SimpleNamespace(
            output_text=f"ok:{cfg.agent}:{cfg.prompt}:{cfg.project}:{input_text}"
        )

    monkeypatch.setattr(runtime, "run_cfg_turn", fake_run_cfg_turn, raising=True)

    res = api.call(
        project="UxFab",
        agent="DialogPostAnalysis",
        prompt="33-Questioning",
        input="hello",
    )
    assert res.get("ok") is True
    assert res.get("final_output", "").startswith(
        "ok:DialogPostAnalysis:33-Questioning:UxFab"
    )
    resolved = res.get("resolved") or {}
    assert resolved.get("agent") == "DialogPostAnalysis"
    assert resolved.get("project") == "UxFab"


def test_call_event_ack(monkeypatch):
    api = importlib.import_module("call.lib.api")

    # Ensure pipeline is not invoked by tracking import attempts
    import sys

    marker = object()
    monkeypatch.setitem(sys.modules, "call.app.call", marker)

    res = api.call(event="session_transcription_done")
    assert isinstance(res, dict)
    assert res.get("ok") is True
    assert res.get("event") == "session_transcription_done"

    # The marker should remain untouched (no attribute access on placeholder)
    assert sys.modules["call.app.call"] is marker


def test_call_event_error_test(monkeypatch):
    api = importlib.import_module("call.lib.api")

    import sys

    marker = object()
    monkeypatch.setitem(sys.modules, "call.app.call", marker)

    res = api.call(event="error_test", agent="DemoAgent", project="DemoProj")
    assert isinstance(res, dict)
    assert res.get("ok") is False
    assert res.get("code") == "FAKE_EVENT_ERROR"
    assert res.get("error_code") == 500
    assert "agent" not in res
    assert "project" not in res

    assert sys.modules["call.app.call"] is marker


def test_api_interpret_exec_payload_event_only():
    api = importlib.import_module("call.lib.api")

    payload = {"event": "session_transcription_done"}
    kwargs, err = api.api_interpret_exec_payload(payload)
    assert err is None
    assert kwargs.get("event") == "session_transcription_done"
    assert kwargs.get("input").startswith("{")
    assert kwargs.get("project") is None
    assert kwargs.get("agent") is None
    assert kwargs.get("prompt") is None
    assert kwargs.get("target") is None


def test_api_interpret_exec_payload_event_with_target():
    api = importlib.import_module("call.lib.api")

    payload = {"event": "session_transcription_done", "target": "UxFab"}
    kwargs, err = api.api_interpret_exec_payload(payload)
    assert kwargs == {}
    assert err is not None
    assert err.get("error_code") == 400
    assert "event" in (err.get("description") or "")


def test_api_interpret_exec_payload_event_with_multiple_selectors_error():
    api = importlib.import_module("call.lib.api")

    payload = {
        "event": "session_transcription_done",
        "project": "UxFab",
        "agent": "DialogPostAnalysis",
    }
    kwargs, err = api.api_interpret_exec_payload(payload)
    assert kwargs == {}
    assert err is not None
    assert err.get("error_code") == 400


def test_api_interpret_exec_payload_includes_model_override():
    api = importlib.import_module("call.lib.api")

    payload = {"agent": "DialogPostAnalysis", "model": "gpt-special"}
    kwargs, err = api.api_interpret_exec_payload(payload)
    assert err is None
    assert kwargs.get("agent") == "DialogPostAnalysis"
    attrs = kwargs.get("attributes")
    assert isinstance(attrs, dict)
    assert attrs.get("model") == "gpt-special"
