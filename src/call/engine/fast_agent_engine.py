from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import yaml

from fast_agent import FastAgent

from call.engine.fast_agent_config_gen import generate_fast_agent_config
from call.engine.types import EngineRunRequest, EngineRunResponse
from call.lib.card_models import CallCard
from call.lib.history_fast_agent import fast_agent_to_messages, messages_to_fast_agent
from call.lib.paths import default_cache_dir


def _safe_filename(name: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").strip()) or "agent"
    return token[:200]


def _render_agent_card_md(
    *, name: str, instructions: str, agents: list[str] | None = None, default: bool = False
) -> str:
    frontmatter: dict = {"type": "agent", "name": name}
    if default:
        frontmatter["default"] = True
    if agents:
        frontmatter["agents"] = list(agents)

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
        self._loaded_root: bool = False

    async def ensure_started(self) -> _RuntimeState:
        async with self._lock:
            if self._state is not None:
                return self._state

            cfg_path = generate_fast_agent_config()
            fast = FastAgent(config_path=str(cfg_path))

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

            # Register the root even if empty; we will reload after writing cards.
            fast.load_agents(str(agents_dir))
            self._loaded_root = True

            app_cm = fast.run()
            app = await app_cm.__aenter__()  # type: ignore[attr-defined]

            self._state = _RuntimeState(fast=fast, app_cm=app_cm, app=app, agents_dir=agents_dir)
            return self._state

    async def ensure_agent_cards(self, cards: list[CallCard]) -> None:
        state = await self.ensure_started()

        changed = False
        for idx, card in enumerate(cards):
            fname = _safe_filename(card.agent_name)
            path = state.agents_dir / f"{fname}.md"

            # Only reference subagents that we have cards for in this generation.
            declared_subagents = [a for a in (card.agents or []) if a]

            text = _render_agent_card_md(
                name=card.agent_name,
                instructions=card.instructions,
                agents=declared_subagents or None,
                default=(idx == 0),
            )
            prev = None
            try:
                if path.exists():
                    prev = path.read_text(encoding="utf-8")
            except Exception:
                prev = None

            if prev != text:
                path.write_text(text, encoding="utf-8")
                changed = True

        if changed:
            try:
                await state.fast.reload_agents()
            except Exception:
                # If reload fails, the next request will surface the error.
                pass


_RUNTIME = _FastAgentRuntime()


class FastAgentEngine:
    """fast-agent engine adapter (default)."""

    async def run_turn(self, req: EngineRunRequest) -> EngineRunResponse:
        # Ensure the target agent card exists (plus sub-agents if provided).
        await _RUNTIME.ensure_agent_cards([req.card])
        state = await _RUNTIME.ensure_started()

        try:
            base = state.app[req.agent_name]  # type: ignore[index]
        except Exception as exc:
            raise RuntimeError(f"fast-agent agent not found: {req.agent_name}") from exc

        clone = await base.spawn_detached_instance(name=f"{base.name}[{req.conversation_id}]")
        clone.load_message_history(messages_to_fast_agent(req.history))
        reply = await clone.send(req.input_text)

        updated_history = fast_agent_to_messages(getattr(clone, "message_history", []) or [])
        return EngineRunResponse(output_text=str(reply or ""), history=updated_history)

