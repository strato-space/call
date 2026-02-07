from __future__ import annotations

from contextvars import ContextVar

from call.lib.history_models import HistoryMessage


# Shared, engine-independent run context.
# This must live in an import-stable module because fast-agent loads function tools
# via dynamic module names; tools should import contextvars from here.

CALL_CONVERSATION_ID: ContextVar[str | None] = ContextVar("call_conversation_id", default=None)
CALL_CALLER_AGENT_NAME: ContextVar[str | None] = ContextVar("call_caller_agent_name", default=None)
CALL_CALLER_SEED_HISTORY: ContextVar[list[HistoryMessage] | None] = ContextVar(
    "call_caller_seed_history",
    default=None,
)
CALL_AGENT_DEPTH: ContextVar[int] = ContextVar("call_agent_depth", default=0)

