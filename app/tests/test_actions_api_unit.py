import json
import pytest
from fastapi.testclient import TestClient

import call.actions.main as main
from call.actions.main import app


@pytest.fixture()
def client(monkeypatch):
    # Ensure bearer guard expects TEST_TOKEN in this process
    monkeypatch.setenv("API_ACCESS_TOKEN", "TEST_TOKEN")
    return TestClient(app)


@pytest.fixture()
def auth_headers():
    # Bearer guard allows tests even without token, but keep header for explicitness
    return {"Authorization": "Bearer TEST_TOKEN"}


def test_call_ok(monkeypatch, client: TestClient, auth_headers):
    def fake_api_call(**kwargs):
        # Expect target routed via name
        assert kwargs.get("target") == "TestAgent"
        assert kwargs.get("input") == "hi"
        return {"ok": True, "final_output": "done"}

    monkeypatch.setattr(main, "api_call", fake_api_call, raising=True)

    r = client.get("/call", params={"name": "TestAgent", "input": "hi", "echo": "1"}, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True


def test_exec_post_agent_context_ok(monkeypatch, client: TestClient, auth_headers):
    def fake_api_call(**kwargs):
        # Context is serialized to JSON string by the handler
        inp = kwargs.get("input") or ""
        try:
            payload = json.loads(inp)
        except Exception:
            payload = {}
        assert isinstance(payload, dict)
        return {"ok": True, "agent": "DialogPostAnalysis"}

    monkeypatch.setattr(main, "api_call", fake_api_call, raising=True)

    payload = {"agent": "DialogPostAnalysis", "context": {"text": "hello"}}
    r = client.post("/exec", params={"echo": "1"}, json=payload, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert data.get("agent") == "DialogPostAnalysis"


def test_exec_post_both_agent_and_prompt_400(monkeypatch, client: TestClient, auth_headers):
    def _fail_if_called(**kwargs):
        raise AssertionError("api_call should not be invoked when multiple selectors are provided")

    monkeypatch.setattr(main, "api_call", _fail_if_called, raising=True)

    payload = {"agent": "A", "prompt": "P", "context": {}}
    r = client.post("/exec", json=payload, headers=auth_headers)
    assert r.status_code == 400


def test_prompts_ok(monkeypatch, client: TestClient, auth_headers):
    monkeypatch.setattr(main, "list_prompts", lambda **kwargs: [
        {"prompt_id": "33-Questioning", "agent": "DialogPostAnalysis", "project": "UxFab"},
        {"prompt_id": "main", "agent": "Stratoslav", "project": "FanFab"},
    ], raising=True)

    r = client.get("/prompts", params={"project": "UxFab", "prompt": "33-*"}, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and data
    assert any(x.get("prompt_id") == "33-Questioning" for x in data)


def test_agents_ok(monkeypatch, client: TestClient, auth_headers):
    monkeypatch.setattr(main, "api_list", lambda **kwargs: [
        {"name": "UxFab", "type": "project", "agents": [
            {"name": "DialogPostAnalysis", "aliases": [], "prompts": ["33-Questioning"], "path": "..."}
        ]}
    ], raising=True)

    r = client.get("/agents", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list) and data and data[0].get("name") == "UxFab"
