from __future__ import annotations

import logging

from call.engine.context import (
    CALL_AGENT_DEPTH,
    CALL_CALLER_SEED_HISTORY,
    CALL_CONVERSATION_ID,
)


logger = logging.getLogger(__name__)

_MAX_DEPTH = 3


async def call_agent(agent: str, message: str, share_history: bool = False) -> str:
    """Call another agent via call's engine middleware (mixed-mode).

    This function is used as a fast-agent FunctionTool (loaded from file path),
    so it must not rely on module-level globals that won't match across dynamic imports.
    Shared context lives in `call.engine.context`.
    """

    current_depth = int(CALL_AGENT_DEPTH.get() or 0)
    if current_depth >= _MAX_DEPTH:
        return f"[call_agent] recursion limit reached (depth={current_depth}, max={_MAX_DEPTH})"

    token = CALL_AGENT_DEPTH.set(current_depth + 1)
    try:
        conversation_id = (CALL_CONVERSATION_ID.get() or "").strip()
        if not conversation_id:
            return "[call_agent] missing conversation_id"

        agent_name = str(agent or "").strip()
        if not agent_name:
            return "[call_agent] missing agent name"

        # Resolve target card using call's repo index.
        from call.lib.api import build_runnable_instructions_config

        cfg, err = build_runnable_instructions_config(
            project=None,
            agent=agent_name,
            prompt=None,
            target=None,
            input=message,
            attributes_override=None,
        )
        if err or cfg is None:
            cfg, err = build_runnable_instructions_config(
                project=None,
                agent=None,
                prompt=None,
                target=agent_name,
                input=message,
                attributes_override=None,
            )
        if err or cfg is None:
            return f"[call_agent] failed to resolve agent '{agent_name}': {err or 'NO_DATA_FOUND'}"

        history_override = None
        if share_history:
            seed = CALL_CALLER_SEED_HISTORY.get()
            if seed:
                try:
                    from call.app.runtime import build_call_card_from_cfg
                    from call.lib.history_db import load_history

                    target_card = build_call_card_from_cfg(cfg)
                    existing = load_history(conversation_id, target_card.agent_name)
                    if not existing:
                        history_override = seed
                except Exception as exc:
                    logger.debug("[call_agent] share_history seed check failed: %s", exc)

        from call.app.runtime import run_cfg_turn

        resp = await run_cfg_turn(
            cfg=cfg,
            conversation_id=conversation_id,
            input_text=str(message or ""),
            history_override=history_override,
        )
        return resp.output_text
    except Exception as exc:
        logger.exception("[call_agent] failed")
        return f"[call_agent] error: {type(exc).__name__}: {exc}"
    finally:
        try:
            CALL_AGENT_DEPTH.reset(token)
        except Exception as exc:
            logger.debug("[call_agent] depth reset failed: %s", exc)

