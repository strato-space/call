from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

try:
    # FastMCP SDK
    from mcp.server.fastmcp import FastMCP, Context
except Exception as e:  # pragma: no cover
    raise SystemExit(f"FastMCP not available: {e}")

# Library imports (no cross-reference with 'voice')
from call.lib.api import call as api_call
from call.lib.api import list as api_list
from call.lib.api import api_interpret_exec_payload
from call.lib.api import list_prompts as api_list_prompts
from call.lib.api import reload as api_reload


@asynccontextmanager
async def lifespan(app: FastMCP):
    load_dotenv()
    # Nothing to preload for library-only usage
    yield {}


mcp = FastMCP("mcp-call", lifespan=lifespan)


@mcp.tool()
def agents(
    query: Optional[str] = None,
    include_aliases: bool = False,
    grouped: bool = False,
    ctx: Context | None = None,
) -> Any:
    """List available agents discovered in the prompt repository (hierarchical)."""
    # list_agents() supports wildcard filters via list(project, agent, prompt)
    # Here 'query' is a convenience substring for agent name; for simplicity we ignore it and return all
    return api_list(project=None, agent=None, prompt=None)


@mcp.tool()
def prompts(
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    state: Optional[str] = None,
    ctx: Context | None = None,
) -> List[Dict[str, Any]]:
    """List prompts from the prompt repo (ready/draft)."""
    items = api_list_prompts(project=project, agent=agent, prompt=prompt, state=state)
    return items if isinstance(items, list) else ([items] if items else [])


@mcp.tool(name="exec")
def mcp_exec(
    payload: Dict[str, Any],
    ctx: Context | None = None,
) -> Any:
    """Execute using a single JSON payload.

    Payload shape: { agent?: str, prompt?: str, target?: str, context?: any, project?: str, echo?: bool, session_id?: str }
    """
    kwargs, err = api_interpret_exec_payload(payload or {})
    if err:
        return err
    return api_call(**kwargs)


@mcp.tool()
def reload(ctx: Context | None = None) -> Any:
    """Reload repository indices (agent/prompt) from .env configuration."""
    return api_reload()


async def main():
    await mcp.run_stdio_async()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
