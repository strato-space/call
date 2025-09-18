import os
import pytest

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
