import importlib
import json

from agents.model_settings import ModelSettings


def _setup_resolve_single(monkeypatch, name="NewsAggr", project="UxFab"):
    api = importlib.import_module("call.lib.api")
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
    return api


def test_session_id_in_success_response(monkeypatch):
    api = _setup_resolve_single(monkeypatch)
    app_call = importlib.import_module("call.app.call")

    class _Cfg:
        def __init__(self, out):
            self._last_final_output = out
            self.id = "NewsAggr"
            self.type = "agent"
            self.project = "UxFab"
            self.agent = "NewsAggr"
            self.prompt = None
            self.path = "/p/UxFab/NewsAggr/agent.md"
            self.url = None
            self.goal = None
            self.instructions = ""
            self.model = "gpt-4.1-mini"
            self.model_settings = ModelSettings()
            self.attributes = {}
            self.tools = []
            self.mcp = []

    class _DummyAgent:
        pass

    class _DummySession:
        def __init__(self, sid):
            self.id = sid

    class _CM:
        def __init__(self, sid):
            self.sid = sid
        async def __aenter__(self):
            return _DummyAgent(), _Cfg("ok"), _DummySession(self.sid)
        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_build_and_run_agent(*, cfg, user_input=""):
        # Simulate a created session id (agentless format chat[:thread])
        return _CM("-100123:10")

    monkeypatch.setattr(app_call, "build_and_run_agent", fake_build_and_run_agent, raising=True)

    res = api.call(project="UxFab", agent="NewsAggr", input="hello")
    assert res.get("ok") is True
    assert res.get("session_id") == "-100123:10"


def test_session_id_override_parsed_and_used(monkeypatch):
    api = _setup_resolve_single(monkeypatch)
    app_call = importlib.import_module("call.app.call")

    class _Cfg:
        def __init__(self, out):
            self._last_final_output = out
            self.id = "NewsAggr"
            self.type = "agent"
            self.project = "UxFab"
            self.agent = "NewsAggr"
            self.prompt = None
            self.path = "/p/UxFab/NewsAggr/agent.md"
            self.url = None
            self.goal = None
            self.instructions = ""
            self.model = "gpt-4.1-mini"
            self.model_settings = ModelSettings()
            self.attributes = {}
            self.tools = []
            self.mcp = []

    class _DummyAgent:
        pass

    class _DummySession:
        def __init__(self, sid):
            self.id = sid

    class _CM:
        def __init__(self, name):
            self._name = name
        async def __aenter__(self):
            # Build SID from globals set by lib.api (parsed from session_id)
            chat = getattr(app_call, "selected_chat_id", None)
            thr = getattr(app_call, "selected_thread_id", None)
            sid = f"{self._name}:{chat}:{thr}" if thr is not None else f"{self._name}:{chat}"
            return _DummyAgent(), _Cfg("ok"), _DummySession(sid)
        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_build_and_run_agent(*, cfg, user_input=""):
        return _CM(cfg.agent)

    monkeypatch.setattr(app_call, "build_and_run_agent", fake_build_and_run_agent, raising=True)

    override = "-100888:77"
    res = api.call(project="UxFab", agent="NewsAggr", input="hello", session_id=override)
    assert res.get("ok") is True
    assert res.get("session_id") == override


def test_no_session_without_routing(monkeypatch):
    api = _setup_resolve_single(monkeypatch)
    app_call = importlib.import_module("call.app.call")

    class _Cfg:
        def __init__(self, out):
            self._last_final_output = out
            self.id = "NewsAggr"
            self.type = "agent"
            self.project = "UxFab"
            self.agent = "NewsAggr"
            self.prompt = None
            self.path = "/p/UxFab/NewsAggr/agent.md"
            self.url = None
            self.goal = None
            self.instructions = ""
            self.model = "gpt-4.1-mini"
            self.model_settings = ModelSettings()
            self.attributes = {}
            self.tools = []
            self.mcp = []

    class _DummyAgent:
        pass

    class _CM:
        async def __aenter__(self):
            # No session created path
            return _DummyAgent(), _Cfg("ok"), None
        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_build_and_run_agent(*, cfg, user_input=""):
        return _CM()

    monkeypatch.setattr(app_call, "build_and_run_agent", fake_build_and_run_agent, raising=True)

    res = api.call(project="UxFab", agent="NewsAggr", input="hello")
    assert res.get("ok") is True
    # When no chat/thread and no override, session_id should be omitted
    assert "session_id" not in res
