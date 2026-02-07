from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from call.lib.card_models import CallCard
from call.lib.history_models import HistoryMessage


EngineName = Literal["fast-agent", "openai-agents"]


@dataclass
class EngineRunRequest:
    conversation_id: str
    agent_name: str
    input_text: str
    card: CallCard
    history: list[HistoryMessage]


@dataclass
class EngineRunResponse:
    output_text: str
    history: list[HistoryMessage]
    diagnostics: dict[str, Any] | None = None


class EngineAdapter(Protocol):
    async def run_turn(self, req: EngineRunRequest) -> EngineRunResponse: ...

