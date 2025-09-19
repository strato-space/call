import json
import os
import pytest

from call.lib.api import interpret_target


def test_interpret_target_prompt_ambiguous_returns_toomanyrows():
    # '*' should match many prompts and produce an ambiguity error
    pj, ag, pr, err = interpret_target(project=None, agent=None, prompt=None, target="*")
    assert err is not None
    assert err.get("code") == "TOO_MANY_ROWS"
    assert isinstance(err.get("options"), list)


def test_interpret_target_project_exact():
    # UxFab is a known project in this workspace
    pj, ag, pr, err = interpret_target(project=None, agent=None, prompt=None, target="UxFab")
    assert err is None
    assert pj == "UxFab"


def test_interpret_target_prompt_single_known():
    # With project filter, resolving a known prompt should be unambiguous
    pj, ag, pr, err = interpret_target(project="UxFab", agent=None, prompt=None, target="33-Questioning")
    assert err is None
    assert pr in ("33-Questioning", "33-Questioning"), pr


def test_interpret_target_with_nonexistent_project():
    # When given a non-existent project, interpret_target should still return
    # the project name without error (validation happens later)
    pj, ag, pr, err = interpret_target(
        project="__NoSuchProject__", 
        agent=None, 
        prompt=None, 
        target=None
    )
    assert err is None
    assert pj == "__NoSuchProject__"
    assert ag is None
    assert pr is None
