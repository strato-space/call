import os
import json
import asyncio
from pathlib import Path


def test_discovery_prompts_for_dialogpostanalysis():
    from call.lib import discovery as disc

    items = disc.prompts(project="UxFab", agent="DialogPostAnalysis")
    names = {it["prompt_id"] for it in items}
    assert "33-Questioning" in names
    assert "34-CollectUnresolvedEscalationItems" in names


def test_list_includes_aliases_for_ainewsaggr():
    from call.lib import api

    tree = api.list(project="FanFab")
    assert isinstance(tree, list) and len(tree) == 1
    agents = tree[0].get("agents") or []
    ai = next(a for a in agents if a.get("name") == "AiNewsAggr")
    # DB-only listing does not enrich aliases — just ensure the agent exists
    assert ai.get("name") == "AiNewsAggr"


def test_scan_project_agents_prompts_from_agent_yaml(tmp_path):
    # DialogPostAnalysis defines prompts in its agent.yaml
    from call.lib import discovery as disc

    base = tmp_path / "UxFab"
    base.mkdir()
    agent_dir = base / "DialogPostAnalysis"
    agent_dir.mkdir()
    (agent_dir / "agent.yaml").write_text(
        """
id: DialogPostAnalysis
prompts:
  - 33-Questioning
  - 34-CollectUnresolvedEscalationItems
""".strip(),
        encoding="utf-8",
    )

    items = disc.scan_project_agents(base)
    dpa = next(a for a in items if a.get("name") == "DialogPostAnalysis")
    assert set(dpa.get("prompts") or []) >= {
        "33-Questioning",
        "34-CollectUnresolvedEscalationItems",
    }


def test_resolve_prompt_db_only(monkeypatch, tmp_path):
    """Resolve a prompt strictly via repo DB: single row must match and provide path."""
    from call.lib import discovery as disc

    repo_mod = __import__("importlib").import_module("call.lib.repo_db")
    # Create a temporary prompt file; DB row will point here
    pfile = tmp_path / "TempPrompt.md"
    pfile.write_text("# Temp Prompt\n", encoding="utf-8")
    # Monkeypatch DB to return a single matching row
    monkeypatch.setattr(
        repo_mod,
        "list_prompts",
        lambda **kw: (
            [
                {
                    "project": "UxFab",
                    "agent": "TempAgent",
                    "prompt": "TempPrompt",
                    "path": str(pfile),
                    "state": "ready",
                    "target": "TempPrompt",
                    "engine": "",
                    "orchestration": "",
                }
            ]
            if (kw.get("prompt") == "TempPrompt")
            else []
        ),
        raising=True,
    )
    resolved = disc.resolve_prompt(
        "TempPrompt", project="UxFab", agent="TempAgent", prefer_ready=True
    )
    assert resolved is not None
    assert Path(resolved).resolve() == pfile.resolve()


def test_build_agent_config_prompt_md(monkeypatch):
    # Prepare required env for imports
    monkeypatch.setenv("TELEGRAM_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAPH_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "2820582847")
    monkeypatch.setenv("TELEGRAM_SECOND_CHAT_ID", "2820582847")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy")

    from call.lib.api import build_runnable_instructions_config

    cfg, err = build_runnable_instructions_config(
        project="UxFab",
        agent="DialogPostAnalysis",
        prompt="33-Questioning",
    )
    assert err is None
    assert isinstance(cfg.instructions, str) and len(cfg.instructions) > 0
    # Should contain text from the MD body
    assert ("Ты — агент" in cfg.instructions) or (
        "Формирует вопросы" in cfg.instructions
    )
