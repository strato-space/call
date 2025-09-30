from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pathlib import Path
from call.lib.logging import configure_logging as call_logging, get_logger, debug_print

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
    # Load environment deterministically: call/.env then repo-root .env (do not override OS env)
    try:
        here = Path(__file__).resolve()
        call_dir = here.parent.parent  # .../call/
        load_dotenv(dotenv_path=str(call_dir / ".env"), override=False)
        load_dotenv(dotenv_path=str(call_dir.parent / ".env"), override=False)
    except Exception:
        pass
    # Configure logging (DEBUG when CALL_DEBUG=1, else INFO)
    try:
        call_logging()
    except Exception:
        pass
    log = get_logger("mcp")
    log.info("Starting mcp-call server (env loaded: call/.env exists=%s)", (call_dir / ".env").exists() if 'call_dir' in locals() else False)
    debug_print("[mcp]", "[START]", "mcp-call server starting via stdio")
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


@mcp.tool(name="notify")
def mcp_notify(
    event: str,
    context: Optional[List[Dict[str, Any]]] = None,
    echo: bool = False,
    session_id: Optional[str] = None,
    ctx: Context | None = None,
) -> Any:
    """Acknowledge an event without executing the pipeline.

    Event notifications may include optional context items and echo/session metadata.
    project|agent|prompt|target selectors are not allowed together with event.
    """

    payload: Dict[str, Any] = {"event": event}
    if context is not None:
        payload["context"] = context
    if echo:
        payload["echo"] = True
    if session_id:
        payload["session_id"] = session_id
    kwargs, err = api_interpret_exec_payload(payload)
    if err:
        return err
    return api_call(**kwargs)


@mcp.tool(name="call")
def mcp_call(
    name: str,
    input: str,
    echo: bool = False,
    session_id: Optional[str] = None,
    event: Optional[str] = None,
    ctx: Context | None = None,
) -> Any:
    """Invoke a single agent/prompt selection by name (uses same rules as /call)."""

    res = api_call(project=None, agent=None, prompt=None, target=name, input=input, event=event, session_id=session_id, echo=echo)
    return res


@mcp.tool()
def reload(ctx: Context | None = None) -> Any:
    """Reload repository indices (agent/prompt) from .env configuration."""
    return api_reload()


async def main():
    # Extra log for explicit execution
    try:
        log = get_logger("mcp")
        log.info("mcp-call main() starting stdio loop")
        debug_print("[mcp]", "[RUN]", "run_stdio_async")
    except Exception:
        pass
    await mcp.run_stdio_async()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
