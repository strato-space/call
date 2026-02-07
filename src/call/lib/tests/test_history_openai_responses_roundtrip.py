import mcp.types as mcp_types

from call.lib.history_models import HistoryMessage
from call.lib.history_openai_responses import (
    messages_to_response_items,
    response_items_to_messages,
)


def test_openai_responses_items_roundtrip_stability():
    messages = [
        HistoryMessage(
            role="user",
            content=[mcp_types.TextContent(type="text", text="hi")],
        ),
        HistoryMessage(
            role="assistant",
            content=[mcp_types.TextContent(type="text", text="calling tool")],
            tool_calls={
                "call_1": mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(
                        name="time__get_current_time", arguments={"timezone": "UTC"}
                    ),
                )
            },
        ),
        HistoryMessage(
            role="assistant",
            content=[],
            tool_results={
                "call_1": mcp_types.CallToolResult(
                    content=[mcp_types.TextContent(type="text", text="{\"ok\": true}")],
                    isError=False,
                )
            },
        ),
    ]

    items = messages_to_response_items(messages)
    reparsed = response_items_to_messages(items)
    items_roundtrip = messages_to_response_items(reparsed)

    assert items_roundtrip == items

