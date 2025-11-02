"""Test that MCPServerStdioHook preserves data integrity in pipeline."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mcp.types import CallToolResult, TextContent


@pytest.mark.asyncio
async def test_mcp_hook_preserves_large_data():
    """Test that MCPServerStdioHook returns full data to agent, not truncated."""
    # Import after pytest fixtures
    from call.app.call import MCPServerStdioHook
    
    # Create large test data (10KB)
    large_data = {"items": [{"id": i, "data": "x" * 100} for i in range(100)]}
    large_json = json.dumps(large_data, ensure_ascii=False)
    assert len(large_json) > 10000, "Test data should be > 10KB"
    
    # Mock CallToolResult that would be returned by real MCP tool
    mock_result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=large_json
            )
        ]
    )
    
    # Create hook instance with mocked parent
    with patch('call.app.call.MCPServerStdio.__init__', return_value=None):
        hook = MCPServerStdioHook(
            params={"command": "test", "args": []},
            name="test",
            client_session_timeout_seconds=120
        )
        hook._mcp_title = "test"
        hook._MCPServerStdioHook__last_tg_text = None
        hook._MCPServerStdioHook__telegram_last_message = None
        hook._MCPServerStdioHook__service_message_ids = []
        
        # Mock the parent call_tool to return our large data
        async def mock_parent_call_tool(self, tool_name, arguments):
            return mock_result
        
        with patch.object(hook.__class__.__bases__[0], 'call_tool', mock_parent_call_tool):
            with patch.object(hook, '_MCPServerStdioHook__edit_message_text', AsyncMock()):
                # Call the hook
                result = await hook.call_tool("test_tool", {})
    
    # Verify result is exactly the original (not wrapped, not truncated)
    assert result == mock_result, "Result should be returned as-is"
    assert result.content[0].text == large_json, "Data should not be truncated"
    assert len(result.content[0].text) > 10000, "Full 10KB+ data should be preserved"


@pytest.mark.asyncio
async def test_mcp_hook_display_truncation_doesnt_affect_pipeline():
    """Test that display truncation (for Telegram) doesn't affect pipeline data."""
    from call.app.call import MCPServerStdioHook
    
    # Create data that will be truncated for display (> 4000 chars)
    large_array = [{"id": i, "value": "data" * 50} for i in range(50)]
    large_json = json.dumps(large_array, ensure_ascii=False)
    assert len(large_json) > 4000, "Test data should exceed display limit"
    
    mock_result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=large_json
            )
        ]
    )
    
    with patch('call.app.call.MCPServerStdio.__init__', return_value=None):
        hook = MCPServerStdioHook(
            params={"command": "test", "args": []},
            name="test",
            client_session_timeout_seconds=120
        )
        hook._mcp_title = "test"
        hook._MCPServerStdioHook__last_tg_text = None
        hook._MCPServerStdioHook__telegram_last_message = None
        hook._MCPServerStdioHook__service_message_ids = []
        
        display_text_sent = None
        
        async def mock_edit_message(text):
            nonlocal display_text_sent
            display_text_sent = text
        
        async def mock_parent_call_tool(self, tool_name, arguments):
            return mock_result
        
        with patch.object(hook.__class__.__bases__[0], 'call_tool', mock_parent_call_tool):
            with patch.object(hook, '_MCPServerStdioHook__edit_message_text', mock_edit_message):
                with patch.dict('os.environ', {}, clear=True):  # Clear DEBUG_MODE to enable truncation
                    result = await hook.call_tool("test_tool", {})
    
    # Verify display was truncated
    assert display_text_sent is not None, "Display message should be sent"
    assert len(display_text_sent) < len(large_json), "Display should be truncated"
    
    # But pipeline data is NOT truncated
    assert result.content[0].text == large_json, "Pipeline data should be full"
    assert len(result.content[0].text) > 4000, "Pipeline should have full data"


@pytest.mark.asyncio  
async def test_mcp_hook_json_array_integrity():
    """Test specific case: 21-element JSON array from routing-prod.json."""
    from call.app.call import MCPServerStdioHook
    
    # Simulate routing-prod.json with 21 elements
    templates = [
        {
            "type": "routing-item-template",
            "topic": f"Topic {i}",
            "sources": [{"chat": {"chat_id": str(1000000 + i), "name": f"Chat {i}"}}],
            "output": [{"tgbot:send_message": {"chat_id": "3100424032", "thread_id": i}}]
        }
        for i in range(21)
    ]
    json_data = json.dumps(templates, ensure_ascii=False, indent=2)
    
    mock_result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json_data
            )
        ]
    )
    
    with patch('call.app.call.MCPServerStdio.__init__', return_value=None):
        hook = MCPServerStdioHook(
            params={"command": "test", "args": []},
            name="fs",
            client_session_timeout_seconds=120
        )
        hook._mcp_title = "fs"
        hook._MCPServerStdioHook__last_tg_text = None
        hook._MCPServerStdioHook__telegram_last_message = None
        hook._MCPServerStdioHook__service_message_ids = []
        
        async def mock_parent_call_tool(self, tool_name, arguments):
            return mock_result
        
        with patch.object(hook.__class__.__bases__[0], 'call_tool', mock_parent_call_tool):
            with patch.object(hook, '_MCPServerStdioHook__edit_message_text', AsyncMock()):
                result = await hook.call_tool("read_text_file", {"path": "/test.json"})
    
    # Parse result to verify all 21 elements
    result_text = result.content[0].text
    parsed = json.loads(result_text)
    
    assert len(parsed) == 21, "All 21 templates must be preserved"
    assert parsed[0]["topic"] == "Topic 0", "First element correct"
    assert parsed[20]["topic"] == "Topic 20", "Last element correct"
