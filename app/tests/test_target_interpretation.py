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


def test_interpret_target_prompt_global_fallback_warn(caplog):
    # When project scope doesn't contain the prompt, the resolver should use global fallback
    # and emit a WARN log via the standard logger 'call.api'.
    from call.lib.logging import configure_logging
    configure_logging()
    caplog.set_level("WARNING", logger="call.api")
    pj, ag, pr, err = interpret_target(project="__NoSuchProject__", agent=None, prompt=None, target="33-Questioning")
    assert err is None
    assert pr.lower().startswith("33-questioning"), pr
    # Ensure a warning about global resolution was emitted
    assert any("outside project scope" in (rec.message or "") for rec in caplog.records)
