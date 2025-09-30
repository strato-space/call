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

    # resolve_agent returns a single resolved item
    monkeypatch.setattr(
        api,
        "resolve_agent",
        lambda **kwargs: {
            "ok": True,
            "resolved": {
                "project": kwargs.get("project") or "UxFab",
                "name": kwargs.get("agent") or "NewsAggr",
                "path": "/p/UxFab/NewsAggr/agent.md",
                "aliases": ["NA"],
                "prompts": ["Default", "Draft"],
            },
        },
    )

    # Patch build_and_run_agent to avoid heavy runtime and return a cfg with _last_final_output
    app_call = importlib.import_module("call.app.call")
    class _Cfg:
        def __init__(self, out):
            self._last_final_output = out
    class _DummyAgent: pass
    class _DummySession: pass
    class _CM:
        def __init__(self, out):
            self.out = out
        async def __aenter__(self):
            return _DummyAgent(), _Cfg(self.out), _DummySession()
        async def __aexit__(self, exc_type, exc, tb):
            return False
    def fake_build_and_run_agent(cli_agent_name, samples_dir, user_input="", prompt_override=None, project_name=None):
        return _CM(f"ok:{cli_agent_name}:{prompt_override}:{project_name}")

    monkeypatch.setattr(app_call, "build_and_run_agent", fake_build_and_run_agent)

    res = api.call(project="UxFab", agent="NewsAggr", prompt="Draft", input="hello")
    assert res.get("ok") is True
    assert res.get("final_output", "").startswith("ok:NewsAggr:Draft:UxFab")
    resolved = res.get("resolved") or {}
    assert resolved.get("name") == "NewsAggr"
    assert resolved.get("project") == "UxFab"


def test_call_event_ack(monkeypatch):
    api = importlib.import_module("call.lib.api")

    # Ensure pipeline is not invoked by tracking import attempts
    import sys
    marker = object()
    monkeypatch.setitem(sys.modules, "call.app.call", marker)

    res = api.call(event="session_closed")
    assert isinstance(res, dict)
    assert res.get("ok") is True
    assert res.get("event") == "session_closed"

    # The marker should remain untouched (no attribute access on placeholder)
    assert sys.modules["call.app.call"] is marker


def test_api_interpret_exec_payload_event_only():
    api = importlib.import_module("call.lib.api")

    payload = {"event": "session_closed"}
    kwargs, err = api.api_interpret_exec_payload(payload)
    assert err is None
    assert kwargs.get("event") == "session_closed"
    assert kwargs.get("input").startswith("{")
    assert kwargs.get("project") is None
    assert kwargs.get("agent") is None
    assert kwargs.get("prompt") is None
    assert kwargs.get("target") is None


def test_api_interpret_exec_payload_event_with_target():
    api = importlib.import_module("call.lib.api")

    payload = {"event": "session_closed", "target": "UxFab"}
    kwargs, err = api.api_interpret_exec_payload(payload)
    assert kwargs == {}
    assert err is not None
    assert err.get("error_code") == 400
    assert "event" in (err.get("description") or "")


def test_api_interpret_exec_payload_event_with_multiple_selectors_error():
    api = importlib.import_module("call.lib.api")

    payload = {"event": "session_closed", "project": "UxFab", "agent": "DialogPostAnalysis"}
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
