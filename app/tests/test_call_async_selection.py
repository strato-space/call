import importlib
import types


def test_call_no_data_found(monkeypatch):
    api = importlib.import_module("call.lib.api")

    # list() returns empty
    monkeypatch.setattr(api, "list", lambda **kwargs: [])

    res = api.call(project="UxFab", agent=None, prompt=None, input="hi")
    assert isinstance(res, dict)
    assert res.get("ok") is False
    assert res.get("code") == "NO_DATA_FOUND"
    assert res.get("error_code") == 404


def test_call_too_many_rows(monkeypatch):
    api = importlib.import_module("call.lib.api")

    def fake_list(**kwargs):
        return [
            {
                "name": "UxFab",
                "type": "project",
                "agents": [
                    {"type": "agent", "name": "A1", "aliases": [], "prompts": ["P1"], "path": "/p/UxFab/A1/agent.yaml"},
                    {"type": "agent", "name": "A2", "aliases": [], "prompts": ["P1"], "path": "/p/UxFab/A2/agent.yaml"},
                ],
            }
        ]

    monkeypatch.setattr(api, "list", fake_list)

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
                "path": "/p/UxFab/NewsAggr/agent.yaml",
                "aliases": ["NA"],
                "prompts": ["Default", "Draft"],
            },
        },
    )

    # Patch run_digest_pipeline to avoid heavy runtime
    app_call = importlib.import_module("call.app.call")
    async def fake_run_digest_pipeline(samples_dir, user_input="", cli_agent_name="", initial_history=None, *, prompt_override=None, project_name=None):
        return types.SimpleNamespace(), [], f"ok:{cli_agent_name}:{prompt_override}:{project_name}"

    monkeypatch.setattr(app_call, "run_digest_pipeline", fake_run_digest_pipeline)

    res = api.call(project="UxFab", agent="NewsAggr", prompt="Draft", input="hello")
    assert res.get("ok") is True
    assert res.get("final_output", "").startswith("ok:NewsAggr:Draft:UxFab")
    resolved = res.get("resolved") or {}
    assert resolved.get("name") == "NewsAggr"
    assert resolved.get("project") == "UxFab"
