from __future__ import annotations

from typing import Any, Dict, Optional

from call.engine.registry import get_engine, normalize_engine
from call.engine.types import EngineRunRequest, EngineRunResponse
from call.lib.card_models import CallCard
from call.lib.history_models import HistoryMessage
from call.lib.history_db import load_history, save_history


def _string_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        # Split common separators.
        tokens: list[str] = []
        for part in value.replace(";", ",").split(","):
            tok = part.strip()
            if tok:
                tokens.append(tok)
        return tokens
    return []


def build_call_card_from_cfg(cfg: Any) -> CallCard:
    attrs = getattr(cfg, "attributes", None)
    raw_meta: Dict[str, Any] = dict(attrs) if isinstance(attrs, dict) else {}

    agent_name = (
        raw_meta.get("id")
        or raw_meta.get("name")
        or raw_meta.get("title")
        or getattr(cfg, "id", None)
        or getattr(cfg, "agent", None)
        or getattr(cfg, "prompt", None)
        or getattr(cfg, "project", None)
        or "agent"
    )
    agent_name = str(agent_name).strip() or "agent"

    engine = raw_meta.get("engine")
    engine_str = str(engine).strip() if isinstance(engine, str) else ""

    agents = _string_list(raw_meta.get("agents"))

    # Resolve mcp server names from cfg.mcp entries (best-effort).
    mcp_servers: list[str] = []
    for entry in getattr(cfg, "mcp", None) or []:
        if not isinstance(entry, dict):
            continue
        for key in ("name", "server", "id"):
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                mcp_servers.append(val.strip())
                break

    tools = _string_list(getattr(cfg, "tools", None) or [])

    return CallCard(
        agent_name=agent_name,
        engine=engine_str or "fast-agent",
        model=getattr(cfg, "model", None),
        instructions=str(getattr(cfg, "instructions", "") or ""),
        agents=agents,
        mcp_servers=mcp_servers,
        tools=tools,
        raw_metadata=raw_meta,
        source_path=getattr(cfg, "path", None),
        source_url=getattr(cfg, "url", None),
    )


async def run_cfg_turn(
    *,
    cfg: Any,
    conversation_id: str,
    input_text: str,
    history_override: list[HistoryMessage] | None = None,
) -> EngineRunResponse:
    card = build_call_card_from_cfg(cfg)
    engine_name = normalize_engine(card.engine)

    history = history_override if history_override is not None else load_history(conversation_id, card.agent_name)
    adapter = get_engine(engine_name)

    resp = await adapter.run_turn(
        EngineRunRequest(
            conversation_id=conversation_id,
            agent_name=card.agent_name,
            input_text=input_text,
            card=card,
            history=history,
        )
    )

    save_history(conversation_id, card.agent_name, engine_name, resp.history)
    return resp


async def run_agent_turn(
    *,
    conversation_id: str,
    input_text: str,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    target: Optional[str] = None,
    attributes_override: Optional[Dict[str, Any]] = None,
) -> EngineRunResponse:
    from call.lib.api import build_runnable_instructions_config

    cfg, err = build_runnable_instructions_config(
        project=project,
        agent=agent,
        prompt=prompt,
        target=target,
        input=input_text,
        attributes_override=attributes_override,
    )
    if err:
        raise RuntimeError(str(err))
    if cfg is None:
        raise RuntimeError("cfg not resolved")

    return await run_cfg_turn(cfg=cfg, conversation_id=conversation_id, input_text=input_text)
