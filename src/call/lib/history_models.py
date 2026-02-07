from __future__ import annotations

from typing import Any, Dict, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

import mcp.types as mcp_types


ContentBlockAdapter = TypeAdapter(mcp_types.ContentBlock)


def normalize_role(value: str | None) -> Literal["user", "assistant"]:
    """Normalize role strings to the minimal call-native set."""
    role = str(value or "").strip().lower()
    if role == "user":
        return "user"
    # Collapse everything else into assistant for now (system/dev/tool/etc).
    return "assistant"


def coerce_content_block(value: Any) -> mcp_types.ContentBlock:
    """Coerce arbitrary JSON-ish values into an MCP ContentBlock.

    This is intentionally tolerant so stored history can evolve without breaking
    callers when shapes change.
    """
    if isinstance(
        value,
        (
            mcp_types.TextContent,
            mcp_types.ImageContent,
            mcp_types.AudioContent,
            mcp_types.ResourceLink,
            mcp_types.EmbeddedResource,
        ),
    ):
        return value
    if isinstance(value, str):
        return mcp_types.TextContent(type="text", text=value)
    try:
        return ContentBlockAdapter.validate_python(value)
    except Exception:
        return mcp_types.TextContent(type="text", text=f"[unsupported-content:{type(value).__name__}]")


class HistoryMessage(BaseModel):
    """Call-owned history message.

    Storage format is engine-agnostic and uses MCP ContentBlocks to represent
    multimodal content + tool IO.
    """

    model_config = ConfigDict(extra="allow")

    role: Literal["user", "assistant"]
    content: list[mcp_types.ContentBlock] = Field(default_factory=list)
    tool_calls: Dict[str, mcp_types.CallToolRequest] | None = None
    tool_results: Dict[str, mcp_types.CallToolResult] | None = None
    channels: Dict[str, list[mcp_types.ContentBlock]] | None = None
    stop_reason: str | None = None

