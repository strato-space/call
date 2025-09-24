"""Builder tests: ensure minimal config fields, model precedence, and error cases."""

import os
import textwrap
from pathlib import Path

import pytest

from call.lib import api as api_module
from call.lib.api import build_runnable_instructions_config


@pytest.mark.parametrize(
    "project,agent,prompt",
    [
        ("UxFab", "DialogPostAnalysis", None),
        ("FanFab", "AiNewsAggr", None),
    ],
)
def test_builder_minimal_fields_ok(project, agent, prompt):
    cfg, err = build_runnable_instructions_config(project=project, agent=agent, prompt=prompt, merge=True)
    assert err is None
    assert cfg is not None
    # Required minimal fields
    assert isinstance(cfg.name, str) and cfg.name
    assert isinstance(cfg.project, str) and cfg.project in (project,)
    assert cfg.agent_yaml_path is None or os.path.exists(cfg.agent_yaml_path)
    # base_dir should be a directory when yaml path is resolved
    if cfg.base_dir:
        assert os.path.isdir(cfg.base_dir)


def test_builder_prompt_override_set():
    cfg, err = build_runnable_instructions_config(project="UxFab", agent="DialogPostAnalysis", prompt="33-Questioning", merge=True)
    assert err is None
    assert cfg and cfg.prompt_override == "33-Questioning"


def test_builder_no_data_found_error():
    cfg, err = build_runnable_instructions_config(project="UxFab", agent="NoSuchAgent", prompt=None, merge=True)
    assert cfg is None
    assert isinstance(err, dict)
    assert err.get("ok") is False
    assert err.get("code") in ("NO_DATA_FOUND", "NOT_FOUND")
    # Preserve 404 mapping when nothing is found
    assert int(err.get("error_code", 404)) in (400, 404)


def test_builder_model_prefers_prompt_over_agent_and_project(monkeypatch, tmp_path):
    proj_path = tmp_path / "project.md"
    agent_path = tmp_path / "agent.md"
    prompt_path = tmp_path / "prompt.md"

    def write_card(path: Path, meta: str, prompt_body: str = ""):
        path.write_text(
            textwrap.dedent(
                f"""
                <!-- METADATA:START -->
                ```yaml
                {meta}
                ```
                <!-- METADATA:END -->
                <!-- PROMPT:START -->
                ```prompt
                {prompt_body}
                ```
                <!-- PROMPT:END -->
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    write_card(proj_path, "model: gpt-5", "Project body")
    write_card(agent_path, "model: gpt-4.1-mini", "Agent body")
    write_card(prompt_path, "model: gpt-4.1-large", "Prompt body")

    monkeypatch.setattr(api_module.call_repo, "find_projects", lambda **_: [{"project": "AgentFab", "path": str(proj_path)}], raising=True)
    monkeypatch.setattr(api_module.call_repo, "find_agents", lambda **_: [{"project": "AgentFab", "agent": "StratoFormatter", "path": str(agent_path)}], raising=True)
    monkeypatch.setattr(api_module.call_repo, "find_prompts", lambda **_: [{"prompt": "33-Questioning", "path": str(prompt_path)}], raising=True)

    cfg, err = build_runnable_instructions_config(
        project="AgentFab",
        agent="StratoFormatter",
        prompt="33-Questioning",
        target=None,
        input=None,
        merge=False,
    )

    assert err is None
    assert cfg is not None
    assert cfg.model == "gpt-4.1-large"


def test_builder_model_falls_back_to_env(monkeypatch, tmp_path):
    proj_path = tmp_path / "project.md"
    agent_path = tmp_path / "agent.md"
    prompt_path = tmp_path / "prompt.md"

    def write_card(path: Path, meta: str, prompt_body: str = ""):
        path.write_text(
            textwrap.dedent(
                f"""
                <!-- METADATA:START -->
                ```yaml
                {meta}
                ```
                <!-- METADATA:END -->
                <!-- PROMPT:START -->
                ```prompt
                {prompt_body}
                ```
                <!-- PROMPT:END -->
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    write_card(proj_path, "title: Project")
    write_card(agent_path, "title: Agent")
    write_card(prompt_path, "title: Prompt")

    monkeypatch.setattr(api_module.call_repo, "find_projects", lambda **_: [{"project": "AgentFab", "path": str(proj_path)}], raising=True)
    monkeypatch.setattr(api_module.call_repo, "find_agents", lambda **_: [{"project": "AgentFab", "agent": "StratoFormatter", "path": str(agent_path)}], raising=True)
    monkeypatch.setattr(api_module.call_repo, "find_prompts", lambda **_: [{"prompt": "33-Questioning", "path": str(prompt_path)}], raising=True)
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")

    cfg, err = build_runnable_instructions_config(
        project="AgentFab",
        agent="StratoFormatter",
        prompt="33-Questioning",
        target=None,
        input=None,
        merge=False,
    )

    assert err is None
    assert cfg is not None
    assert cfg.model == "gpt-4o-mini"
