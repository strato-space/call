"""Builder tests: ensure minimal config fields, model precedence, and error cases."""

import os
import textwrap
from pathlib import Path

import pytest

from call.lib import api as api_module
from call.lib.api import build_runnable_instructions_config
from call.lib import repo_db as repo_db_module


@pytest.fixture(autouse=True)
def _repo_card_loader(monkeypatch):
    original_get_card = repo_db_module.get_card

    def _fake_get_card(card_id: str):
        path = Path(str(card_id))
        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                text = ""
            if not text:
                return {}, "", ""
            suffix = path.suffix.lower()
            if suffix in (".md", ".markdown"):
                from call.lib.utils import parse_metadata_and_prompt

                try:
                    parsed = parse_metadata_and_prompt(text, path=str(path))
                except ValueError:
                    return {}, "", text
                except Exception:
                    return {}, "", text
                meta = dict(parsed or {}) if isinstance(parsed, dict) else {}
                body = str((parsed or {}).get("prompt") or "")
                meta.pop("prompt", None)
                return meta, body, text
            if suffix in (".yaml", ".yml"):
                import yaml as _yaml

                data = _yaml.safe_load(text) or {}
                if isinstance(data, dict):
                    return data, "", text
                return {}, "", text
        return original_get_card(card_id)

    monkeypatch.setattr(repo_db_module, "get_card", _fake_get_card, raising=True)


@pytest.mark.parametrize(
    "project,agent,prompt",
    [
        ("UxFab", "DialogPostAnalysis", None),
        ("FanFab", "AiNewsAggr", None),
    ],
)
def test_builder_minimal_fields_ok(project, agent, prompt):
    cfg, err = build_runnable_instructions_config(project=project, agent=agent, prompt=prompt)
    assert err is None
    assert cfg is not None
    # Required minimal fields
    assert isinstance(cfg.id, str) and cfg.id
    assert isinstance(cfg.project, str) and cfg.project in (project,)
    assert cfg.path is None or isinstance(cfg.path, str)
    # base_dir should be a directory when yaml path is resolved
    if cfg.base_dir:
        assert os.path.isdir(cfg.base_dir)


def test_builder_prompt_override_set():
    cfg, err = build_runnable_instructions_config(project="UxFab", agent="DialogPostAnalysis", prompt="33-Questioning")
    assert err is None
    assert cfg and cfg.prompt == "33-Questioning"


def test_builder_model_override_has_priority():
    cfg, err = build_runnable_instructions_config(
        project="UxFab",
        agent="DialogPostAnalysis",
        attributes_override={"model": "gpt-test-override"},
    )
    assert err is None
    assert cfg is not None
    assert cfg.model == "gpt-test-override"
    assert cfg.attributes.get("model") == "gpt-test-override"


def test_builder_target_prefers_prompt_over_agent(monkeypatch, tmp_path):
    agent_path = tmp_path / "agent.md"
    agent_path.write_text(
        """
        <!-- METADATA:START -->
        ```yaml
        model: gpt-from-agent
        ```
        <!-- METADATA:END -->

        <!-- PROMPT:START -->
        Agent body
        <!-- PROMPT:END -->
        """,
        encoding="utf-8",
    )

    monkeypatch.setattr(api_module.call_repo, "list_prompts", lambda **_: [
        {"prompt": "PromptAlpha", "project": "ProjX", "agent": "AgentA"},
        {"prompt": "PromptBeta", "project": "ProjX", "agent": "AgentB"},
    ])
    monkeypatch.setattr(api_module, "interpret_target", lambda **_: ("ProjX", "AgentB", "PromptBeta", None))
    monkeypatch.setattr(api_module, "resolve_agent", lambda **kw: {
        "ok": True,
        "resolved": {
            "project": "ProjX",
            "name": "AgentB",
            "path": str(agent_path),
        },
    })
    monkeypatch.setattr(api_module.call_repo, "find_projects", lambda **_: [])
    monkeypatch.setattr(api_module.call_repo, "find_agents", lambda **_: [])

    cfg, err = build_runnable_instructions_config(project="ProjX", agent="AgentA", prompt=None, target="PromptBeta")
    assert err is None
    assert cfg.prompt == "PromptBeta"
    assert cfg.agent == "AgentB"


