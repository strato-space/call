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
    assert ref["type"] == "file"
    assert ref["name"] == "31-OnlineQuestionsBabook"
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
    names = [r["name"] for r in ctx]
    assert names == ["31-OnlineQuestionsBabook", "32-InterviewSummary"]
    paths = [r["path"].replace("\\", "/") for r in ctx]
    assert paths == ["prompt/draft/31-OnlineQuestionsBabook.md", "prompt/draft/32-InterviewSummary.md"]


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

    monkeypatch.setattr(call_api, "list_prompts", fake_list_prompts, raising=True)

    # Provide both exact and wildcard that resolve to the same item
    payload_json, _ = call_api.build_input_payload(target="AgentFab", main_text="31-OnlineQuestionsBabook 31-*")
    obj = json.loads(payload_json)
    ctx = obj.get("context")
    # Expect only one entry
    assert isinstance(ctx, list) and len(ctx) == 1
    assert ctx[0]["name"] == "31-OnlineQuestionsBabook"


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
    assert ctx[0]["name"] == "32-InterviewSummary"
