import pytest
from call.lib import api as api_module
from call.lib import repo_db as repo_db_module
from call.lib.api import build_runnable_instructions_config


@pytest.fixture(autouse=True)
def _minimal_repo(monkeypatch):
    def _empty_find(**kwargs):
        return []

    monkeypatch.setattr(
        api_module.call_repo, "find_projects", _empty_find, raising=True
    )
    monkeypatch.setattr(api_module.call_repo, "find_prompts", _empty_find, raising=True)

    def _find_agents(**kwargs):
        return [
            {
                "project": kwargs.get("project") or "",
                "agent": kwargs.get("agent") or "TestAgent",
                "id": kwargs.get("agent") or "TestAgent",
                "target": "TestAgent",
            }
        ]

    monkeypatch.setattr(api_module.call_repo, "find_agents", _find_agents, raising=True)
    monkeypatch.setattr(
        api_module.call_repo, "get_card", lambda cid: ({}, "", ""), raising=True
    )

    monkeypatch.setattr(
        api_module,
        "interpret_target",
        lambda **kwargs: repo_db_module.RepoCardRow(
            id="TestAgent",
            target="TestAgent",
            project="",
            agent="TestAgent",
            prompt="",
            path="",
            state="",
            engine="",
            orchestration="",
            type="agent",
            rel_path="",
            url="",
            goal="",
            card="",
        ),
        raising=True,
    )


def _build_with_overrides(overrides: dict):
    cfg, err = build_runnable_instructions_config(
        project=None,
        agent="TestAgent",
        prompt=None,
        target=None,
        input="",
        attributes_override=overrides,
    )
    assert err is None
    assert cfg is not None
    return cfg.model_settings


def test_model_settings_prefers_model_specific_over_generic():
    overrides = {
        "model": "gpt-5",
        "model-settings": {"temperature": 0.9},
        "model-settings-gpt-5": {"temperature": 0.1, "reasoning": {"effort": "medium"}},
    }
    settings = _build_with_overrides(overrides)
    assert settings.temperature == 0.1
    assert getattr(settings.reasoning, "effort", None) == "medium"


def test_model_settings_generic_used_when_specific_missing():
    overrides = {
        "model": "gpt-5",
        "model-settings": {"temperature": 0.7, "top_p": 0.8},
    }
    settings = _build_with_overrides(overrides)
    assert settings.temperature == 0.7
    assert settings.top_p == 0.8
