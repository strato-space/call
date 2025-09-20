from call.telegram_bot.filters import parse_prompts_filters, parse_prompts_and_state


def test_parse_basic_bare_tokens():
    # Bare project token, second bare token as prompt
    proj, agent, prompt, target = parse_prompts_filters("/prompts_ready UxFab 13-*", command="/prompts_ready", default_project=None)
    assert proj == "UxFab"
    assert agent is None
    assert prompt == "13-*"
    assert target is None


def test_parse_agent_shorthand_and_kwargs():
    text = "/prompts_ready @DialogPostAnalysis project=UxFab prompt=10*"
    proj, agent, prompt, target = parse_prompts_filters(text, command="/prompts_ready", default_project=None)
    assert proj == "UxFab"
    assert agent == "DialogPostAnalysis"
    assert prompt == "10*"
    assert target is None


def test_parse_long_opts_and_target():
    text = "/prompts --project FanFab --agent Stratoslav --prompt 33-* --target r:FanFab/*"
    proj, agent, prompt, target, state = parse_prompts_and_state(text, command="/prompts", default_project=None)
    assert proj == "FanFab"
    assert agent == "Stratoslav"
    assert prompt == "33-*"
    assert target == "r:FanFab/*"
    assert state is None


def test_parse_state_variants():
    text = "/prompts state=ready project=UxFab @DialogPostAnalysis"
    proj, agent, prompt, target, state = parse_prompts_and_state(text, command="/prompts", default_project=None)
    assert proj == "UxFab"
    assert agent == "DialogPostAnalysis"
    assert state == "ready"

    text2 = "/prompts --state draft"
    proj2, agent2, prompt2, target2, state2 = parse_prompts_and_state(text2, command="/prompts", default_project="AgentFab")
    assert proj2 == "AgentFab"  # default project applied
    assert state2 == "draft"
