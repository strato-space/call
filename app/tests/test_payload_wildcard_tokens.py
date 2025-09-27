"""Validate wildcard token handling when building input payloads for AgentFab tools."""

import json
import pytest

pytestmark = pytest.mark.anyio("asyncio")


def test_build_input_payload_wildcard_single(monkeypatch):
    from call.lib import api as call_api

    # Simulate DB prompts with a single match for '31-*'
    def fake_list_prompts(*, project=None, agent=None, prompt=None, state=None, target=None):
        # When wildcard used, our builder passes prompt=None and filters locally
        items = [
            {"prompt": "31-OnlineQuestionsBabook", "rel_path": "prompt/draft/31-OnlineQuestionsBabook.md", "path": "prompt/draft/31-OnlineQuestionsBabook.md"},
            {"prompt": "32-InterviewSummary", "rel_path": "prompt/draft/32-InterviewSummary.md", "path": "prompt/draft/32-InterviewSummary.md"},
        ]
        if prompt:
            # Direct match path for non-wildcard
            return [it for it in items if it["prompt"] == prompt]
        return items

    monkeypatch.setattr(call_api, "list_prompts", fake_list_prompts, raising=True)

    payload_json, payload_dict = call_api.build_input_payload(target="AgentFab", main_text="@31-*")
    assert isinstance(payload_json, str)
    obj = json.loads(payload_json)
    assert obj["target"] == "AgentFab"
    assert obj["input"] == "@31-*"
    # Context should be present with the matched prompt
    ctx = obj.get("context")
    assert isinstance(ctx, list) and len(ctx) == 1
    ref = ctx[0]
    assert ref["type"] == "prompt"
    assert ref["id"] == "31-OnlineQuestionsBabook"
    assert ref["path"].replace("\\", "/") == "prompt/draft/31-OnlineQuestionsBabook.md"
    assert ref.get("mutable") is True


def test_build_input_payload_multiple_wildcards(monkeypatch):
    from call.lib import api as call_api

    # Simulate DB prompts with two independent wildcard hits: '31-*' and '32-*'
    def fake_list_prompts(*, project=None, agent=None, prompt=None, state=None, target=None):
        items = [
            {"prompt": "31-OnlineQuestionsBabook", "rel_path": "prompt/draft/31-OnlineQuestionsBabook.md", "path": "prompt/draft/31-OnlineQuestionsBabook.md"},
            {"prompt": "32-InterviewSummary", "rel_path": "prompt/draft/32-InterviewSummary.md", "path": "prompt/draft/32-InterviewSummary.md"},
        ]
        if prompt:
            return [it for it in items if it["prompt"] == prompt]
        return items

    monkeypatch.setattr(call_api, "list_prompts", fake_list_prompts, raising=True)

    payload_json, payload_dict = call_api.build_input_payload(target="AgentFab", main_text="31-* 32-*")
    obj = json.loads(payload_json)
    ctx = obj.get("context")
    # Expect two refs, stable order by first match order in items
    assert isinstance(ctx, list) and len(ctx) == 2
    ids = [r["id"] for r in ctx]
    assert ids == ["31-OnlineQuestionsBabook", "32-InterviewSummary"]
    paths = [r["path"].replace("\\", "/") for r in ctx]
    assert paths == ["prompt/draft/31-OnlineQuestionsBabook.md", "prompt/draft/32-InterviewSummary.md"]
    types = [r["type"] for r in ctx]
    assert types == ["prompt", "prompt"]


def test_build_input_payload_wildcard_no_matches(monkeypatch):
    from call.lib import api as call_api

    def fake_list_prompts(*, project=None, agent=None, prompt=None, state=None, target=None):
        # No items in DB
        return []

    monkeypatch.setattr(call_api, "list_prompts", fake_list_prompts, raising=True)

    payload_json, payload_dict = call_api.build_input_payload(target="AgentFab", main_text="@99-*")
    obj = json.loads(payload_json)
    assert obj["target"] == "AgentFab"
    # No context array when nothing matched
    assert obj.get("context") is None or obj.get("context") == []


def test_build_input_payload_deduplicate_refs(monkeypatch):
    from call.lib import api as call_api

    items = [
        {"prompt": "31-OnlineQuestionsBabook", "rel_path": "prompt/draft/31-OnlineQuestionsBabook.md", "path": "prompt/draft/31-OnlineQuestionsBabook.md"},
    ]

    def fake_list_prompts(*, project=None, agent=None, prompt=None, state=None, target=None):
        if prompt:
            return [it for it in items if it["prompt"] == prompt]
        return items

    # Provide both exact and wildcard that resolve to the same item
    payload_json, _ = call_api.build_input_payload(target="AgentFab", main_text="31-OnlineQuestionsBabook 31-*")
    obj = json.loads(payload_json)
    ctx = obj.get("context")
    assert isinstance(ctx, list) and len(ctx) == 1
    assert ctx[0]["id"] == "31-OnlineQuestionsBabook"


