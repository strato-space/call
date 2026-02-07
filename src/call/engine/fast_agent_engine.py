from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import yaml

from fast_agent import FastAgent

from call.engine.fast_agent_config_gen import generate_fast_agent_config
from call.engine.context import (
    CALL_CALLER_AGENT_NAME,
    CALL_CALLER_SEED_HISTORY,
    CALL_CONVERSATION_ID,
)
from call.engine.types import EngineRunRequest, EngineRunResponse
from call.lib.card_models import CallCard
from call.lib.history_fast_agent import fast_agent_to_messages, messages_to_fast_agent
from call.lib.history_models import HistoryMessage
from call.lib.paths import default_cache_dir


logger = logging.getLogger(__name__)


def _safe_filename(name: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").strip()) or "agent"
    return token[:200]


def _render_agent_card_md(
    *,
    name: str,
    instructions: str,
    agents: list[str] | None = None,
    default: bool = False,
    servers: list[str] | None = None,
    model: str | None = None,
    function_tools: list[str] | None = None,
) -> str:
    frontmatter: dict = {"type": "agent", "name": name}
    if default:
        frontmatter["default"] = True
    if agents:
        frontmatter["agents"] = list(agents)
    if servers:
        frontmatter["servers"] = list(servers)
    if model:
        frontmatter["model"] = model
    if function_tools:
        frontmatter["function_tools"] = list(function_tools)

    fm = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    body = (instructions or "").strip()
    return f"---\n{fm}\n---\n{body}\n"


@dataclass
class _RuntimeState:
    fast: FastAgent
    app_cm: object
    app: object
    agents_dir: Path


class _FastAgentRuntime:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._state: _RuntimeState | None = None

    async def ensure_started(self) -> _RuntimeState:
        async with self._lock:
            if self._state is not None:
                return self._state

            cfg_path = generate_fast_agent_config()
            fast = FastAgent(
                "call",
                config_path=str(cfg_path),
                parse_cli_args=False,
                quiet=True,
            )

            # Mimic CLI args; keep conservative defaults (no watch in call by default).
            fast.args = SimpleNamespace(
                watch=False,
                reload=True,
                model=None,
                name=None,
                quiet=True,
                server=False,
                transport=None,
            )

            agents_dir = default_cache_dir() / "agentcards"
            agents_dir.mkdir(parents=True, exist_ok=True)

            # Root should already contain at least one card (written by ensure_agent_cards).
            fast.load_agents(str(agents_dir))

            app_cm = fast.run()
            app = await app_cm.__aenter__()  # type: ignore[attr-defined]

            self._state = _RuntimeState(fast=fast, app_cm=app_cm, app=app, agents_dir=agents_dir)
            return self._state

    async def ensure_agent_cards(self, cards: list[CallCard]) -> _RuntimeState:
        agents_dir = default_cache_dir() / "agentcards"
        agents_dir.mkdir(parents=True, exist_ok=True)

        tool_spec = f"{(Path(__file__).resolve().parent / 'call_agent_tool.py')}:call_agent"

        changed = False
        for idx, card in enumerate(cards):
            fname = _safe_filename(card.agent_name)
            path = agents_dir / f"{fname}.md"

            # fast-agent expects referenced subagents to exist as cards.
            declared_subagents = [a for a in (card.agents or []) if a]

            text = _render_agent_card_md(
                name=card.agent_name,
                instructions=card.instructions,
                agents=declared_subagents or None,
                default=(idx == 0),
                servers=(card.mcp_servers or None),
                model=(card.model or None),
                function_tools=[tool_spec],
            )
            prev = None
            try:
                if path.exists():
                    prev = path.read_text(encoding="utf-8")
            except Exception as exc:
                logger.debug("[fast-agent] failed reading agentcard %s: %s", path, exc)
                prev = None

            if prev != text:
                path.write_text(text, encoding="utf-8")
                changed = True

        state = self._state
        if state is None:
            state = await self.ensure_started()
        elif changed:
            try:
                await state.app.reload_agents()  # reload + refresh shared instance
            except Exception as exc:
                logger.debug("[fast-agent] reload_agents failed: %s", exc)

        return state


_RUNTIME = _FastAgentRuntime()


class FastAgentEngine:
    """fast-agent engine adapter (default)."""

    def _resolve_cards(self, root: CallCard) -> list[CallCard]:
        """Resolve sub-agent cards declared in `agents` (best-effort, 1-level)."""
        cards: list[CallCard] = [root]
        if not root.agents:
            return cards

        try:
            from call.app.runtime import build_call_card_from_cfg
            from call.lib.api import build_runnable_instructions_config
        except Exception as exc:
            logger.debug("[fast-agent] failed importing resolver helpers: %s", exc)
            return cards

        for sub_name in root.agents:
            name = str(sub_name or "").strip()
            if not name:
                continue
            try:
                cfg, err = build_runnable_instructions_config(
                    project=None,
                    agent=name,
                    prompt=None,
                    target=None,
                    input="",
                    attributes_override=None,
                )
                if err or cfg is None:
                    cfg, err = build_runnable_instructions_config(
                        project=None,
                        agent=None,
                        prompt=None,
                        target=name,
                        input="",
                        attributes_override=None,
                    )
                if err or cfg is None:
                    logger.debug("[fast-agent] failed to resolve sub-agent '%s': %s", name, err)
                    continue
                cards.append(build_call_card_from_cfg(cfg))
            except Exception as exc:
                logger.debug("[fast-agent] failed resolving sub-agent '%s': %s", name, exc)

        return cards

    async def run_turn(self, req: EngineRunRequest) -> EngineRunResponse:
        # Ensure the target agent card exists (plus declared sub-agents).
        cards = self._resolve_cards(req.card)
        state = await _RUNTIME.ensure_agent_cards(cards)

        try:
            base = state.app[req.agent_name]  # type: ignore[index]
        except Exception as exc:
            raise RuntimeError(f"fast-agent agent not found: {req.agent_name}") from exc

        clone = await base.spawn_detached_instance(name=f"{base.name}[{req.conversation_id}]")
        clone.load_message_history(messages_to_fast_agent(req.history))
        # Expose run context for mixed-mode tools (call_agent).
        seed_history: list[HistoryMessage] = list(req.history)
        try:
            import mcp.types as mcp_types

            seed_history.append(
                HistoryMessage(
                    role="user",
                    content=[mcp_types.TextContent(type="text", text=str(req.input_text or ""))],
                )
            )
        except Exception as exc:
            logger.debug("[fast-agent] failed building seed history: %s", exc)

        tok_conv = CALL_CONVERSATION_ID.set(req.conversation_id)
        tok_agent = CALL_CALLER_AGENT_NAME.set(req.agent_name)
        tok_hist = CALL_CALLER_SEED_HISTORY.set(seed_history)
        try:
            reply = await clone.send(req.input_text)
        finally:
            try:
                CALL_CONVERSATION_ID.reset(tok_conv)
            except Exception as exc:
                logger.debug("[fast-agent] context reset failed: %s", exc)
            try:
                CALL_CALLER_AGENT_NAME.reset(tok_agent)
            except Exception as exc:
                logger.debug("[fast-agent] context reset failed: %s", exc)
            try:
                CALL_CALLER_SEED_HISTORY.reset(tok_hist)
            except Exception as exc:
                logger.debug("[fast-agent] context reset failed: %s", exc)

        updated_history = fast_agent_to_messages(getattr(clone, "message_history", []) or [])
        return EngineRunResponse(output_text=str(reply or ""), history=updated_history)
