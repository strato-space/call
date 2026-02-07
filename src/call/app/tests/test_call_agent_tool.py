import importlib
import types

import pytest


pytestmark = pytest.mark.anyio("asyncio")


async def test_call_agent_dispatches_and_seeds_history(monkeypatch):
    ctx = importlib.import_module("call.engine.context")
    tool_mod = importlib.import_module("call.engine.call_agent_tool")
    runtime = importlib.import_module("call.app.runtime")
    api = importlib.import_module("call.lib.api")
    history_db = importlib.import_module("call.lib.history_db")
    card_models = importlib.import_module("call.lib.card_models")

    import mcp.types as mcp_types
    from call.lib.history_models import HistoryMessage

    seed = [
        HistoryMessage(role="assistant", content=[mcp_types.TextContent(type="text", text="seed")])
    ]

    tok_conv = ctx.CALL_CONVERSATION_ID.set("conv1")
    tok_seed = ctx.CALL_CALLER_SEED_HISTORY.set(seed)
    tok_depth = ctx.CALL_AGENT_DEPTH.set(0)
    try:
        cfg = types.SimpleNamespace(
            id="SubAgent",
            type="agent",
            project=None,
            agent="SubAgent",
            prompt=None,
            path="agent/Demo/SubAgent/agent.md",
            url=None,
            goal=None,
            instructions="hi",
            model="gpt-5",
            attributes={"id": "SubAgent"},
            mcp=[],
            tools=[],
        )

        monkeypatch.setattr(api, "build_runnable_instructions_config", lambda **_: (cfg, None), raising=True)

        monkeypatch.setattr(
            runtime,
            "build_call_card_from_cfg",
            lambda _cfg: card_models.CallCard(agent_name="SubAgent", engine="fast-agent", instructions="hi"),
            raising=True,
        )
        monkeypatch.setattr(history_db, "load_history", lambda *_: [], raising=True)

        captured = {}

        async def fake_run_cfg_turn(*, cfg, conversation_id, input_text, history_override=None):
            captured["conversation_id"] = conversation_id
            captured["input_text"] = input_text
            captured["history_override"] = history_override
            return types.SimpleNamespace(output_text="ok")

        monkeypatch.setattr(runtime, "run_cfg_turn", fake_run_cfg_turn, raising=True)

        out = await tool_mod.call_agent(agent="SubAgent", message="hello", share_history=True)

        assert out == "ok"
        assert captured["conversation_id"] == "conv1"
        assert captured["input_text"] == "hello"
        assert captured["history_override"] == seed
    finally:
        ctx.CALL_AGENT_DEPTH.reset(tok_depth)
        ctx.CALL_CALLER_SEED_HISTORY.reset(tok_seed)
        ctx.CALL_CONVERSATION_ID.reset(tok_conv)


async def test_call_agent_recursion_guard(monkeypatch):
    ctx = importlib.import_module("call.engine.context")
    tool_mod = importlib.import_module("call.engine.call_agent_tool")

    tok_conv = ctx.CALL_CONVERSATION_ID.set("conv1")
    tok_depth = ctx.CALL_AGENT_DEPTH.set(3)
    try:
        out = await tool_mod.call_agent(agent="SubAgent", message="hello", share_history=False)
        assert "recursion limit" in out
    finally:
        ctx.CALL_AGENT_DEPTH.reset(tok_depth)
        ctx.CALL_CONVERSATION_ID.reset(tok_conv)

