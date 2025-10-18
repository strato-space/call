"""Agents-as-tools runtime tests: ensure tool wrapper logging is wired up."""

import pytest
from types import SimpleNamespace
from agents.model_settings import ModelSettings
from agents.tool import FunctionTool

pytestmark = pytest.mark.anyio("asyncio")


async def test_agents_as_tools_wrapper_assigns_logging(monkeypatch):
    """Build a cfg with one sub-agent and verify on_invoke_tool is wrapped with logging."""
    from call.app import call as app_call

    # Avoid Telegram/session side effects
    monkeypatch.setattr(app_call, "TELEGRAM_CHAT_ID", None, raising=False)
    monkeypatch.setattr(app_call, "TELEGRAM_THREAD_ID", None, raising=False)
    app_call.selected_chat_id = None
    app_call.selected_thread_id = None
    app_call.force_no_session = True

    # Capture debug_print calls to verify logging
    debug_calls = []

    def capture_debug_print(*args, **kwargs):
        debug_calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(app_call, "debug_print", capture_debug_print, raising=False)
    monkeypatch.setattr(app_call, "init_bot", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(app_call, "safe_send_message", lambda **k: None, raising=False)
    monkeypatch.setattr(
        app_call, "safe_edit_message_text", lambda **k: None, raising=False
    )
    monkeypatch.setattr(
        app_call, "send_telegram_welcome_message", lambda **k: None, raising=False
    )
    monkeypatch.setattr(
        app_call, "send_digest_notification", lambda **k: None, raising=False
    )
    monkeypatch.setattr(app_call, "post_run_git_push", lambda **k: None, raising=False)
    monkeypatch.setattr(app_call, "_merge_outputs", lambda *a, **k: {})
    monkeypatch.setattr(app_call, "_extract_tg_targets", lambda merged: (None, None))

    async def fake_prepare_mcp_servers(astack):
        return [], None

    monkeypatch.setattr(
        app_call, "_prepare_mcp_servers", fake_prepare_mcp_servers, raising=False
    )

    async def fake_build_tools_for_cfg(cfg):
        return ["web"]

    monkeypatch.setattr(
        app_call, "build_tools_for_cfg", fake_build_tools_for_cfg, raising=False
    )
    monkeypatch.setattr(
        app_call,
        "_collect_tool_entries",
        lambda cfg: [("HelperAgent", "desc")],
        raising=False,
    )

    # Stub async runner + session types
    async def fake_run(agent, *args, **kwargs):
        return SimpleNamespace(final_output="ok")

    monkeypatch.setattr(app_call.Runner, "run", staticmethod(fake_run))

    class DummySession:
        def __init__(self, session_id, db_path):
            self.session_id = session_id
            self.db_path = db_path

    monkeypatch.setattr(app_call, "SQLiteSession", DummySession, raising=False)

    class DummyAgent:
        def __init__(self, *_, tools=None, **__):
            self.tools = tools or []

    monkeypatch.setattr(app_call, "Agent", DummyAgent, raising=False)

    created_tools: list[FunctionTool] = []
    orig_invocations: list[str] = []

    async def orig_invoke(ctx, input_text: str):
        orig_invocations.append(input_text)
        return "wrapped-ok"

    def fake_get_or_create_agent(*_, **__):
        class _Agent:
            def as_tool(self_inner, tool_name, tool_description):
                tool = FunctionTool(
                    name=tool_name,
                    description=tool_description,
                    params_json_schema={
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                    on_invoke_tool=orig_invoke,
                )
                created_tools.append(tool)
                return tool

        return _Agent()

    monkeypatch.setattr(
        app_call, "get_or_create_agent", fake_get_or_create_agent, raising=False
    )

    sub_cfg = SimpleNamespace(
        id="HelperAgent",
        prompt="HelperPrompt",
        instructions="",
        model="gpt-4.1-mini",
        model_settings=ModelSettings(),
        attributes={},
        tools=["FileSearchTool[foo]"],
        mcp=[],
    )

    monkeypatch.setattr(
        "call.lib.api.build_runnable_instructions_config",
        lambda **_: (sub_cfg, None),
        raising=False,
    )

    cfg = SimpleNamespace(
        id="AgentFab",
        project="AgentFab",
        instructions="",
        model="gpt-4.1-mini",
        model_settings=ModelSettings(),
        attributes={"agents": {"HelperAgent": "desc"}},
        path="agent/AgentFab/agent.md",
        tools=["WebSearchTool"],
        mcp=[],
    )

    async with app_call.build_and_run_agent(cfg, user_input="hello"):
        pass

    assert created_tools, "Expected helper tool to be built"
    wrapped_handler = created_tools[0].on_invoke_tool
    assert wrapped_handler is not orig_invoke

    # Invoke wrapper to ensure original handler still executes
    result = await wrapped_handler(None, '{"foo": "bar"}')
    assert result == "wrapped-ok"
    assert orig_invocations == ['{"foo": "bar"}']

    # Verify logging hooks were called
    # Should log: [Agent Tool][HelperAgent] Calling tool
    calling_logs = [
        c
        for c in debug_calls
        if len(c["args"]) > 0 and "[Agent Tool][HelperAgent]" in str(c["args"][0])
    ]
    assert (
        len(calling_logs) >= 1
    ), f"Expected agent tool calling log, got: {debug_calls}"

    # Should log: [Agent Tool] Input (YAML) or Input (raw)
    input_logs = [
        c
        for c in debug_calls
        if len(c["args"]) > 0 and "[Agent Tool] Input" in str(c["args"][0])
    ]
    assert len(input_logs) >= 1, "Expected agent tool input log"

    # Should log: [Agent Tool][HelperAgent] Tool returned
    result_logs = [
        c
        for c in debug_calls
        if len(c["args"]) > 0 and "Tool returned" in str(c["args"][0])
    ]
    assert len(result_logs) >= 1, "Expected agent tool result log"
