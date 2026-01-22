from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from pathlib import Path
from call.lib.logging import configure_logging as call_logging, get_logger, debug_print
from call.lib.paths import default_env_candidates
from call.app.call import MCPInitializationError, wait_for_mcp_init, start_mcp_owner_task, stop_mcp_owner_task

try:
    # FastMCP SDK
    from mcp.server.fastmcp import FastMCP, Context
    from mcp.types import ToolAnnotations
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
    # Load environment deterministically: repo/workspace .env (do not override OS env)
    try:
        for candidate in default_env_candidates():
            load_dotenv(dotenv_path=str(candidate), override=False)
    except Exception:
        pass
    # Configure logging (DEBUG when CALL_DEBUG=1, else INFO)
    
    # Mark that we are running inside the call MCP server process so that
    # client-side MCP initialization can avoid creating a recursive "call" MCP server.
    try:
        os.environ.setdefault("CALL_MCP_SERVER_MODE", "1")
    except Exception:
        pass

    call_logging()
    log = get_logger("mcp")
    log.info(
        "Starting mcp-call server (env loaded: call/.env exists=%s)",
        bool(default_env_candidates() and default_env_candidates()[0].exists()),
    )
    debug_print("[mcp]", "[START]", "mcp-call server starting via stdio")
    
    # Start MCP owner task - initialization happens in the background task
    await start_mcp_owner_task("mcp")
    log.info("MCP owner task started for server lifespan")
    debug_print("[mcp]", "[START]", "MCP server ready (owner running)")

    yield {}

    # Shutdown: signal the owner task to clean up
    await stop_mcp_owner_task()
    log.info("MCP owner task stopped for server lifespan")
    debug_print("[mcp]", "[STOP]", "MCP server shutdown complete")


mcp = FastMCP("mcp-call", lifespan=lifespan)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
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


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
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


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True, destructiveHint=True))
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


@mcp.tool(name="exec", annotations=ToolAnnotations(openWorldHint=True))
async def mcp_exec(
    payload: Dict[str, Any],
    ctx: Context | None = None,
) -> Any:
    """Execute via payload (best for content buckets).

    Example payload from Actions API docs:

    ```json
    {
     "agent": "UxResearcherReq",
     "input": "user message text",
     "replay": "replay to message text",
     "context":
      [
          {
              "type": "text",
              "text": "foo headline line.\nbar summary line.\nbaz call-to-action button description.",
              "source": {
                  "type": "file",
                  "file_id": "13LlOsEr6AGw6n6YX1mzrUIVUdH3xT63-",
                  "name": "foo-bar-document.docx"
              }
          },
          {
              "type": "text",
              "text": "foo question about service? bar cloud offer allows foo chain registration. baz on-prem build does not include that.",
              "source": {
                  "type": "session",
                  "_id": "68afe646ef46aed531a8ecc5",
                  "name": "foo bar voicebot session"
              }
          },
          {
              "type": "session",
              "_id": "68c7ab4cab67ffbd365062f1"
          },
          {
              "type": "file",
              "file_id": "13LlOsEr6AGw6n6YX1mzrUIVUdH3xT63-"
          }
      ]
    }
    ```

    See `prompt/schema/context-array.md` for the canonical context array schema.

    Fields mirror the exec payload contract — provide exactly one selector
    (`project`|`agent`|`prompt`|`target`) plus optional `context`, `input`,
    `model`, `session_id`, and `echo`.
    """
    try:
        # Forward to the library's single-source-of-truth payload interpreter
        kwargs, err = api_interpret_exec_payload(payload)
        if err:
            return err
        return await api_call_async(**kwargs)
    except Exception as exc:
        debug_print("[mcp]", "[ERROR]", f"Exec payload failed: {type(exc).__name__}: {exc}")
        # Return structured error instead of raising to prevent FastMCP cancel scope issues
        return {
            "ok": False,
            "error_code": 500,
            "description": f"Payload execution failed: {str(exc)}",
            "code": "EXEC_EXECUTION_ERROR",
        }


@mcp.tool(name="notify", annotations=ToolAnnotations(idempotentHint=True))
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
    try:
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
    except Exception as exc:
        debug_print("[mcp]", "[ERROR]", f"Notify failed: {type(exc).__name__}: {exc}")
        # Return structured error instead of raising to prevent FastMCP cancel scope issues
        return {
            "ok": False,
            "error_code": 500,
            "description": f"Notify execution failed: {str(exc)}",
            "code": "NOTIFY_EXECUTION_ERROR",
        }


@mcp.tool(name="call", annotations=ToolAnnotations(openWorldHint=True))
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
    
    log = get_logger("mcp.tool.call")
    log.info("[CALL] Tool invoked: name=%s, input_len=%d, echo=%s, session_id=%s, event=%s, model=%s", 
             name, len(input) if input else 0, echo, session_id, event, model)
    debug_print("[mcp]", "[CALL]", f"Tool invoked: name={name}, input_len={len(input) if input else 0}")

    try:
        # Wait for MCP servers to be ready before first call (background init)
        log.debug("[CALL] Waiting for MCP servers to be ready...")
        await wait_for_mcp_init(timeout=120.0)
        log.debug("[CALL] MCP servers ready")
        
        attrs = None
        if model is not None:
            model_str = str(model).strip()
            if model_str:
                attrs = {"model": model_str}
        
        log.debug("[CALL] Calling api_call_async...")
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
        
        # Log result details
        res_type = type(res).__name__
        res_ok = res.get("ok") if isinstance(res, dict) else None
        res_len = len(str(res)) if res is not None else 0
        
        log.info("[CALL] ✅ Tool completed: type=%s, ok=%s, result_len=%d", res_type, res_ok, res_len)
        debug_print("[mcp]", "[CALL]", f"✅ Tool completed: type={res_type}, ok={res_ok}, result_len={res_len}")
        
        # Log first 500 chars of result for debugging
        if res is not None:
            res_preview = str(res)[:500]
            log.debug("[CALL] Result preview (500 chars): %s", res_preview)
        
        return res
    except Exception as exc:
        log.error("[CALL] ❌ Tool failed: %s: %s", type(exc).__name__, exc, exc_info=True)
        debug_print("[mcp]", "[ERROR]", f"Tool call failed: {type(exc).__name__}: {exc}")
        # Return structured error instead of raising to prevent FastMCP cancel scope issues
        return {
            "ok": False,
            "error_code": 500,
            "description": f"Tool execution failed: {str(exc)}",
            "code": "TOOL_EXECUTION_ERROR",
        }


@mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
def reload(ctx: Context | None = None) -> Any:
    """Reload repository indices (agent/prompt) from .env configuration."""
    try:
        debug_print("[mcp]", "[TOOL]", "reload invoked via MCP tool")
    except Exception:
        pass

    try:
        result = api_reload()
        try:
            debug_print("[mcp]", "[TOOL]", f"reload result: ok={result.get('ok', None)}")
        except Exception:
            pass
        return result
    except Exception as exc:
        try:
            debug_print(
                "[mcp]",
                "[TOOL]",
                f"reload failed with {type(exc).__name__}: {exc}",
            )
        except Exception:
            pass
        return {
            "ok": False,
            "error_code": 500,
            "description": f"reload failed: {type(exc).__name__}: {exc}",
            "code": "RELOAD_ERROR",
        }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
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
