from __future__ import annotations

import json
import logging

from agents import Agent, Runner
from agents.tool import FunctionTool

from call.engine.types import EngineRunRequest, EngineRunResponse
from call.engine.context import (
    CALL_CALLER_AGENT_NAME,
    CALL_CALLER_SEED_HISTORY,
    CALL_CONVERSATION_ID,
)
from call.engine.call_agent_tool import call_agent as _call_agent
from call.lib.history_models import HistoryMessage
from call.lib.history_openai_responses import (
    messages_to_response_items,
    response_items_to_messages,
)


logger = logging.getLogger(__name__)


def _build_call_agent_tool() -> FunctionTool:
    async def _invoke(_ctx, input_text: str):
        try:
            payload = json.loads(input_text) if input_text else {}
        except Exception:
            payload = {}
        agent = str(payload.get("agent") or payload.get("name") or "").strip()
        message = str(payload.get("message") or payload.get("input") or "").strip()
        share_history = bool(payload.get("share_history") or payload.get("shareHistory") or False)
        return await _call_agent(agent=agent, message=message, share_history=share_history)

    return FunctionTool(
        name="call_agent",
        description="Call another agent via call's engine middleware (mixed-mode).",
        params_json_schema={
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Target agent name/id"},
                "message": {"type": "string", "description": "Message to send"},
                "share_history": {
                    "type": "boolean",
                    "description": "If true, seed the target agent's history from the caller (best-effort).",
                    "default": False,
                },
            },
            "required": ["agent", "message"],
        },
        on_invoke_tool=_invoke,
    )


class OpenAIAgentsEngine:
    """Stateless OpenAI Agents SDK adapter.

    History ownership lives in call; we pass history as Responses input items and
    reconstruct updated history from `RunResult.to_input_list()`.
    """

    async def run_turn(self, req: EngineRunRequest) -> EngineRunResponse:
        seed_history: list[HistoryMessage] = list(req.history)
        try:
            import mcp.types as mcp_types

            seed_history.append(
                # Mirror the "current user turn" so share_history tools see it.
                HistoryMessage(
                    role="user",
                    content=[mcp_types.TextContent(type="text", text=str(req.input_text or ""))],
                )
            )
        except Exception as exc:
            logger.debug("[openai-agents] failed building seed history: %s", exc)

        tok_conv = CALL_CONVERSATION_ID.set(req.conversation_id)
        tok_agent = CALL_CALLER_AGENT_NAME.set(req.agent_name)
        tok_hist = CALL_CALLER_SEED_HISTORY.set(seed_history)
        try:
            items = messages_to_response_items(req.history)
            items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": req.input_text}],
                }
            )

            agent = Agent(
                name=req.agent_name,
                instructions=req.card.instructions,
                model=req.card.model or req.card.raw_metadata.get("model"),
                tools=[_build_call_agent_tool()],
            )

            result = await Runner.run(agent, items, session=None, max_turns=150)

            output_text = getattr(result, "final_output", "") or ""
            updated_items = result.to_input_list()
            updated_history = response_items_to_messages(updated_items)

            return EngineRunResponse(output_text=output_text, history=updated_history)
        finally:
            try:
                CALL_CONVERSATION_ID.reset(tok_conv)
            except Exception as exc:
                logger.debug("[openai-agents] context reset failed: %s", exc)
            try:
                CALL_CALLER_AGENT_NAME.reset(tok_agent)
            except Exception as exc:
                logger.debug("[openai-agents] context reset failed: %s", exc)
            try:
                CALL_CALLER_SEED_HISTORY.reset(tok_hist)
            except Exception as exc:
                logger.debug("[openai-agents] context reset failed: %s", exc)
