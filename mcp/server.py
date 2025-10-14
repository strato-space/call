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
from call.lib.api import call_async as api_call_async
from call.lib.api import list as api_list
from call.lib.api import api_interpret_exec_payload
from call.lib.api import list_prompts as api_list_prompts
from call.lib.api import reload as api_reload
from call.lib.api import models as api_models
from call.lib.api import read as api_read
from call.lib.api import write as api_write
from call.lib import repo_db as repo_db_module


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
    log.info(
        "Starting mcp-call server (env loaded: call/.env exists=%s)",
        (call_dir / ".env").exists() if "call_dir" in locals() else False,
    )
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


@mcp.tool()
def read(id: str, ctx: Context | None = None) -> Any:
    """Return raw card text from repo.db."""

    try:
        return api_read(id)
    except repo_db_module.CardNotFoundError as exc:
        return {
            "ok": False,
            "error_code": 404,
            "description": str(exc),
            "code": "NO_DATA_FOUND",
        }
    except ValueError as exc:
        return {
            "ok": False,
            "error_code": 400,
            "description": str(exc),
            "code": "BAD_REQUEST",
        }


@mcp.tool()
def write(id: str, text: str, ctx: Context | None = None) -> Any:
    """Persist card text to repo.db and filesystem."""

    try:
        api_write(id, text)
        return "ok"
    except repo_db_module.CardNotFoundError as exc:
        return {
            "ok": False,
            "error_code": 404,
            "description": str(exc),
            "code": "NO_DATA_FOUND",
        }
    except ValueError as exc:
        return {
            "ok": False,
            "error_code": 400,
            "code": "BAD_REQUEST",
        }


@mcp.tool(name="exec")
async def mcp_exec(
    payload: Dict[str, Any],
    ctx: Context | None = None,
) -> Any:
    """Execute via payload (best for content buckets)."""
    # Forward to the library's single-source-of-truth payload interpreter
    kwargs, err = api_interpret_exec_payload(payload)
    if err:
        return err
    return await api_call_async(**kwargs)


@mcp.tool(name="notify")
async def mcp_notify(
    event: str,
    context: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
    ctx: Context | None = None,
) -> Any:
    """Acknowledge an event with optional context.

    Args:
        event: event name (e.g., 'session_transcription_done')
        context: optional list of context items
        session_id: optional session identifier
    """
    return await api_call_async(
        project=None,
        agent=None,
        prompt=None,
        target=None,
        input=None,
        event=event,
        session_id=session_id,
        attributes={"context": context} if context else None,
    )


@mcp.tool(name="call")
async def mcp_call(
    name: str,
    input: str,
    echo: bool = False,
    session_id: Optional[str] = None,
    event: Optional[str] = None,
    model: Optional[str] = None,
    ctx: Context | None = None,
) -> Any:
    """Invoke a single agent/prompt selection by name (uses same rules as /call)."""

    attrs = None
    if model is not None:
        model_str = str(model).strip()
        if model_str:
            attrs = {"model": model_str}
    res = await api_call_async(
        project=None,
        agent=None,
        prompt=None,
        target=name,
        input=input,
        event=event,
        session_id=session_id,
        echo=echo,
        attributes=attrs,
    )
    return res


@mcp.tool()
def reload(ctx: Context | None = None) -> Any:
    """Reload repository indices (agent/prompt) from .env configuration."""
    return api_reload()


@mcp.tool()
def models(ctx: Context | None = None) -> Any:
    """List available OpenAI models."""

    return api_models()


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
