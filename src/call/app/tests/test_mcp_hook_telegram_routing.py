from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mcp.types import CallToolResult, TextContent


@pytest.mark.asyncio
async def test_mcp_hook_no_debug_chat_never_falls_back_to_origin(monkeypatch):
    from call.app import call as app_call
    from call.app.call import MCPServerStdioHook

    monkeypatch.setenv("CALL_DEBUG_TELEGRAM", "1")
    app_call.selected_chat_id = 999
    app_call.selected_thread_id = 111
    app_call.debug_chat_id = None
    app_call.debug_thread_id = None

    with patch("call.app.call.MCPServerStdio.__init__", return_value=None):
        hook = MCPServerStdioHook(
            params={"command": "test", "args": []},
            name="test",
            client_session_timeout_seconds=120,
        )
        hook._mcp_title = "test"
        hook._MCPServerStdioHook__last_tg_text = None
        hook._MCPServerStdioHook__telegram_last_message = None
        hook._MCPServerStdioHook__service_message_ids = []

        async def mock_parent_call_tool(self, tool_name, arguments):
            return CallToolResult(content=[TextContent(type="text", text="ok")])

        with patch.object(hook.__class__.__bases__[0], "call_tool", mock_parent_call_tool):
            with patch("call.app.call.safe_send_message", new=AsyncMock()) as send_mock:
                with patch("call.app.call.safe_edit_message_text", new=AsyncMock()) as edit_mock:
                    await hook.call_tool("test_tool", {})

        send_mock.assert_not_awaited()
        edit_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_hook_debug_disabled_sends_nothing(monkeypatch):
    from call.app import call as app_call
    from call.app.call import MCPServerStdioHook

    monkeypatch.setenv("CALL_DEBUG_TELEGRAM", "0")
    app_call.selected_chat_id = 999
    app_call.selected_thread_id = 111
    app_call.debug_chat_id = 123
    app_call.debug_thread_id = 456

    with patch("call.app.call.MCPServerStdio.__init__", return_value=None):
        hook = MCPServerStdioHook(
            params={"command": "test", "args": []},
            name="test",
            client_session_timeout_seconds=120,
        )
        hook._mcp_title = "test"
        hook._MCPServerStdioHook__last_tg_text = None
        hook._MCPServerStdioHook__telegram_last_message = None
        hook._MCPServerStdioHook__service_message_ids = []

        async def mock_parent_call_tool(self, tool_name, arguments):
            return CallToolResult(content=[TextContent(type="text", text="ok")])

        with patch.object(hook.__class__.__bases__[0], "call_tool", mock_parent_call_tool):
            with patch("call.app.call.safe_send_message", new=AsyncMock()) as send_mock:
                with patch("call.app.call.safe_edit_message_text", new=AsyncMock()) as edit_mock:
                    await hook.call_tool("test_tool", {})

        send_mock.assert_not_awaited()
        edit_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_hook_debug_enabled_targets_debug_chat(monkeypatch):
    from call.app import call as app_call
    from call.app.call import MCPServerStdioHook

    monkeypatch.setenv("CALL_DEBUG_TELEGRAM", "1")
    app_call.selected_chat_id = 999
    app_call.selected_thread_id = 111
    app_call.debug_chat_id = 123
    app_call.debug_thread_id = 456

    fake_msg = SimpleNamespace(chat_id=123, message_id=1, message_thread_id=456)

    with patch("call.app.call.MCPServerStdio.__init__", return_value=None):
        hook = MCPServerStdioHook(
            params={"command": "test", "args": []},
            name="test",
            client_session_timeout_seconds=120,
        )
        hook._mcp_title = "test"
        hook._MCPServerStdioHook__last_tg_text = None
        hook._MCPServerStdioHook__telegram_last_message = None
        hook._MCPServerStdioHook__service_message_ids = []

        async def mock_parent_call_tool(self, tool_name, arguments):
            return CallToolResult(content=[TextContent(type="text", text="ok")])

        with patch.object(hook.__class__.__bases__[0], "call_tool", mock_parent_call_tool):
            send_mock = AsyncMock(return_value=fake_msg)
            edit_mock = AsyncMock(return_value=True)
            with patch("call.app.call.safe_send_message", new=send_mock):
                with patch("call.app.call.safe_edit_message_text", new=edit_mock):
                    await hook.call_tool("test_tool", {})

        assert send_mock.await_count >= 1
        assert send_mock.await_args.kwargs.get("chat_id") == 123
