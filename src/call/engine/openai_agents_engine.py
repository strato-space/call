from __future__ import annotations

from agents import Agent, Runner

from call.engine.types import EngineRunRequest, EngineRunResponse
from call.lib.history_openai_responses import (
    messages_to_response_items,
    response_items_to_messages,
)


class OpenAIAgentsEngine:
    """Stateless OpenAI Agents SDK adapter.

    History ownership lives in call; we pass history as Responses input items and
    reconstruct updated history from `RunResult.to_input_list()`.
    """

    async def run_turn(self, req: EngineRunRequest) -> EngineRunResponse:
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
        )

        result = await Runner.run(agent, items, session=None, max_turns=150)

        output_text = getattr(result, "final_output", "") or ""
        updated_items = result.to_input_list()
        updated_history = response_items_to_messages(updated_items)

        return EngineRunResponse(output_text=output_text, history=updated_history)