def test_builder_db_prompt_without_agent(monkeypatch, tmp_path):
    prompt_path = tmp_path / "prompt.md"
    prompt_path.write_text(
        """
        <!-- METADATA:START -->
        ```yaml
        model: gpt-prompt
        ```
        <!-- METADATA:END -->

        <!-- PROMPT:START -->
        Prompt body
        <!-- PROMPT:END -->
        """,
        encoding="utf-8",
    )

    monkeypatch.setattr(api_module.call_repo, "find_projects", lambda **_: [])
    monkeypatch.setattr(api_module.call_repo, "find_agents", lambda **_: [])
    monkeypatch.setattr(api_module.call_repo, "find_prompts", lambda **_: [{"prompt": "StandalonePrompt", "project": "ProjZ", "path": str(prompt_path)}])

    cfg, err = build_runnable_instructions_config(project=None, agent=None, prompt="StandalonePrompt")
    assert err is None
    assert cfg is not None
    assert cfg.prompt == "StandalonePrompt"
    assert cfg.project == "ProjZ"
    assert cfg.model == "gpt-prompt"

def test_builder_nested_configs_and_lists(monkeypatch, tmp_path):
    from call.lib import api as api_module

    project_dir = tmp_path / "agent" / "ProjX"
    agent_dir = project_dir / "AgentY"
    prompt_ready = tmp_path / "prompt" / "ready"
    project_dir.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)
    prompt_ready.mkdir(parents=True, exist_ok=True)

    project_md = project_dir / "project.md"
    agent_md = agent_dir / "agent.md"
    prompt_md = prompt_ready / "PromptZ.md"

    project_md.write_text(
        textwrap.dedent(
            """
            <!-- METADATA:START -->
            ```yaml
            goal: Project goal
            role: project role
            tools:
              - FileSearchTool[vs_project]
            mcp:
              - id: project-mcp
            ```
            <!-- METADATA:END -->

            <!-- PROMPT:START -->
            Project instructions
            <!-- PROMPT:END -->
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    agent_md.write_text(
        textwrap.dedent(
            """
            <!-- METADATA:START -->
            ```yaml
            goal: Agent goal
            role: agent role
            tools:
              - FileSearchTool[vs_agent]
            mcp:
              - id: agent-mcp
            ```
            <!-- METADATA:END -->

            <!-- PROMPT:START -->
            Agent instructions
            <!-- PROMPT:END -->
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    prompt_md.write_text(
        textwrap.dedent(
            """
            <!-- METADATA:START -->
            ```yaml
            goal: Prompt goal
            role: prompt role
            agent: AgentY
            tools:
              - FileSearchTool[vs_prompt]
            mcp:
              - id: prompt-mcp
            ```
            <!-- METADATA:END -->

            <!-- PROMPT:START -->
            Prompt instructions
            <!-- PROMPT:END -->
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    def fake_resolve_agent(*, project=None, agent=None, prompt=None, target=None):
        return {
            "ok": True,
            "resolved": {
                "project": project or "ProjX",
                "name": agent or "AgentY",
                "path": str(agent_md),
            },
        }

    monkeypatch.setattr(api_module, "resolve_agent", fake_resolve_agent, raising=True)

    monkeypatch.setattr(
        api_module.call_repo,
        "find_projects",
        lambda **_: [{"project": "ProjX", "path": str(project_md), "target": str(project_md)}],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_agents",
        lambda **kw: [
            {"project": "ProjX", "agent": "AgentY", "path": str(agent_md), "target": str(agent_md)}
        ]
        if kw.get("agent") in (None, "AgentY")
        else [],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_prompts",
        lambda **kw: [
            {"project": "ProjX", "agent": "AgentY", "path": str(prompt_md), "target": str(prompt_md)}
        ]
        if kw.get("prompt") in (None, "PromptZ")
        else [],
        raising=True,
    )

    cfg, err = build_runnable_instructions_config(
        project="ProjX",
        agent="AgentY",
        prompt="PromptZ",
    )

    assert err is None
    assert cfg is not None

    assert cfg.project == "ProjX"
    assert cfg.agent == "AgentY"
    assert cfg.prompt == "PromptZ"

    assert cfg.role == "prompt role"
    assert cfg.tools == ["FileSearchTool[vs_prompt]"]
    assert cfg.mcp == [{"id": "prompt-mcp"}]

    assert cfg.instructions.strip().startswith("Prompt instructions")



def test_builder_no_data_found_error():
    cfg, err = build_runnable_instructions_config(project="UxFab", agent="NoSuchAgent", prompt=None)
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

    monkeypatch.setattr(
        api_module.call_repo,
        "find_projects",
        lambda **_: [{"project": "AgentFab", "path": str(proj_path), "target": str(proj_path)}],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_agents",
        lambda **_: [{"project": "AgentFab", "agent": "StratoFormatter", "path": str(agent_path), "target": str(agent_path)}],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_prompts",
        lambda **_: [{"prompt": "33-Questioning", "path": str(prompt_path), "target": str(prompt_path)}],
        raising=True,
    )

    cfg, err = build_runnable_instructions_config(
        project="AgentFab",
        agent="StratoFormatter",
        prompt="33-Questioning",
        target=None,
        input=None,
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

    monkeypatch.setattr(
        api_module.call_repo,
        "find_projects",
        lambda **_: [{"project": "AgentFab", "path": str(proj_path), "target": str(proj_path)}],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_agents",
        lambda **_: [{"project": "AgentFab", "agent": "StratoFormatter", "path": str(agent_path), "target": str(agent_path)}],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_prompts",
        lambda **_: [{"prompt": "33-Questioning", "path": str(prompt_path), "target": str(prompt_path)}],
        raising=True,
    )
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")

    cfg, err = build_runnable_instructions_config(
        project="AgentFab",
        agent="StratoFormatter",
        prompt="33-Questioning",
        target=None,
        input=None,
    )

    assert err is None
    assert cfg is not None
    assert cfg.model == "gpt-4o-mini"


def test_builder_parses_prompt_block_with_spaced_tags(monkeypatch, tmp_path):
    agent_path = tmp_path / "agent.md"
    agent_path.write_text(
        textwrap.dedent(
            """
            <!--   METADATA : START   -->
            ```yaml
            model: gpt-5
            ```
            <!-- METADATA:END -->

            <!-- PROMPT: START -->
            ВАЖНО: сделай что-то
            <!-- PROMPT: END -->
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        api_module.call_repo,
        "find_projects",
        lambda **_: [],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_agents",
        lambda **_: [{"project": "FanFab", "agent": "Vasil3", "path": str(agent_path), "target": str(agent_path)}],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_prompts",
        lambda **_: [],
        raising=True,
    )

    cfg, err = build_runnable_instructions_config(
        project="FanFab",
        agent="Vasil3",
    )

    assert err is None
    assert cfg is not None
    assert "ВАЖНО" in cfg.instructions
    assert cfg.instructions.strip().startswith("ВАЖНО")


def test_builder_agent_attributes_do_not_include_project_metadata():
    cfg, err = build_runnable_instructions_config(project="FanFab", agent="Vasil3")

    assert err is None
    assert cfg is not None
    assert cfg.agent == "Vasil3"
    assert cfg.project == "FanFab"

    attrs = cfg.attributes
    assert isinstance(attrs, dict)
    assert attrs.get("model") == "gpt-5"
    assert attrs.get("id") == "Vasil3"
    assert "agents" not in attrs
    assert "aliases" not in attrs


def test_builder_prompt_attributes_only_inherit_model(monkeypatch, tmp_path):
    project_path = tmp_path / "proj.md"
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
                {prompt_body}
                <!-- PROMPT:END -->
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    write_card(
        project_path,
        """
        model: gpt-project
        model-settings-gpt-project:
          temperature: 0.3
        project-only: keep-out
        """,
        "project body",
    )
    write_card(
        agent_path,
        """
        model: gpt-agent
        model-settings-gpt-agent:
          reasoning:
            effort: high
        agent-only: keep-out
        """,
        "agent body",
    )
    write_card(
        prompt_path,
        """
        id: PromptTest
        prompt-only: keep
        """,
        "prompt body",
    )

    monkeypatch.setattr(
        api_module.call_repo,
        "find_projects",
        lambda **_: [{"project": "Proj", "path": str(project_path), "target": str(project_path), "id": str(project_path)}],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_agents",
        lambda **_: [{"project": "Proj", "agent": "Agent", "path": str(agent_path), "target": str(agent_path), "id": str(agent_path)}],
        raising=True,
    )
    monkeypatch.setattr(
        api_module.call_repo,
        "find_prompts",
        lambda **_: [{"project": "Proj", "agent": "Agent", "prompt": "PromptTest", "path": str(prompt_path), "target": str(prompt_path), "id": str(prompt_path)}],
        raising=True,
    )

    cfg, err = build_runnable_instructions_config(project="Proj", agent="Agent", prompt="PromptTest")

    assert err is None
    assert cfg is not None
    assert cfg.model == "gpt-agent"

    attrs = cfg.attributes
    assert attrs.get("id") == "PromptTest"
    assert attrs.get("prompt-only") == "keep"
    assert "agent-only" not in attrs
    assert "project-only" not in attrs
    assert attrs.get("model") == "gpt-agent"
    assert attrs.get("model-settings-gpt-agent") == {"reasoning": {"effort": "high"}}
    assert attrs.get("model-settings-gpt-project") == {"temperature": 0.3}
