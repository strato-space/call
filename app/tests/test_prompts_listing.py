import os
from pathlib import Path

import pytest

from call.lib.discovery import discover_prompt_repo, resolve_prompt, prompts, iter_prompts


def test_discover_repo_exists():
    repo = discover_prompt_repo()
    assert repo.exists()
    assert (repo / 'draft').exists() or (repo / 'ready').exists()


def test_iter_prompts_and_metadata_reads():
    # Ensure we can iterate and basic keys exist
    items = list(iter_prompts())
    assert isinstance(items, list) or isinstance(items, list)  # generator-like turned list
    # If there are no prompts at all, this repo is misconfigured for tests
    assert len(items) >= 1
    for it in items[:10]:
        assert 'prompt_id' in it
        assert 'name' in it
        assert 'state' in it
        assert it['state'] in ('draft', 'ready')
        # URL may be None if env is missing; path should exist
        p = Path(it['path'])
        assert p.exists(), f"missing file for prompt: {it}"


def test_prompts_filtering_by_state_and_agent():
    # Pick any item as a reference and test filters match at least that item
    items = prompts()
    assert items, "no prompts found"
    ref = items[0]
    # Filter by state
    by_state = prompts(state=ref['state'])
    assert any(x['path'] == ref['path'] for x in by_state)
    # Filter by agent (may be None in some prompts)
    if ref.get('agent'):
        by_agent = prompts(agent=ref['agent'])
        assert any(x['path'] == ref['path'] for x in by_agent)


def test_resolve_prompt_prefers_basename():
    # Try to resolve a known prompt by basename; choose the first item
    items = prompts()
    ref = items[0]
    name = Path(ref['path']).stem
    # If name contains '--Agent', strip suffix to get basename
    base = name.split('--', 1)[0]
    p = resolve_prompt(base)
    assert isinstance(p, Path)
    assert p.exists()