def test_build_input_payload_markdown_suffix_stripping(monkeypatch):
    from call.lib import api as call_api

    def fake_list_prompts(*, project=None, agent=None, prompt=None, state=None, target=None):
        items = [
            {"prompt": "32-InterviewSummary", "rel_path": "prompt/draft/32-InterviewSummary.md", "path": "prompt/draft/32-InterviewSummary.md"},
        ]
        if prompt:
            return [it for it in items if it["prompt"] == prompt]
        return items

    monkeypatch.setattr(call_api, "list_prompts", fake_list_prompts, raising=True)

    # Token includes .markdown suffix and leading '@'
    payload_json, _ = call_api.build_input_payload(target="AgentFab", main_text="@32-InterviewSummary.markdown")
    obj = json.loads(payload_json)
    ctx = obj.get("context")
    assert isinstance(ctx, list) and len(ctx) == 1
    assert ctx[0]["id"] == "32-InterviewSummary"


def test_cli_prompt_wildcard_context(monkeypatch):
    from call.lib import api as call_api

    def fake_list_prompts(*, project=None, agent=None, prompt=None, state=None, target=None):
        items = [
            {
                "project": "AgentFab",
                "agent": "DiscoveryAgent",
                "prompt": "33-extensions",
                "path": "prompt/ready/33-extensions.md",
                "rel_path": "prompt/ready/33-extensions.md",
                "url": "https://github.com/strato-space/prompt/blob/master/ready/33-extensions.md",
                "type": "prompt",
            },
            {
                "project": "AgentFab",
                "agent": "DiscoveryAgent",
                "prompt": "33-Questioning",
                "path": "prompt/draft/33-Questioning.md",
                "rel_path": "prompt/draft/33-Questioning.md",
                "url": "https://github.com/strato-space/prompt/blob/master/draft/33-Questioning.md",
                "type": "prompt",
            },
        ]
        if prompt:
            return [it for it in items if it["prompt"] == prompt]
        return items

    monkeypatch.setattr(call_api, "list_prompts", fake_list_prompts, raising=True)
    monkeypatch.setattr(call_api.call_repo, "find_projects", lambda **_: [], raising=False)
    monkeypatch.setattr(call_api.call_repo, "find_agents", lambda **_: [], raising=False)

    payload_json, _ = call_api.build_input_payload(target=None, main_text="@33-*")
    obj = json.loads(payload_json)
    ctx = obj.get("context")
    assert isinstance(ctx, list) and len(ctx) == 2
    ids = [c["id"] for c in ctx]
    assert ids == ["33-extensions", "33-Questioning"]
    urls = [c.get("url") for c in ctx]
    assert urls == [
        "https://github.com/strato-space/prompt/blob/master/ready/33-extensions.md",
        "https://github.com/strato-space/prompt/blob/master/draft/33-Questioning.md",
    ]
    types = [c["type"] for c in ctx]
    assert types == ["prompt", "prompt"]


def test_cli_agent_exact_context(monkeypatch):
    from call.lib import api as call_api

    def fake_find_agents(*, project=None, agent=None, target=None):
        return [
            {
                "project": "AgentFab",
                "agent": "DiscoveryAgent",
                "path": "prompt/AgentFab/DiscoveryAgent/agent.md",
                "rel_path": "prompt/AgentFab/DiscoveryAgent/agent.md",
                "url": "https://github.com/strato-space/prompt/blob/master/AgentFab/DiscoveryAgent/agent.md",
                "type": "agent",
            }
        ]

    monkeypatch.setattr(call_api.call_repo, "find_agents", fake_find_agents, raising=True)
    monkeypatch.setattr(call_api.call_repo, "find_projects", lambda **_: [], raising=False)
    monkeypatch.setattr(call_api, "list_prompts", lambda **_: [], raising=False)

    payload_json, _ = call_api.build_input_payload(target=None, main_text="@DiscoveryAgent")
    obj = json.loads(payload_json)
    ctx = obj.get("context")
    assert isinstance(ctx, list) and len(ctx) == 1
    ref = ctx[0]
    assert ref["id"] == "DiscoveryAgent"
    assert ref["type"] == "agent"
    assert ref["path"].replace("\\", "/") == "prompt/AgentFab/DiscoveryAgent/agent.md"
    assert ref["url"].startswith("https://github.com/strato-space/prompt/blob/master/AgentFab/DiscoveryAgent/agent.md")


def test_cli_project_exact_context(monkeypatch):
    from call.lib import api as call_api

    def fake_find_projects(*, project=None, target=None):
        return [
            {
                "project": "AgentFab",
                "rel_path": "prompt/AgentFab/project.md",
                "url": "https://github.com/strato-space/prompt/blob/master/AgentFab/project.md",
                "type": "project",
            }
        ]

    monkeypatch.setattr(call_api.call_repo, "find_projects", fake_find_projects, raising=True)
    monkeypatch.setattr(call_api.call_repo, "find_agents", lambda **_: [], raising=False)
    monkeypatch.setattr(call_api, "list_prompts", lambda **_: [], raising=False)

    payload_json, _ = call_api.build_input_payload(target=None, main_text="@AgentFab")
    obj = json.loads(payload_json)
    ctx = obj.get("context")
    assert isinstance(ctx, list) and len(ctx) == 1
    ref = ctx[0]
    assert ref["id"] == "AgentFab"
    assert ref["type"] == "project"
    assert ref["path"].replace("\\", "/") == "prompt/AgentFab/project.md"
    assert ref["url"].startswith("https://github.com/strato-space/prompt/blob/master/AgentFab/project.md")
