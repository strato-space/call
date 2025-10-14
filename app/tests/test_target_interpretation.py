import json
import os
import pytest

from call.lib import repo_db as repo_db_module
from call.lib.api import interpret_target


def test_interpret_target_prompt_ambiguous_returns_toomanyrows():
    # '*' should match many prompts and produce an ambiguity error
    with pytest.raises(repo_db_module.TooManyRowsError):
        interpret_target(project=None, agent=None, prompt=None, target="*")


def test_interpret_target_project_exact():
    # UxFab is a known project in this workspace
    row = interpret_target(project=None, agent=None, prompt=None, target="UxFab")
    assert row.project == "UxFab"
    assert row.type == "project"


def test_interpret_target_prompt_single_known():
    # With project filter, resolving a known prompt should be unambiguous
    row = interpret_target(
        project="UxFab", agent=None, prompt=None, target="33-Questioning"
    )
    assert row.prompt == "33-Questioning"
    assert row.type == "prompt"


def test_interpret_target_with_nonexistent_project():
    # A missing project should raise a not-found error
    with pytest.raises(repo_db_module.SelectionNotFoundError):
        interpret_target(
            project="__NoSuchProject__",
            agent=None,
            prompt=None,
            target=None,
        )
