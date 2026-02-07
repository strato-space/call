from __future__ import annotations

import os

from call.engine.types import EngineAdapter, EngineName


def normalize_engine(value: str | None) -> EngineName:
    raw = str(value or "").strip().lower()
    if not raw:
        raw = str(os.environ.get("CALL_DEFAULT_ENGINE", "fast-agent")).strip().lower()

    if raw in {"fast-agent", "fast_agent", "fast"}:
        return "fast-agent"
    if raw in {"openai-agents", "openai_agents", "openai", "agents"}:
        return "openai-agents"
    # Default: fast-agent (decision locked by design doc).
    return "fast-agent"


def get_engine(engine: EngineName) -> EngineAdapter:
    if engine == "openai-agents":
        from call.engine.openai_agents_engine import OpenAIAgentsEngine

        return OpenAIAgentsEngine()

    from call.engine.fast_agent_engine import FastAgentEngine

    return FastAgentEngine()

