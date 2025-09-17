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
    aliases = ai.get("aliases") or []
    assert set(aliases) >= {"ai-news-aggr", "ai-news", "AI News", "AI News [aggr]", "AI News Aggregator"}


def test_scan_project_agents_prompts_from_agent_yaml():
    # DialogPostAnalysis defines prompts in its agent.yaml
    from call.lib import discovery as disc
    base = Path("c:/home/strato-space/agent/UxFab")
    items = disc.scan_project_agents(base)
    dpa = next(a for a in items if a.get("name") == "DialogPostAnalysis")
    assert set(dpa.get("prompts") or []) >= {"33-Questioning", "34-CollectUnresolvedEscalationItems"}


def test_resolve_prompt_agent_folder_fallback(tmp_path, monkeypatch):
    """If prompt repo lacks the file but agent folder has it, resolve_prompt should return agent folder file."""
    from call.lib import discovery as disc

    # Create fake prompt repo with no target prompt
    prom = tmp_path / "prompt"
    (prom / "draft").mkdir(parents=True)
    (prom / "ready").mkdir(parents=True)

    # Create fake agent repo with UxFab/TempAgent/TempPrompt.md
    arepo = tmp_path / "agent"
    adir = arepo / "UxFab" / "TempAgent"
    adir.mkdir(parents=True)
    pfile = adir / "TempPrompt.md"
    pfile.write_text("# Temp Prompt\n", encoding="utf-8")

    # Monkeypatch discovery roots
    monkeypatch.setenv("PROMPT_REPO", str(prom))
    monkeypatch.setenv("AGENT_REPO", str(arepo))

    # Also ensure projects list contains UxFab
    monkeypatch.setattr(disc, "load_projects_index", lambda repo=None: ["UxFab"])  # type: ignore[attr-defined]

    resolved = disc.resolve_prompt("TempPrompt", project="UxFab", agent="TempAgent", prefer_ready=True)
    assert resolved is not None
    assert Path(resolved).resolve() == pfile.resolve()


def test_build_agent_config_merge_prompt_md(monkeypatch):
    # Prepare required env for call.app.call imports
    monkeypatch.setenv("TELEGRAM_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAPH_TOKEN", "dummy")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "2820582847")
    monkeypatch.setenv("TELEGRAM_SECOND_CHAT_ID", "2820582847")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy")

    async def _go():
        from call.app.call import build_agent_config
        cfg = await build_agent_config(
            agent_name="DialogPostAnalysis",
            prompt_override="33-Questioning",
            project_name="UxFab",
            merge=True,
        )
        return cfg

    cfg = asyncio.run(_go())
    assert isinstance(cfg.instructions, str) and len(cfg.instructions) > 0
    # Should contain text from the MD body
    assert "Формирует вопросы" in cfg.instructions
