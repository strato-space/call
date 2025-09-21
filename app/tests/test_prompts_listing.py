import os
from pathlib import Path

import pytest

from call.lib.discovery import discover_prompt_repo, resolve_prompt, prompts, iter_prompts


def test_discover_repo_exists():
    repo = discover_prompt_repo()
    assert repo.exists()
    assert (repo / 'draft').exists() or (repo / 'ready').exists()


def test_iter_prompts_and_metadata_reads(tmp_path, monkeypatch):
    # Provide DB rows via monkeypatch
    from importlib import import_module
    repo_mod = import_module('call.lib.repo')
    p1 = tmp_path / 'one.md'; p1.write_text('# one', encoding='utf-8')
    p2 = tmp_path / 'two.md'; p2.write_text('# two', encoding='utf-8')
    monkeypatch.setattr(
        repo_mod,
        'list_prompts',
        lambda **kw: [
            {"project": "FanFab", "agent": "Stratoslav", "prompt": "StratoSammary", "path": str(p1), "state": "ready", "target": "StratoSammary", "engine": "", "orchestration": ""},
            {"project": "FanFab", "agent": "Stratoslav", "prompt": "130-QAcriteriaDefinition", "path": str(p2), "state": "ready", "target": "130-QAcriteriaDefinition", "engine": "", "orchestration": ""},
        ],
        raising=True,
    )
    items = list(iter_prompts())
    assert isinstance(items, list)
    assert len(items) >= 1
    for it in items[:10]:
        assert 'prompt_id' in it
        assert 'name' in it
        assert 'state' in it
        assert it['state'] in ('draft', 'ready')
        p = Path(it['path'])
        assert p.exists(), f"missing file for prompt: {it}"


def test_prompts_filtering_by_state_and_agent(tmp_path, monkeypatch):
    from importlib import import_module
    repo_mod = import_module('call.lib.repo')
    p = tmp_path / 'file.md'; p.write_text('# x', encoding='utf-8')
    rows = [
        {"project": "FanFab", "agent": "Stratoslav", "prompt": "StratoSammary", "path": str(p), "state": "ready", "target": "StratoSammary", "engine": "", "orchestration": ""},
    ]
    monkeypatch.setattr(repo_mod, 'list_prompts', lambda **kw: rows if ((kw.get('state') in (None, 'ready')) and (kw.get('agent') in (None, 'Stratoslav'))) else [], raising=True)
    items = prompts()
    assert items, "no prompts found"
    ref = items[0]
    by_state = prompts(state=ref['state'])
    assert any(x['path'] == ref['path'] for x in by_state)
    if ref.get('agent'):
        by_agent = prompts(agent=ref['agent'])
        assert any(x['path'] == ref['path'] for x in by_agent)


def test_resolve_prompt_prefers_basename(tmp_path, monkeypatch):
    # With DB-only resolution we select by prompt id
    from importlib import import_module
    repo_mod = import_module('call.lib.repo')
    f = tmp_path / '33-Questioning.md'; f.write_text('# q', encoding='utf-8')
    monkeypatch.setattr(repo_mod, 'list_prompts', lambda **kw: [
        {"project": "UxFab", "agent": "DialogPostAnalysis", "prompt": "33-Questioning", "path": str(f), "state": "ready", "target": "33-Questioning", "engine": "", "orchestration": ""}
    ] if ((kw.get('prompt') in ('33-Questioning', None))) else [], raising=True)
    p = resolve_prompt('33-Questioning')
    assert isinstance(p, Path) and p.exists()
