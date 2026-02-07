from __future__ import annotations

from typing import Any

from fast_agent.mcp.prompt_message_extended import PromptMessageExtended

from call.lib.history_models import HistoryMessage, normalize_role


def messages_to_fast_agent(messages: list[HistoryMessage]) -> list[PromptMessageExtended]:
    out: list[PromptMessageExtended] = []
    for msg in messages:
        out.append(
            PromptMessageExtended(
                role=msg.role,
                content=list(msg.content or []),
                tool_calls=dict(msg.tool_calls) if msg.tool_calls else None,
                tool_results=dict(msg.tool_results) if msg.tool_results else None,
                channels=dict(msg.channels) if msg.channels else None,
                stop_reason=None,
            )
        )
    return out


def fast_agent_to_messages(fa_messages: list[PromptMessageExtended]) -> list[HistoryMessage]:
    out: list[HistoryMessage] = []
    for msg in fa_messages or []:
        stop_reason: str | None = None
        try:
            if msg.stop_reason is not None:
                stop_reason = str(msg.stop_reason)
        except Exception:
            stop_reason = None

        out.append(
            HistoryMessage(
                role=normalize_role(getattr(msg, "role", "assistant")),
                content=list(getattr(msg, "content", None) or []),
                tool_calls=getattr(msg, "tool_calls", None),
                tool_results=getattr(msg, "tool_results", None),
                channels=getattr(msg, "channels", None),
                stop_reason=stop_reason,
            )
        )
    return out
