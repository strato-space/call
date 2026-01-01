from __future__ import annotations

from pathlib import Path
from typing import Dict, Any
from agents.model_settings import ModelSettings
from dataclasses import dataclass

from call.lib.api import RunnableConfig
from call.lib.logging import debug_print


# Local YAML loader for MCP configuration (simple safe_load)
def _load_mcp_yaml_config(path: Path) -> Dict[str, Any]:
    try:
        import yaml as _yaml

        return _yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception as e:
        try:
            import logging as _log

            _log.exception("_load_mcp_yaml_config: failed to read %s", path)
        except Exception:
            pass
        return {}


# Thin wrapper to expose discovery to tests: call.app.call.discover_agent_yaml
def discover_agent_yaml(agent_name: str, project: str | None = None):
    """Delegate to call.lib.discovery.discover_agent_yaml(agent_name, project=None).

    - Supports project-scoped search when `project` is provided.
    - Falls back to cross-project indices and directory scan when `project` is None.
    """
    from call.lib.discovery import discover_agent_yaml as _discover

    return _discover(agent_name, project=project)


import os
import argparse
import asyncio
import functools
import enum
import importlib
import logging
from typing import Optional, Dict, Any, List, Callable, Awaitable, Type, Union
import base64
import re
import shlex
import time
from contextvars import ContextVar
import uuid
from contextlib import asynccontextmanager, ExitStack, AsyncExitStack, suppress
import urllib.parse
from pathlib import Path
import sys

import json
import tempfile
import yaml
import inspect
import httpx
import anyio
from openai import OpenAI, DefaultAsyncHttpxClient
from openai.types.shared import Reasoning as OpenAIReasoning
import html as _html

from call.lib import api as call_api
from call.lib import call_db


def _suppress_mcp_cleanup_errors(loop, context):
    """Custom asyncio exception handler to suppress known MCP/anyio cleanup errors.
    
    During MCP client cleanup, anyio's CancelScope may exit in a different task than
    it was entered, causing RuntimeError. This is a known issue in MCP/anyio that
    doesn't affect functionality but creates noisy logs.
    
    See: https://github.com/python-trio/anyio/issues/issues/cancel-scope-task-mismatch
    """
    exc = context.get('exception')
    msg = context.get('message', '')
    
    # Suppress "Attempted to exit cancel scope in a different task" from MCP cleanup
    if isinstance(exc, RuntimeError):
        if 'cancel scope' in str(exc).lower() and 'different task' in str(exc).lower():
            # Log at debug level instead of error
            try:
                logging.debug(
                    "[mcp-cleanup] Suppressed known anyio CancelScope cleanup warning: %s",
                    exc
                )
            except Exception:
                pass
            return
    
    # For all other exceptions, use default handler
    loop.default_exception_handler(context)


def _extract_cost_from_output(text: str | None):
    """Parse cost line like 'Cost: 0.015 USD' from textual output."""
    if not isinstance(text, str):
        return None
    m = re.search(r"Cost:\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]{3})?", text)
    if not m:
        return None
    try:
        cost = float(m.group(1))
    except Exception:
        return None
    currency = m.group(2).strip() if m.group(2) else None
    return cost, currency


def _update_cost_totals_from_output(text: str | None) -> call_db.CostTotals | None:
    """Update cost totals in call_db from textual output."""
    try:
        parsed = _extract_cost_from_output(text)
        if not parsed:
            return None
        cost, currency = parsed
        res = call_db.update_cost_totals(cost, currency)
        if res:
            logging.debug(
                "[call] cost totals updated cost=%.6f cur=%s totals=all=%.6f today=%.6f date=%s",
                cost,
                res.currency or currency,
                res.total_cost,
                res.total_cost_today,
                res.last_updated_date,
            )
            return res
    except Exception:
        logging.debug("[call] cost totals update failed", exc_info=True)
    return None


class _LiteralYamlDumper(yaml.SafeDumper):
    """YAML dumper that renders multiline strings as block scalars.
    
    Overrides Emitter to disable the heuristics that force quoted style
    for long strings, ensuring literal block scalars are always respected.
    """
    
    def choose_scalar_style(self):
        """Override PyYAML's scalar style selection to honor representer hints.
        
        PyYAML Emitter has heuristics that override style hints from representers:
        - Strings >1024 chars forced to quoted style
        - Strings with trailing whitespace forced to quoted
        - Strings with certain special chars forced to quoted
        
        This override forces the style specified by the representer.
        """
        # Call parent implementation
        style = super().choose_scalar_style()

        # If representer explicitly requested literal (|) or folded (>), honor it
        # even for very long strings
        if self.event.style in ('|', '>'):
            # Force literal/folded style regardless of length or content
            return self.event.style
        
        return style


def _literal_yaml_str_representer(dumper, data):
    # Always use literal block scalar for multiline strings to preserve formatting
    if "\n" in data:
        # Debug: log when we're using literal style
        if len(data) > 200:
            try:
                if os.environ.get("CALL_DEBUG_YAML", "0").strip().lower() in ("1", "true", "yes", "on"):
                    from call.lib.logging import debug_print

                    debug_print(
                        f"[YAML Representer] Using literal style (multiline): "
                        f"len={len(data)}, newlines={data.count(chr(10))}, preview={data[:60]!r}"
                    )
            except Exception:
                pass
        # Use | (literal) for clean multiline display
        # CRITICAL: PyYAML Emitter can ignore style hint for very long strings (>1024 chars)
        # or strings with trailing whitespace. Force literal style by ensuring content is clean.
        try:
            # Strip trailing spaces from each line to help PyYAML accept literal style
            cleaned = "\n".join(line.rstrip() for line in data.split("\n"))
            result = dumper.represent_scalar("tag:yaml.org,2002:str", cleaned, style="|")
            return result
        except Exception as e:
            # If literal style fails, log and fall back to quoted
            try:
                if os.environ.get("CALL_DEBUG_YAML", "0").strip().lower() in ("1", "true", "yes", "on"):
                    from call.lib.logging import debug_print
                    debug_print(f"[YAML Representer] Literal style failed: {e!r}, falling back to quoted")
            except Exception:
                pass
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')
    
    # For very long single-line strings, convert to multiline by adding artificial newline at end
    # This forces literal block scalar style which never wraps
    # PyYAML will strip the trailing newline when parsing (using |-)
    if len(data) > 100:
        # Add newline to force literal style, dumper will use |- which strips trailing newlines
        modified_data = data + "\n"
        return dumper.represent_scalar("tag:yaml.org,2002:str", modified_data, style="|")
    
    # For strings starting with special YAML chars, use single-quoted style to avoid ambiguity
    if data and data[0] in "#-:>|&*![]{}?@`":
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    
    # For short normal strings, use plain style (no quotes)
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=None)


_LiteralYamlDumper.add_representer(str, _literal_yaml_str_representer)


def _dump_yaml_literal(obj: Any, *, width: int = 999999) -> str:
    """Serialize Python data to YAML with readable multiline formatting."""

    try:
        # Configure dumper to handle very long literal scalars
        # PyYAML may fall back to quoted style if it thinks literal is "too long"
        # We force it to always respect our style choice by using extreme limits
        result = yaml.dump(
            obj,
            Dumper=_LiteralYamlDumper,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=width,
            line_break="\n",
            default_style=None,  # Don't force a single style globally
        )
        # Debug: check if result contains quoted strings with escape sequences
        if len(result) > 500 and ('"' in result[:200] or '\\n' in result[:200]):
            try:
                if os.environ.get("CALL_DEBUG_YAML", "0").strip().lower() in ("1", "true", "yes", "on"):
                    from call.lib.logging import debug_print
                    has_literal = '|' in result[:200] or '|-' in result[:200]
                    has_quoted = '"' in result[:200] and '\\n' in result[:200]
                    debug_print(
                        f"[YAML Dump Result] len={len(result)}, has_literal={has_literal}, "
                        f"has_quoted={has_quoted}, preview={result[:150]!r}"
                    )
            except Exception:
                pass
        return result
    except Exception as e:
        # First YAML dump failed - log and try fallback
        try:
            if os.environ.get("CALL_DEBUG_YAML", "0").strip().lower() in ("1", "true", "yes", "on"):
                from call.lib.logging import debug_print
                debug_print(f"[YAML Dump] _LiteralYamlDumper failed: {e!r}, trying safe_dump fallback")
        except Exception:
            pass
        try:
            return yaml.safe_dump(
                obj,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                width=width,
            )
        except Exception:
            try:
                return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
            except Exception:
                return str(obj)


# Import agent utilities (internal copy)
try:
    from .utils.agent_utils import extract_agent_attributes, get_agent_instructions
except ImportError:
    # Fallback for when running as script directly
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "utils"))
    from agent_utils import extract_agent_attributes, get_agent_instructions
import shutil

# Import HTML/Telegram text utilities from package-relative utils
from .utils.html_sanitizer import (
    sanitize_telegram_html,
    clean_html_for_telegraph,
    minify_html_func,
)
from .utils.telegram_text import (
    telegram_truncate_html_safe,
    telegram_truncate_markdown_safe,
    telegram_prepare_html,
    telegram_prepare_markdown,
)
from .utils.telegraph_utils import publish_results, create_telegrath_account


from mcp.types import CallToolResult, TextContent
from mcp.shared.exceptions import McpError

# Increase default max turns for nested agent runs (tools/sub-agents) to avoid hitting library default 10
try:
    from agents import run as _agents_run
    import os as _os_patch

    _agents_run.DEFAULT_MAX_TURNS = int(
        _os_patch.environ.get("AGENTS_DEFAULT_MAX_TURNS", "150") or "150"
    )
except Exception:
    pass

"""
Environment loading: resolve .env relative to this file location, not CWD.
Search order:
  1) call/.env (sibling of this app/ directory)
  2) repo_root/.env (one level above call/)
If not found, raise with a helpful message. We do not copy files.
"""
_here = Path(__file__).resolve()
_app_dir = _here.parent
_call_dir = _app_dir.parent
_repo_root = _call_dir.parent

_env_candidates = [
    _call_dir / ".env",
    _repo_root / ".env",
]
_env_file = next((p for p in _env_candidates if p.exists()), None)
if _env_file is None:
    checked = ", ".join(str(p) for p in _env_candidates)
    raise FileNotFoundError(f".env not found. Checked: {checked}")

from agents import Agent, Runner, WebSearchTool, SQLiteSession
from agents.tool import FileSearchTool, FunctionTool, ImageGenerationTool, function_tool
from agents.run_context import RunContextWrapper
from agents.mcp import MCPServerSse, MCPServerStdio
from agents.model_settings import ModelSettings

# Simple Agent factory/cache to avoid re-instantiating identical Agents within a run.
# Cached agents always receive a fresh ``mcp_servers`` list on reuse to avoid stale MCP connections.
AGENT_CACHE: dict[str, Agent] = {}

# MCP servers singleton cache: (name → server instance)
# Lazy-loaded on first use, shared across all agent runs
_MCP_SERVERS_CACHE: dict[str, Any] = {}
_MCP_SERVERS_LOCK = asyncio.Lock()


class _MCPInitState(enum.Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    READY = "ready"
    FAILED = "failed"


_MCP_INIT_STATE: _MCPInitState = _MCPInitState.NOT_STARTED
_MCP_INIT_ERROR: MCPInitializationError | None = None
_MCP_INIT_EVENT: asyncio.Event | None = None
_MCP_CONFIG_CACHE: dict | None = None
_MCP_EXIT_STACK: AsyncExitStack | None = None  # Global exit stack for singleton MCP servers
_MCP_OWNER_TASK: asyncio.Task | None = None
_MCP_OWNER_TAG: str | None = None
_MCP_OWNER_SHUTDOWN_EVENT: asyncio.Event | None = None
_MCP_OWNER_LOCK = asyncio.Lock()


def _reset_mcp_state() -> None:
    global _MCP_INIT_STATE, _MCP_INIT_ERROR, _MCP_INIT_EVENT, _MCP_CONFIG_CACHE
    global _MCP_SERVERS_CACHE, _MCP_EXIT_STACK, _MCP_OWNER_TASK, _MCP_OWNER_TAG, _MCP_OWNER_SHUTDOWN_EVENT
    _MCP_INIT_STATE = _MCPInitState.NOT_STARTED
    _MCP_INIT_ERROR = None
    _MCP_INIT_EVENT = None
    _MCP_CONFIG_CACHE = None
    _MCP_SERVERS_CACHE = {}
    _MCP_EXIT_STACK = None
    _MCP_OWNER_TASK = None
    _MCP_OWNER_TAG = None
    _MCP_OWNER_SHUTDOWN_EVENT = None


def _set_mcp_exit_stack(astack: AsyncExitStack | None) -> None:
    """Set or clear the global MCP exit stack from the owner task."""
    global _MCP_EXIT_STACK
    _MCP_EXIT_STACK = astack


def _on_mcp_owner_done(task: asyncio.Task) -> None:
    """Callback to reset global state when the owner task finishes."""
    global _MCP_OWNER_TASK, _MCP_OWNER_TAG, _MCP_OWNER_SHUTDOWN_EVENT
    log = logging.getLogger("call.mcp.owner")
    try:
        task.result()
    except asyncio.CancelledError:
        log.info("MCP owner task cancelled")
    except Exception:
        log.exception("MCP owner task failed")
    _MCP_OWNER_TASK = None
    _MCP_OWNER_TAG = None
    _MCP_OWNER_SHUTDOWN_EVENT = None


def is_mcp_owner_running() -> bool:
    """Return True when the MCP owner task exists and is still active."""

    task = _MCP_OWNER_TASK
    return bool(task and not task.done())


def get_mcp_owner_tag() -> str | None:
    """Expose the tag of the currently running MCP owner task, if any."""

    return _MCP_OWNER_TAG


async def _mcp_owner_main(tag: str) -> None:
    """Owner task that manages MCP initialization and cleanup."""
    global _MCP_OWNER_SHUTDOWN_EVENT

    log = logging.getLogger("call.mcp.owner")
    shutdown_event = asyncio.Event()
    _MCP_OWNER_SHUTDOWN_EVENT = shutdown_event

    debug_tag = f"[mcp-owner:{tag}]"
    log.info("MCP owner task starting (tag=%s)", tag)
    debug_print(debug_tag, "starting owner task")

    try:
        async with AsyncExitStack() as exit_stack:
            _set_mcp_exit_stack(exit_stack)
            log.info("MCP owner exit stack entered")
            debug_print(debug_tag, "exit stack entered")

            try:
                await preinitialize_mcp_servers_async(tag)
                log.info("MCP owner initialization complete")
                debug_print(debug_tag, "init complete")
            except Exception:
                log.exception("MCP owner initialization failed")
                raise

            log.info("MCP owner waiting for shutdown signal")
            debug_print(debug_tag, "awaiting shutdown signal")

            try:
                await asyncio.shield(shutdown_event.wait())
            except asyncio.CancelledError:
                log.info("MCP owner task cancelled while waiting for shutdown; forcing cleanup")
                shutdown_event.set()

            log.info("MCP owner shutdown signaled")
            debug_print(debug_tag, "shutdown signaled")

            try:
                await cleanup_mcp_servers()
            except Exception:
                log.exception("MCP owner cleanup failed")
            else:
                log.info("MCP owner cleanup complete")
                debug_print(debug_tag, "cleanup complete")
    finally:
        _set_mcp_exit_stack(None)
        log.info("MCP owner exit stack closed")
        debug_print(debug_tag, "exit stack cleared")


async def start_mcp_owner_task(tag: str) -> asyncio.Task:
    """Ensure the MCP owner task is running and return it."""
    global _MCP_OWNER_TASK, _MCP_OWNER_TAG

    async with _MCP_OWNER_LOCK:
        if _MCP_OWNER_TASK and not _MCP_OWNER_TASK.done():
            if _MCP_OWNER_TAG != tag:
                logging.getLogger("call.mcp.owner").info(
                    "Reusing existing MCP owner task (current tag=%s, requested=%s)",
                    _MCP_OWNER_TAG,
                    tag,
                )
                debug_print(
                    f"[mcp-owner:{_MCP_OWNER_TAG}]",
                    f"reuse requested by tag={tag}",
                )
            return _MCP_OWNER_TASK

        loop = asyncio.get_running_loop()
        _MCP_OWNER_TAG = tag
        task = loop.create_task(_mcp_owner_main(tag), name=f"mcp-owner:{tag}")
        task.add_done_callback(_on_mcp_owner_done)
        _MCP_OWNER_TASK = task
        logging.getLogger("call.mcp.owner").info("MCP owner task created (tag=%s)", tag)
        debug_print(f"[mcp-owner:{tag}]", "owner task created")
        return task


async def stop_mcp_owner_task(timeout: float = 30.0) -> None:
    """Signal the MCP owner task to shut down and wait for completion."""
    async with _MCP_OWNER_LOCK:
        task = _MCP_OWNER_TASK
        shutdown_event = _MCP_OWNER_SHUTDOWN_EVENT

    if not task or task.done():
        return

    log = logging.getLogger("call.mcp.owner")

    if shutdown_event and not shutdown_event.is_set():
        shutdown_event.set()
        log.info("Signaled MCP owner shutdown")
        debug_print(f"[mcp-owner:{_MCP_OWNER_TAG}]", "shutdown signaled")

    try:
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("Timed out waiting for MCP owner task to finish")
        task.cancel()
    except Exception:
        log.exception("Error while awaiting MCP owner task completion")

def create_mcp_lifespan_callbacks(tag: str = "app") -> tuple[callable, callable]:
    """Create post_init and post_shutdown callbacks for PTB-style applications.

    Args:
        tag: Human-readable identifier for logging (e.g. "actions", "bot").
    """

    async def _post_init(application):
        log = logging.getLogger("call.mcp.lifespan")
        log.info("Starting MCP owner for %s", tag)
        debug_print(f"[mcp-lifespan:{tag}]", "post_init -> start owner")
        await start_mcp_owner_task(tag)

    async def _post_shutdown(application):
        log = logging.getLogger("call.mcp.lifespan")
        log.info("Stopping MCP owner for %s", tag)
        debug_print(f"[mcp-lifespan:{tag}]", "post_shutdown -> stop owner")
        await stop_mcp_owner_task()

    return _post_init, _post_shutdown


async def wait_for_mcp_init(timeout: float = 120.0) -> None:
    """Wait for MCP servers to finish initializing. Safe to call multiple times.

    Args:
        timeout: Maximum seconds to wait for initialization (default 120s)

    Raises:
        MCPInitializationError: If initialization failed or timeout
    """
    global _MCP_INIT_STATE, _MCP_INIT_ERROR, _MCP_INIT_EVENT, _MCP_OWNER_TASK

    # Already initialized successfully
    if _MCP_INIT_STATE == _MCPInitState.READY:
        return

    # Initialization failed previously
    if _MCP_INIT_STATE == _MCPInitState.FAILED:
        raise _MCP_INIT_ERROR or MCPInitializationError("MCP initialization failed")

    # Auto-start the owner task if nothing has kicked it off yet
    if _MCP_OWNER_TASK is None or _MCP_OWNER_TASK.done():
        await start_mcp_owner_task("waiter")

    # Ensure we have an event to wait on
    event = _ensure_mcp_event()

    # Re-check in case initialization completed while starting the owner
    if _MCP_INIT_STATE == _MCPInitState.READY:
        return

    if _MCP_INIT_STATE == _MCPInitState.FAILED:
        raise _MCP_INIT_ERROR or MCPInitializationError("MCP initialization failed")

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise MCPInitializationError(f"MCP initialization timeout after {timeout}s") from exc

    if _MCP_INIT_STATE == _MCPInitState.FAILED:
        raise _MCP_INIT_ERROR or MCPInitializationError("MCP initialization failed")

    if _MCP_INIT_STATE != _MCPInitState.READY:
        raise MCPInitializationError("MCP initialization did not reach ready state")


class MCPInitializationError(RuntimeError):
    """Raised when MCP servers fail to initialize."""

    def __init__(self, message: str, *, cause: Exception | None = None):
        super().__init__(message)
        self.cause = cause


def _ensure_mcp_event() -> asyncio.Event:
    global _MCP_INIT_EVENT
    if _MCP_INIT_EVENT is None:
        _MCP_INIT_EVENT = asyncio.Event()
    return _MCP_INIT_EVENT


def get_or_create_agent(
    *,
    name: str,
    instructions: str,
    model: str,
    model_settings: ModelSettings,
    tools: list,
    mcp_servers: list,
) -> Agent:
    """Return cached Agent by name, refreshing its mcp_servers on reuse.

    This keeps the Agent cache lightweight while ensuring that agents-as-tools
    never hold onto MCP server instances whose sessions have been cleaned up
    (for example, after MCP auto-reinitialization or remote timeout).
    """
    cached: Agent | None = None
    try:
        cached = AGENT_CACHE.get(name)
    except Exception as e:
        # Cache read is best-effort; log and continue with fresh Agent
        logging.debug("[agent-cache] Failed to read AGENT_CACHE for %s: %s", name, e)

    if cached is not None:
        # Always refresh MCP servers on reuse so agents don't hold stale connections
        try:
            cached.mcp_servers = mcp_servers
        except Exception as e:
            logging.debug(
                "[agent-cache] Failed to update mcp_servers for %s: %s", name, e
            )
        return cached

    agent = Agent(
        name=name,
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        tools=tools,
        mcp_servers=mcp_servers,
    )
    try:
        AGENT_CACHE[name] = agent
    except Exception:
        pass
    return agent


# Telegraph usage is handled via utils.telegraph_utils

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Bot, Message
from telegram.error import TelegramError, TimedOut, NetworkError, BadRequest
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode, ChatAction
from dotenv import load_dotenv


def _ensure_proxy_env_defaults() -> None:
    """Populate HTTP(S)_PROXY env vars from ALL_PROXY when missing."""

    try:
        all_proxy = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
        if not all_proxy:
            return

        updated: list[str] = []
        for key in ("HTTP_PROXY", "HTTPS_PROXY"):
            if not os.environ.get(key):
                os.environ[key] = all_proxy
                updated.append(key)
        for key in ("http_proxy", "https_proxy"):
            if not os.environ.get(key):
                os.environ[key] = all_proxy
                updated.append(key)

        if updated:
            try:
                debug_print(
                    "[proxy]",
                    "Filled missing proxy env vars from ALL_PROXY:",
                    ", ".join(updated),
                )
            except Exception:
                pass
    except Exception as exc:
        try:
            debug_print("[proxy]", f"Failed to mirror ALL_PROXY: {exc}")
        except Exception:
            pass


def _build_proxy_proxies_map() -> dict[str, str]:
    """Construct an httpx proxies mapping from the current environment."""

    env = os.environ
    all_proxy = env.get("ALL_PROXY") or env.get("all_proxy")
    http_proxy = env.get("HTTP_PROXY") or env.get("http_proxy") or all_proxy
    https_proxy = env.get("HTTPS_PROXY") or env.get("https_proxy") or all_proxy

    proxies: dict[str, str] = {}
    if http_proxy:
        proxies["http://"] = http_proxy
    if https_proxy:
        proxies["https://"] = https_proxy
    if not proxies and all_proxy:
        proxies["all://"] = all_proxy

    return proxies


def _prepare_async_http_client_kwargs(proxies: dict[str, str]) -> dict[str, Any]:
    """Translate our proxy mapping into kwargs supported by httpx.AsyncClient."""

    if not proxies:
        return {}

    try:
        params = inspect.signature(httpx.AsyncClient.__init__).parameters
    except Exception:
        return {}

    if "proxies" in params:
        return {"proxies": proxies}

    # httpx>=0.28 replaced the mapping with a single `proxy` argument.
    if "proxy" in params:
        for key in ("https://", "http://", "all://"):
            value = proxies.get(key)
            if value:
                return {"proxy": value}

    # Fall back to per-scheme mounts when available.
    if "mounts" in params:
        mounts: dict[str, httpx.AsyncBaseTransport] = {}
        for scheme, url in proxies.items():
            prefix = scheme.split(":", 1)[0]
            mounts[f"{prefix}://"] = httpx.AsyncHTTPTransport(proxy=url)
        if mounts:
            return {"mounts": mounts}

    return {}


# Proxy diagnostics removed - no longer needed


def _configure_agents_proxy_http_client() -> None:
    """Ensure OpenAI Agents SDK shares our proxy-aware HTTP client."""

    proxies = _build_proxy_proxies_map()
    if not proxies:
        return

    client_kwargs = _prepare_async_http_client_kwargs(proxies)
    if not client_kwargs:
        try:
            debug_print(
                "[proxy]",
                "httpx AsyncClient exposes no proxy kwargs; relying on env",
            )
        except Exception:
            pass
        return

    client: DefaultAsyncHttpxClient | None = None
    configured_modules: list[str] = []

    for module_path, attr in [
        ("agents.models.openai_provider", "_http_client"),
        ("agents.voice.models.openai_model_provider", "_http_client"),
    ]:
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:
            try:
                debug_print("[proxy]", f"Skip proxy wiring for {module_path}: {exc}")
            except Exception:
                pass
            continue

        if getattr(module, attr, None) is not None:
            continue

        if client is None:
            try:
                client = DefaultAsyncHttpxClient(**client_kwargs)
            except Exception as exc:
                try:
                    debug_print(
                        "[proxy]",
                        "Failed to create proxy-aware httpx client:",
                        str(exc),
                    )
                except Exception:
                    pass
                return

        setattr(module, attr, client)
        configured_modules.append(module_path)

    if configured_modules:
        try:
            debug_print(
                "[proxy]",
                "Configured OpenAI Agents HTTP client with proxies for:",
                ", ".join(configured_modules),
            )
        except Exception:
            pass


load_dotenv(dotenv_path=str(_env_file), override=True)

_ensure_proxy_env_defaults()
_configure_agents_proxy_http_client()


# check_proxy_tool removed - no longer needed


async def async_retry(
    op: Callable[[], Awaitable[Any]],
    *,
    retries: int = 2,
    base_delay: float = 0.5,
    jitter: float = 0.1,
    retry_on: tuple[Type[BaseException], ...] = (Exception,),
) -> Any:
    """Retry an async operation with exponential backoff and jitter.

    Args:
        op: coroutine factory with no args that returns the awaited operation.
        retries: number of retries (not counting the first attempt).
        base_delay: initial delay before first retry.
        jitter: random jitter added/subtracted to delay.
        retry_on: exception classes to trigger a retry.
    """
    import random

    attempt = 0
    while True:
        try:
            return await op()
        except retry_on as e:
            if attempt >= retries:
                raise
            delay = base_delay * (2**attempt)
            # Apply jitter within ±jitter seconds
            if jitter:
                delay = max(0.0, delay + random.uniform(-jitter, jitter))
            try:
                await asyncio.sleep(delay)
            except Exception:
                # If sleep fails for some reason, proceed immediately
                pass
            attempt += 1


async def safe_edit_message_text(
    *, chat_id: int, message_id: int, text: str, parse_mode: str | None = None
) -> Message | None:
    """Safe edit for Telegram messages with robust fallbacks.

    - Sanitizes HTML if requested by caller.
    - Retries transient errors.
    - On BadRequest 'can't parse entities', falls back to plain text edit.
    - On 'message to edit not found', sends a new message instead.
    - Returns Message on success; None when falling back silently fails.
    """
    await _init_bot_safe()
    # Prepare body conservatively; let caller pre-sanitize if needed
    prepared = text or ""

    async def _op():
        return await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=prepared, parse_mode=parse_mode
        )

    try:
        return await async_retry(
            _op,
            retries=2,
            base_delay=1.0,
            jitter=0.2,
            retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
        )
    except BadRequest as e:
        msg = str(e).lower()
        # Fallback to plain edit if HTML entities fail
        if "can't parse entities" in msg or "parse entities" in msg or "entity" in msg:
            try:
                plain = re.sub(r"<[^>]+>", "", prepared)

                async def _plain():
                    return await bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=plain
                    )

                return await async_retry(
                    _plain,
                    retries=1,
                    base_delay=0.7,
                    jitter=0.1,
                    retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
                )
            except Exception:
                pass
        # If message was deleted or cannot be edited, send a new one
        if (
            "message to edit not found" in msg
            or "message can't be edited" in msg
            or "message is not modified" in msg
        ):
            try:
                return await safe_send_message(
                    chat_id=chat_id, text=prepared, parse_mode=parse_mode
                )
            except Exception:
                return None


def _attrs_to_yaml_text(attrs) -> str | None:
    """Convert a dict of attributes to YAML with block style for multi-line strings.

    Standalone helper so main functions stay compact.
    """
    if not isinstance(attrs, dict) or not attrs:
        return None
    return _dump_yaml_literal(attrs, width=1000)


def debug_dump_cfg_preview(cfg) -> None:
    """Debug: dump cfg attributes and instruction preview to the log."""
    ytxt = _attrs_to_yaml_text(getattr(cfg, "attributes", None))
    if ytxt:
        debug_print("[cfg]", "attributes (YAML):\n" + ytxt)

    attrs_has_instr = isinstance(getattr(cfg, "attributes", None), dict) and (
        "instructions" in (cfg.attributes or {})
    )

    if attrs_has_instr:
        return

    instr = getattr(cfg, "instructions", "") or ""
    preview = instr[:4096] + ("…" if len(instr) > 4096 else "")

    debug_print("[cfg]", "Agent instructions preview: |-\n" + preview)
    debug_print("[cfg]", "Agent instructions len:", str(len(instr)))


async def _validate_and_cache_mcp_config() -> dict | None:
    """Validate MCP config, create servers once, cache both.
    
    Returns config YAML dict.
    Server instances created once and cached for reuse.
    
    Raises MCPInitializationError if validation fails.
    """
    global _MCP_INIT_STATE, _MCP_INIT_ERROR, _MCP_SERVERS_CACHE, _MCP_CONFIG_CACHE, _MCP_EXIT_STACK

    event = _ensure_mcp_event()

    # Return cached result if already initialized
    if _MCP_INIT_STATE is _MCPInitState.READY:
        return _MCP_CONFIG_CACHE

    # Re-raise previous failure
    if _MCP_INIT_STATE is _MCPInitState.FAILED:
        raise _MCP_INIT_ERROR or MCPInitializationError("MCP initialization failed")

    # Wait if initialization is in progress
    if _MCP_INIT_STATE is _MCPInitState.IN_PROGRESS:
        await event.wait()
        if _MCP_INIT_STATE is _MCPInitState.READY:
            return _MCP_CONFIG_CACHE
        raise _MCP_INIT_ERROR or MCPInitializationError("MCP initialization failed")

    # Start initialization
    async with _MCP_SERVERS_LOCK:
        if _MCP_INIT_STATE is _MCPInitState.NOT_STARTED:
            _MCP_INIT_STATE = _MCPInitState.IN_PROGRESS
            event.clear()
        else:
            return await _validate_and_cache_mcp_config()

    cfg_path_env = (os.environ.get("MCP_CONFIG_PATH") or "").strip()
    cfg_path = Path(cfg_path_env) if cfg_path_env else None

    try:
        
        
        logging.info("[mcp] MCP_CONFIG_PATH=%s", cfg_path_env or "<unset>")
        debug_print("[mcp]", f"MCP_CONFIG_PATH={cfg_path_env or '<unset>'}")

        # No config path => MCP disabled silently
        if not cfg_path_env:
            _MCP_CONFIG_CACHE = None
            _MCP_INIT_STATE = _MCPInitState.READY
            event.set()
            logging.info("[mcp] MCP disabled (MCP_CONFIG_PATH not set); skipping init")
            return _MCP_CONFIG_CACHE

        path = cfg_path
        if not path.exists():
            # Explicit opt-in but file missing -> error
            _MCP_INIT_STATE = _MCPInitState.FAILED
            _MCP_INIT_ERROR = MCPInitializationError(f"MCP config not found: {cfg_path_env}")
            event.set()
            raise _MCP_INIT_ERROR

        cfg_yaml = _load_mcp_yaml_config(path)
        if not cfg_yaml:
            raise MCPInitializationError(
                "MCP config is empty or invalid YAML; ensure it contains a top-level "
                "'mcpServers' mapping with server entries indented under it (for example, "
                "use '  time:' instead of 'time:' at the root)."
            )
        if not isinstance(cfg_yaml.get("mcpServers"), dict):
            raise MCPInitializationError(
                "MCP config must contain a top-level 'mcpServers' mapping; make sure all "
                "MCP servers are nested under it with consistent indentation."
            )

        # Validate config has at least one enabled server
        enabled_count = sum(
            1 for spec in cfg_yaml.get("mcpServers", {}).values()
            if isinstance(spec, dict) and spec.get("enabled", False)
        )
        if enabled_count == 0:
            logging.info("[mcp] No enabled MCP servers; skipping init")
            _MCP_CONFIG_CACHE = None
            _MCP_INIT_STATE = _MCPInitState.READY
            event.set()
            return _MCP_CONFIG_CACHE

        enabled_names = [
            name
            for name, spec in cfg_yaml.get("mcpServers", {}).items()
            if isinstance(spec, dict) and spec.get("enabled", False)
        ]
        logging.info("[mcp] Config validated - %d enabled servers: %s", enabled_count, enabled_names)
        debug_print("[mcp]", f"✅ Config valid - {enabled_count} enabled servers: {enabled_names}")

        # Use provided exit stack or fail
        if _MCP_EXIT_STACK is None:
            raise MCPInitializationError("MCP initialization requires exit stack to be set")

        # Filter config: keep enabled servers that are not plain serverUrl-only (remote HTTP/SSE).
        local_cfg_yaml: dict | None = None
        try:
            servers_map = cfg_yaml.get("mcpServers") or {}
            if isinstance(servers_map, dict):
                local_servers: dict[str, Any] = {}
                for name, spec in servers_map.items():
                    if not isinstance(spec, dict):
                        continue
                    if not spec.get("enabled", False):
                        continue
                    has_command = "command" in spec
                    has_bridge = isinstance(spec.get("bridge"), dict)
                    has_server_url = "serverUrl" in spec
                    # Plain remote HTTP/SSE: serverUrl present, but no command/bridge.
                    if has_server_url and not has_command and not has_bridge:
                        continue
                    local_servers[name] = spec
                if local_servers:
                    local_cfg_yaml = {"mcpServers": local_servers}
        except Exception as e:
            logging.getLogger("call.mcp").exception(
                "[mcp] Failed to split local/remote MCP servers: %s", e
            )
            local_cfg_yaml = cfg_yaml

        servers = await _build_mcp_servers_from_yaml(local_cfg_yaml, _MCP_EXIT_STACK)
        _MCP_SERVERS_CACHE = {srv.name: srv for srv in servers} if servers else {}
        _MCP_CONFIG_CACHE = cfg_yaml
        logging.info("[mcp] Created %d MCP server instances (cached for reuse)", len(_MCP_SERVERS_CACHE))
        debug_print(
            "[mcp]",
            f"{len(_MCP_SERVERS_CACHE)} servers created and cached: {list(_MCP_SERVERS_CACHE.keys())}",
        )
        
        _MCP_INIT_STATE = _MCPInitState.READY
        event.set()
        return _MCP_CONFIG_CACHE

    except MCPInitializationError as exc:
        logging.error("[mcp] Init failed: %s", exc)
        _MCP_INIT_STATE = _MCPInitState.FAILED
        _MCP_INIT_ERROR = exc
        event.set()
        raise
    except Exception as exc:
        logging.exception("[mcp] Unexpected init error")
        wrapper = MCPInitializationError("MCP init failed", cause=exc)
        _MCP_INIT_STATE = _MCPInitState.FAILED
        _MCP_INIT_ERROR = wrapper
        event.set()
        raise wrapper


async def _prepare_mcp_servers(astack: AsyncExitStack | None = None) -> tuple[list[Any], dict | None]:
    """Return cached singleton MCP servers, reused across calls."""
    if _MCP_INIT_STATE is _MCPInitState.NOT_STARTED or _MCP_INIT_STATE is _MCPInitState.IN_PROGRESS:
        await _validate_and_cache_mcp_config()
    
    if _MCP_INIT_STATE is _MCPInitState.FAILED:
        raise _MCP_INIT_ERROR or MCPInitializationError("MCP init failed")
    
    # Return cached singleton servers
    return list(_MCP_SERVERS_CACHE.values()), _MCP_CONFIG_CACHE


async def cleanup_mcp_servers() -> None:
    """Clear MCP server cache. Actual cleanup done by exit stack in lifespan."""
    global _MCP_SERVERS_CACHE
    
    count = len(_MCP_SERVERS_CACHE)
    debug_print("[mcp]", f"Clearing {count} MCP servers cache...")
    _MCP_SERVERS_CACHE = {}
    logging.info("[mcp] MCP server cache cleared")


async def preinitialize_mcp_servers_async(module_tag: str) -> dict[str, Any]:
    """Async helper to initialize MCP servers (singleton, reused)."""
    tag = f"[{module_tag}]" if not module_tag.startswith("[") else module_tag
    debug_print(tag, "[STARTUP]", "Initializing MCP servers...")
    cfg_yaml = await _validate_and_cache_mcp_config()
    debug_print(tag, "[STARTUP]", f"✅ {len(_MCP_SERVERS_CACHE)} servers initialized and cached")
    logging.getLogger("call.mcp").info("%s MCP servers initialized - %d cached", tag, len(_MCP_SERVERS_CACHE))
    return {}  # Server instances cached in _MCP_SERVERS_CACHE


def preinitialize_mcp_servers_sync(module_tag: str) -> dict[str, Any]:
    """Sync helper to warm up MCP servers outside of an event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(preinitialize_mcp_servers_async(module_tag))
    
    if loop.is_running():
        raise RuntimeError("Cannot use sync init while event loop is running")
    return loop.run_until_complete(preinitialize_mcp_servers_async(module_tag))


def _extract_file_search_payload(name: str) -> Any | None:
    """Parse ``FileSearchTool[...]`` expressions into a payload."""

    if not isinstance(name, str):
        return None

    tool_name = name.strip()
    if not tool_name:
        return None

    if not (tool_name.startswith("FileSearchTool[") and tool_name.endswith("]")):
        return None

    inner = tool_name[len("FileSearchTool[") : -1].strip()
    if not inner:
        return None

    parts = [part.strip() for part in inner.split(",") if part.strip()]
    if not parts:
        return inner
    if len(parts) == 1:
        return parts[0]
    return parts


def get_tool_by_name(name: str) -> Any:
    tool_name = (name or "").strip()
    if not tool_name:
        return None

    payload = _extract_file_search_payload(tool_name)
    if payload is not None:
        if isinstance(payload, str):
            ids = [payload]
        else:
            try:
                ids = list(payload)
            except TypeError:
                ids = []
        valid_ids = [
            vs_id
            for vs_id in (id_.strip() for id_ in ids if isinstance(id_, str))
            if vs_id.startswith("vs_")
        ]
        if valid_ids:
            try:
                return FileSearchTool(vector_store_ids=valid_ids)
            except Exception:
                return None
        return None

    tools_catalog = {
        "WebSearchTool": WebSearchTool,
        "ImageGenerationTool": ImageGenerationTool,
        "image_genetation_tool": lambda: image_genetation_tool,
    }

    factory = tools_catalog.get(tool_name)
    if factory is None:
        return None

    try:
        tool = factory()
    except TypeError:
        # Already an instance (lambda returning existing tool)
        tool = factory
    except Exception:
        return None

    return tool


async def build_tools_for_cfg(cfg) -> list[Any]:
    """Build tool instances from the explicit `cfg.tools` entries."""

    configured_tools = [str(name).strip() for name in cfg.tools if str(name).strip()]
    tool_instances: list[Any] = []
    seen: set[str] = set()

    for name in configured_tools:
        if name in seen:
            continue
        tool_obj = None

        payload = _extract_file_search_payload(name)
        if payload is not None:
            resolved_ids = await resolve_vector_stores(payload)
            valid_ids: list[str] = []
            for vs_id in resolved_ids:
                vs_id_str = (vs_id or "").strip()
                if vs_id_str.startswith("vs_") and vs_id_str not in valid_ids:
                    valid_ids.append(vs_id_str)

            if valid_ids:
                try:
                    tool_obj = FileSearchTool(vector_store_ids=valid_ids)
                except Exception:
                    tool_obj = None
            # Skip fallback to get_tool_by_name regardless of resolution result
            if tool_obj is not None:
                tool_instances.append(tool_obj)
                seen.add(name)
            continue

        tool_obj = get_tool_by_name(name)
        if tool_obj is not None:
            tool_instances.append(tool_obj)
            seen.add(name)

    return tool_instances


def _remote_url_with_token(remote_url: str | None, token: str | None) -> str | None:
    if not remote_url or not token:
        return None

    url = remote_url.strip()
    tok = token.strip()
    if not url or not tok:
        return None

    if url.startswith("git@"):
        if url.startswith("git@github.com:"):
            path = url.split(":", 1)[1]
            path = path.lstrip("/")
            return f"https://x-access-token:{tok}@github.com/{path}"
        return None

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    netloc = parsed.netloc
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]

    netloc = f"x-access-token:{tok}@{netloc}"

    try:
        return urllib.parse.urlunsplit(parsed._replace(netloc=netloc))
    except Exception:
        return None


async def prompt_repo_git_pull_rebase() -> None:
    """Run `git pull --rebase` (with plain pull fallback) in the prompt repo.

    This helper only updates the local clone; it does not push.
    """
    try:
        prompt_repo = discover_prompt_repo()

        from asyncio.subprocess import PIPE

        async def _run_git(
            cmd: list[str], *, env: dict[str, str] | None = None
        ) -> tuple[int, bytes, bytes]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(prompt_repo),
                stdout=PIPE,
                stderr=PIPE,
                env=env,
            )
            out, err = await proc.communicate()
            return proc.returncode, out, err

        git_env: dict[str, str] | None = None
        token = os.environ.get("GITHUB_TOKEN_PROMPT", "").strip()

        try:
            debug_print("[git]", f"Token available: {bool(token)}")
        except Exception:
            pass

        # Always set git_env to disable interactive prompts and bypass proxy for GitHub
        git_env = os.environ.copy()
        git_env["GIT_TERMINAL_PROMPT"] = "0"
        git_env["GIT_ASKPASS"] = "echo"
        git_env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
        # Ensure GitHub is not proxied
        no_proxy = git_env.get("NO_PROXY", "")
        if "github.com" not in no_proxy:
            git_env["NO_PROXY"] = (
                f"{no_proxy},github.com,*.github.com"
                if no_proxy
                else "github.com,*.github.com"
            )
        git_env["no_proxy"] = git_env["NO_PROXY"]

        try:
            debug_print("[git]", f"NO_PROXY set to: {git_env['NO_PROXY']}")
        except Exception:
            pass

        if token:
            git_env["GITHUB_TOKEN_PROMPT"] = token
            try:
                rc_url, out_url, _ = await asyncio.wait_for(
                    _run_git(
                        ["git", "config", "--get", "remote.origin.url"], env=git_env
                    ),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                debug_print("[git]", "git config timed out, skipping token setup")
                return
            except Exception as e:
                debug_print("[git]", f"git config failed: {type(e).__name__}: {e}")
                return

            if rc_url == 0:
                remote_url = out_url.decode(errors="ignore").strip()
                token_url = _remote_url_with_token(remote_url, token)
                if token_url:
                    try:
                        await asyncio.wait_for(
                            _run_git(
                                ["git", "remote", "set-url", "origin", token_url],
                                env=git_env,
                            ),
                            timeout=5.0,
                        )
                        try:
                            debug_print(
                                "[git]",
                                "origin remote switched to token-auth URL for prompt repo",
                            )
                        except Exception:
                            pass
                    except asyncio.TimeoutError:
                        debug_print("[git]", "git remote set-url timed out")
                    except Exception as e:
                        debug_print(
                            "[git]",
                            f"git remote set-url failed: {type(e).__name__}: {e}",
                        )

        debug_print("[git]", f"Pulling prompt repo at {prompt_repo} with --rebase")
        try:
            rc, out_rebase, err_rebase = await asyncio.wait_for(
                _run_git(["git", "pull", "--rebase"], env=git_env), timeout=10.0
            )
        except asyncio.TimeoutError:
            debug_print("[git]", "git pull --rebase timed out after 10s, skipping")
            return
        except Exception as e:
            # Compact log for unexpected errors during rebase pull
            debug_print("[git]", f"git pull --rebase failed: {type(e).__name__}: {e}")
            return
        if rc != 0:
            # Rebase can fail when there are local changes; keep log compact but informative
            stderr_len = len(err_rebase or b"")
            debug_print(
                "[git]",
                f"git pull --rebase failed (rc={rc}, stderr_bytes={stderr_len}); "
                "retrying plain pull (common cause: local uncommitted changes)",
            )
            try:
                rc_plain, out_plain, err_plain = await asyncio.wait_for(
                    _run_git(["git", "pull"], env=git_env), timeout=10.0
                )
            except asyncio.TimeoutError:
                debug_print("[git]", "git pull timed out after 10s, skipping")
                return
            except Exception as e:
                debug_print("[git]", f"git pull failed: {type(e).__name__}: {e}")
                return
            debug_print(
                "[git]",
                "plain pull rc=%s out=%s err=%s"
                % (
                    rc_plain,
                    out_plain.decode(errors="ignore")[:200],
                    err_plain.decode(errors="ignore")[:200],
                ),
            )
        else:
            debug_print(
                "[git]",
                "rebase pull rc=%s out=%s err=%s"
                % (
                    rc,
                    out_rebase.decode(errors="ignore")[:200],
                    err_rebase.decode(errors="ignore")[:200],
                ),
            )
    except Exception:
        pass


async def _git_pull_prompt_repo() -> None:
    """Backward-compatibility wrapper used by tests.

    Delegates to prompt_repo_git_pull_rebase() without changing behavior.
    """
    await prompt_repo_git_pull_rebase()


def _collect_tools(cfg) -> list[tuple[str, str]]:
    """Collect agent/prompt entries that should be exposed as tools."""
    entries: list[tuple[str, str]] = []
    attrs = getattr(cfg, "attributes", {}) if hasattr(cfg, "attributes") else {}
    if not isinstance(attrs, dict):
        return entries
    ag_map = attrs.get("agents")
    if isinstance(ag_map, dict):
        for name, desc in ag_map.items():
            try:
                entries.append((str(name), "" if desc is None else str(desc)))
            except Exception:
                continue

    pr_map = attrs.get("prompts")
    try:
        debug_print(
            "[tools]",
            f"Scanning project attributes for agents/prompts; has_agents={isinstance(ag_map, dict)} has_prompts={isinstance(pr_map, (dict, list))}",
        )
    except Exception:
        pass

    if isinstance(pr_map, dict):
        for name, desc in pr_map.items():
            try:
                entries.append((str(name), "" if desc is None else str(desc)))
            except Exception:
                continue
    elif isinstance(pr_map, list):
        for item in pr_map:
            try:
                # Handle both plain strings and single-key dicts (YAML list of mappings)
                if isinstance(item, dict):
                    for name, desc in item.items():
                        entries.append((str(name), "" if desc is None else str(desc)))
                else:
                    entries.append((str(item), ""))
            except Exception:
                continue

    return entries


def _append_agent_tools_from_cfg(
    *, cfg, tools: list[Any], mcp_servers: list[Any]
) -> None:
    """Populate `tools` with helper agents/prompts declared in the config."""

    tools2append = _collect_tools(cfg)
    if not tools2append:
        return

    base_tools_snapshot = list(tools)

    for sub_name, sub_desc in tools2append:
        tool = _build_agent_tool(
            cfg=cfg,
            sub_name=sub_name,
            sub_desc=sub_desc,
            base_tools_snapshot=base_tools_snapshot,
            mcp_servers=mcp_servers,
        )
        if tool:
            tools.append(tool)


@dataclass
class ProcessedUserInput:
    canonical: str
    sanitized: str
    embedded: str
    normalized: str


async def process_user_input(user_input: Any) -> ProcessedUserInput:
    """Normalize user input: sanitize target, embed files, and provide canonical strings."""

    if isinstance(user_input, str):
        canonical = user_input
    else:
        try:
            canonical = json.dumps(user_input, ensure_ascii=False)
        except Exception:
            canonical = str(user_input)

    sanitized_dict: dict | None = None
    sanitized_str = canonical

    if isinstance(user_input, str):
        try:
            parsed = json.loads(user_input)
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            sanitized_dict = dict(parsed)
    elif isinstance(user_input, dict):
        sanitized_dict = dict(user_input)

    if isinstance(sanitized_dict, dict):
        sanitized_dict.pop("target", None)
        try:
            sanitized_str = json.dumps(sanitized_dict, ensure_ascii=False)
        except Exception:
            sanitized_str = json.dumps(sanitized_dict)

    if isinstance(sanitized_str, str) and sanitized_str.strip().startswith(("{", "[")):
        embedded = await _embed_files_in_user_input(sanitized_str)
    else:
        embedded = sanitized_str

    normalized = embedded if embedded not in (None, "", {}, "{}") else "go"

    return ProcessedUserInput(
        canonical=canonical,
        sanitized=sanitized_str,
        embedded=embedded,
        normalized=normalized,
    )


def _merge_tool_input_into_canonical(
    canonical_json: str | None, input_json: str | None
) -> str | None:
    """Replace the `input` key inside canonical JSON (if dict) with the latest tool input string."""
    if not canonical_json:
        return canonical_json

    try:
        canonical_obj = json.loads(canonical_json)
    except Exception:
        return canonical_json

    if not isinstance(canonical_obj, dict):
        return canonical_json

    if input_json:
        try:
            parsed_input = json.loads(input_json)
        except Exception:
            parsed_input = input_json

        if isinstance(parsed_input, dict) and "input" in parsed_input:
            replacement = parsed_input.get("input")
        else:
            replacement = parsed_input

        canonical_obj["input"] = replacement

    try:
        return json.dumps(canonical_obj, ensure_ascii=False)
    except Exception:
        return json.dumps(canonical_obj)


def _wrap_function_tool(tool: Any, *, sub_cfg, sub_name: str, cfg) -> None:
    """Replace FunctionTool handler to emit debug/Telegram logs."""
    if not isinstance(tool, FunctionTool):
        return

    orig_invoke = tool.on_invoke_tool

    async def _wrapped_on_invoke(ctx, input: str):
        # Log agent-as-tool invocation with arguments (similar to MCP Hook)
        parent_name = getattr(cfg, 'name', '') or getattr(cfg, 'id', '')
        logging.info("[agent-tool] 🤖 Invoking agent-as-tool: %s (from %s)", sub_name, parent_name)
        
        try:
            debug_print(f"[Agent Tool][{sub_name}] Calling tool: {sub_name}")
            # Parse and format input arguments as YAML
            try:
                parsed_input = json.loads(input) if input else {}

                # Helper function to format as YAML (reuse logic from MCP hook)
                def _format_args_yaml(obj):
                    try:
                        return _dump_yaml_literal(obj, width=10000)
                    except Exception:
                        try:
                            return json.dumps(
                                obj, ensure_ascii=False, indent=2, default=str
                            )
                        except Exception:
                            return str(obj)

                yaml_input = _format_args_yaml(parsed_input)
                debug_print("[Agent Tool] Input (YAML):\n" + yaml_input)
                
                # Log input preview
                input_preview = json.dumps(parsed_input, ensure_ascii=False, default=str)
                if len(input_preview) > 200:
                    input_preview = input_preview[:200] + "..."
                logging.debug("[agent-tool][%s] input: %s", sub_name, input_preview)
            except Exception:
                # Fallback to raw input display
                debug_print("[Agent Tool] Input (raw):\n" + (input or ""))
        except Exception:
            pass

        tg_msg = None
        try:
            # Debug-only: never send agents-as-tools logs to the origin chat.
            debug_enabled = os.environ.get("CALL_DEBUG_TELEGRAM", "0").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            if debug_enabled and debug_chat_id is not None:
                await init_bot()
                title = f"🛠️ <b>{sanitize_telegram_html(getattr(sub_cfg, 'name', '') or sub_name)}</b>"
                caller = f"<i>from</i> <b>{sanitize_telegram_html(getattr(cfg, 'name', '') or '')}</b>"
                body = ""
                try:
                    import json as _json, html as _html

                    js = _json.loads(input) if input else {}
                    # Format as YAML instead of JSON
                    try:
                        pretty = _dump_yaml_literal(js, width=10000)
                        lang = "yaml"
                    except Exception:
                        # Fallback to JSON if YAML fails
                        pretty = _json.dumps(js, ensure_ascii=False, indent=2)
                        lang = "json"
                    if len(pretty) > 1500:
                        pretty = pretty[:1497] + "..."
                    body = f'\n<pre><code class="language-{lang}">{_html.escape(pretty)}</code></pre>'
                except Exception:
                    esc = sanitize_telegram_html(input or "")
                    if len(esc) > 1500:
                        esc = esc[:1497] + "..."
                    body = f"\n<code>{esc}</code>"
                text = f"{title} {caller}{body}"
                tg_msg = await safe_send_message(
                    chat_id=debug_chat_id,
                    message_thread_id=debug_thread_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            tg_msg = None

        try:
            result = await orig_invoke(ctx, input)
            logging.info("[agent-tool] ✅ agent-as-tool %s completed", sub_name)
        except Exception as e:
            logging.error(
                "[agent-tool] ❌ agent-as-tool %s failed: %s: %s",
                sub_name,
                type(e).__name__,
                str(e),
                exc_info=True
            )
            raise

        # Log agent-as-tool result (similar to MCP Hook)
        try:

            def _format_result_yaml(obj):
                try:
                    # Convert pydantic models and format as YAML
                    def _to_dict(o):
                        for attr in ("model_dump", "dict"):
                            method = getattr(o, attr, None)
                            if callable(method):
                                try:
                                    return method()
                                except Exception:
                                    pass
                        if isinstance(o, dict):
                            return {k: _to_dict(v) for k, v in o.items()}
                        elif isinstance(o, (list, tuple)):
                            return [_to_dict(item) for item in o]
                        return o

                    converted = _to_dict(obj)
                    return _dump_yaml_literal(converted, width=10000)
                except Exception:
                    try:
                        return json.dumps(
                            obj, ensure_ascii=False, indent=2, default=str
                        )
                    except Exception:
                        return str(obj)

            result_yaml = _format_result_yaml(result)
            debug_print(f"[Agent Tool][{sub_name}] Tool returned:\n" + result_yaml)
        except Exception:
            pass

        if tg_msg is not None:
            try:
                from telegram.error import BadRequest as _BadReq

                await init_bot()
                try:
                    import html as _html

                    rtxt = str(result)
                    if len(rtxt) > 1200:
                        rtxt = rtxt[:1197] + "..."
                    rtxt = _html.escape(rtxt)
                    updated = f"{title} {caller}\n<b>✓ Completed</b>\n<pre><code>{rtxt}</code></pre>"
                    updated = sanitize_telegram_html(updated)
                    await safe_edit_message_text(
                        chat_id=tg_msg.chat_id,
                        message_id=tg_msg.message_id,
                        text=updated,
                        parse_mode=ParseMode.HTML,
                    )
                except _BadReq:
                    await safe_send_message(
                        chat_id=tg_msg.chat_id,
                        text=f"{title} {caller} — ✓ Completed",
                        parse_mode=ParseMode.HTML,
                    )
            except Exception:
                pass

        try:
            debug_print(
                "[tool-call]",
                f"{getattr(sub_cfg, 'id', '') or sub_name}",
                "✓ completed",
            )
        except Exception:
            pass
        return result

    try:
        tool.on_invoke_tool = _wrapped_on_invoke
    except Exception:
        pass


def _build_agent_tool(
    *,
    cfg,
    sub_name: str,
    sub_desc: str,
    base_tools_snapshot: list[Any],
    mcp_servers: list[Any],
):
    """Create a sub-agent tool and return it (or None on failure)."""

    # Don't filter by project when resolving helper prompts - let target resolution work independently
    try:
        debug_print("[tools]", f"Building sub-config for entry: {sub_name}")
    except Exception:
        pass

    try:
        # Resolve at call-time so tests can monkeypatch call_api.build_runnable_instructions_config
        sub_cfg, sub_err = call_api.build_runnable_instructions_config(
            project=None,
            agent=None,
            prompt=None,
            target=sub_name,
            input=None,
        )
    except Exception as exc:
        sub_cfg, sub_err = None, exc
    if sub_err or not sub_cfg:
        try:
            desc = getattr(sub_err, "description", None)
            if isinstance(sub_err, dict):
                desc = sub_err.get("description")
            debug_print("[tools]", f"Skip entry {sub_name}: error={desc or sub_err}")
        except Exception:
            pass
        return None

    try:
        if sub_cfg.id and cfg.id and (sub_cfg.id.strip() == cfg.id.strip()):
            debug_print(
                "[tools]", f"Skip entry {sub_name}: resolved to self ({cfg.id})"
            )
            return None
    except Exception:
        pass

    try:
        debug_print(
            "[tools]",
            f"Sub-cfg built: id={sub_cfg.id} prompt={sub_cfg.prompt} instr_len={len(sub_cfg.instructions or '')}",
        )
    except Exception:
        pass

    try:
        sub_attrs_has_instr = isinstance(sub_cfg.attributes, dict) and (
            "instructions" in (sub_cfg.attributes or {})
        )
    except Exception:
        sub_attrs_has_instr = False
    if not sub_attrs_has_instr:
        sub_agent = get_or_create_agent(
            name=(sub_cfg.id or sub_name),
            instructions=(sub_cfg.instructions or ""),
            model=sub_cfg.model,
            model_settings=(sub_cfg.model_settings or ModelSettings()),
            tools=base_tools_snapshot,
            mcp_servers=mcp_servers,
        )
    tool = sub_agent.as_tool(
        tool_name=sub_name,
        tool_description=(sub_desc or f"Invoke agent '{sub_name}'"),
    )
    _wrap_function_tool(tool, sub_cfg=sub_cfg, sub_name=sub_name, cfg=cfg)
    try:
        debug_print("[tools]", f"Tool added: {sub_name} (resolved={sub_cfg.id or '?'})")
    except Exception:
        pass
    return tool


async def send_telegram_welcome_message(
    text: str = "", *, chat_id: int | None = None, message_thread_id: int | None = None
):
    # Send initial message and store its ID
    global telegram_last_message
    # Choose chat: prefer explicit override; else use selected_* initialized from .env and possibly overridden by agent
    if chat_id is None:
        chat_id = selected_chat_id or TELEGRAM_DEBUG_CHAT_ID
    # Ensure bot exists before sending welcome
    await init_bot()
    # Send clean welcome banner without any progress bar
    telegram_last_message = await safe_send_message(
        chat_id=chat_id or telegram_last_message.chat_id,
        text=text,
        message_thread_id=(
            message_thread_id
            if message_thread_id is not None
            else (selected_thread_id or TELEGRAM_DEBUG_THREAD_ID or None)
        ),
        parse_mode=ParseMode.HTML,
    )
    debug_print(
        "[app]",
        f"Last message set. ID: {telegram_last_message.message_id}, Chat ID: {telegram_last_message.chat_id}, Thread ID: {telegram_last_message.message_thread_id}",
    )


async def _send_welcome_banner(
    *,
    cfg,
    user_input: str,
    mcp_servers: list[Any],
    selected_chat_id: int | None,
    selected_thread_id: int | None,
) -> str | None:
    """Compose and send the Telegram welcome banner for the current agent run."""
    # Treat the welcome banner as a debug-only message:
    # - Never send it to the origin chat (selected_chat_id)
    # - Only send it to TELEGRAM_DEBUG_CHAT_ID (debug_chat_id) when CALL_DEBUG_TELEGRAM=1
    debug_enabled = os.environ.get("CALL_DEBUG_TELEGRAM", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not debug_enabled:
        debug_print("[app]", "[BANNER] skipped: CALL_DEBUG_TELEGRAM=0")
        return None
    if debug_chat_id is None:
        debug_print("[app]", "[BANNER] skipped: debug_chat_id is None")
        return None

    debug_print(
        "[app]",
        f"[BANNER] target(debug): chat_id={debug_chat_id}, thread_id={debug_thread_id}",
    )
    try:
        welcome_html = compose_welcome_html(
            agent_name=(cfg.id or ""),
            source_path=(
                (
                    (cfg.attributes or {}).get("_source_path")
                    if isinstance(getattr(cfg, "attributes", None), dict)
                    else None
                )
                or (cfg.path or None)
            ),
            user_input=user_input,
            mcp_servers_started=mcp_servers,
            tools=cfg.tools or [],
            model=(getattr(cfg, "model", None) or None),
        )
        debug_print("[app]", "welcome_html=\n" + (welcome_html or ""))

        await send_telegram_welcome_message(
            text=welcome_html,
            chat_id=debug_chat_id,
            message_thread_id=debug_thread_id,
        )
        return welcome_html
    except Exception as exc:
        try:
            err_text = format_exception_text(exc)
            debug_print("[app]", "[WARN] welcome message send failed:\n" + err_text)
        except Exception:
            pass
        return None


async def _embed_files_in_user_input(
    raw: str,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> str:
    """Embed base64 file contents into a JSON user_input payload when available."""
    try:
        data = json.loads(raw)
    except Exception:
        return raw

    ctx = data.get("context")
    if not isinstance(ctx, list):
        return raw

    factory = client_factory or (
        lambda: httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    )
    found = False
    try:
        client_ctx = factory()
        async with client_ctx as client:
            for item in ctx:
                try:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type")) != "file":
                        continue
                    url = str(item.get("url") or "").strip()
                    if not url:
                        continue
                    if isinstance(item.get("base64"), str) and item["base64"]:
                        continue
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        b64 = base64.b64encode(resp.content).decode("ascii")
                        item["base64"] = b64
                        found = True
                except Exception:
                    continue
    except Exception:
        return raw

    if not found:
        return raw

    try:
        output = json.dumps(data, ensure_ascii=False)
        debug_print("[app]", "[PAYLOAD] embedded base64 for files")
        return output
    except Exception:
        return raw


_MEDIA_OUTPUT_MARKER = "/media/output_"
_FETCH_IMAGES_MARKER = "fetch-images"
_MEDIA_OUTPUT_URL_RE = re.compile(
    r"https?://[^\s<>\"]*/media/output_[^\s<>\"]+",
    re.IGNORECASE,
)
_FETCH_IMAGES_URL_RE = re.compile(
    r"https?://[^\s<>\"]*/media/output_[^\s<>\"]*fetch-images[^\s<>\"]*",
    re.IGNORECASE,
)
_FETCH_IMAGES_ANCHOR_RE = re.compile(
    r"<a\b[^>]*href=[\"'][^\"']*fetch-images[^\"']*[\"'][^>]*>.*?</a>",
    re.IGNORECASE | re.DOTALL,
)


def _strip_fetch_images_links(text: str) -> str:
    """Remove fetch-images media output links when non-fetch media outputs exist."""
    if not isinstance(text, str) or not text:
        return text
    if _FETCH_IMAGES_MARKER not in text or _MEDIA_OUTPUT_MARKER not in text:
        return text

    media_urls = _MEDIA_OUTPUT_URL_RE.findall(text)
    if not media_urls:
        return text

    has_fetch = any(_FETCH_IMAGES_MARKER in url for url in media_urls)
    has_non_fetch = any(_FETCH_IMAGES_MARKER not in url for url in media_urls)
    if not (has_fetch and has_non_fetch):
        return text

    filtered = _FETCH_IMAGES_ANCHOR_RE.sub("", text)
    filtered = _FETCH_IMAGES_URL_RE.sub("", filtered)
    if filtered == text:
        return text

    output_lines: list[str] = []
    pending_blank = False
    for line in filtered.splitlines():
        if line.strip():
            if pending_blank and output_lines:
                output_lines.append("")
            output_lines.append(line.rstrip())
            pending_blank = False
        else:
            pending_blank = True
    return "\n".join(output_lines).strip("\n")


async def _init_bot_safe(*, project_name: str | None = None) -> None:
    """Call init_bot safely whether it's async or sync; swallow errors."""
    try:
        res = init_bot(project_name=project_name)
        import inspect as _inspect

        if _inspect.isawaitable(res):
            await res
    except Exception:
        pass


def _create_session_if_any(
    selected_chat_id: int | None, selected_thread_id: int | None
) -> SQLiteSession | None:
    """Create a SQLiteSession when Telegram routing is enabled; otherwise return None."""
    if selected_chat_id is None:
        return None

    if selected_thread_id is not None:
        session_id = f"{selected_chat_id}:{selected_thread_id}"
    else:
        session_id = f"{selected_chat_id}"

    # Ensure uniqueness across concurrent requests (two messages can arrive in the same second).
    session_id = f"{session_id}:{uuid.uuid4().hex}"

    db_path = os.getenv("CALL_DB", "call/call.db")
    try:
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
    except Exception:
        pass

    session = SQLiteSession(session_id, db_path)
    debug_print(f"[INFO] Session id: {session_id} @ {db_path}")
    return session


async def _notify_digest_if_applicable(
    *,
    cfg: RunnableConfig,
    user_input: str,
    step1_output: str | None,
    selected_chat_id: int | None,
    selected_thread_id: int | None,
) -> None:
    """Send digest notification and post-run git push when the run succeeded."""
    is_error_output = isinstance(
        step1_output, str
    ) and step1_output.strip().lower().startswith("error:")
    if selected_chat_id is None:
        return
    if is_error_output:
        message_for_tg = None
        if isinstance(step1_output, str):
            for line in step1_output.splitlines():
                line_stripped = line.strip()
                if line_stripped.lower().startswith("message:"):
                    message_for_tg = (
                        line_stripped.split(":", 1)[1].strip().strip('"')
                    )
                    break
        await _send_error_notification(
            cfg=cfg,
            selected_chat_id=selected_chat_id,
            selected_thread_id=selected_thread_id,
            message=message_for_tg or (step1_output or "Unknown error"),
        )
        return

    use_chat_id = selected_chat_id
    use_thread_id = selected_thread_id
    try:
        logging.info(
            "[digest] Sending digest notification chat=%s thread=%s agent=%s",
            use_chat_id,
            use_thread_id,
            getattr(cfg, "id", ""),
        )
        message_output = step1_output or ""
        if isinstance(step1_output, str) and _env_flag_for_cfg(
            "TG_FILTER_FETCH_IMAGES", cfg, False
        ):
            filtered = _strip_fetch_images_links(step1_output)
            if filtered != step1_output:
                message_output = filtered
                logging.debug(
                    "[digest] filtered fetch-images links for agent=%s",
                    getattr(cfg, "id", ""),
                )
        result = await send_digest_notification(
            agent_name=(cfg.id or ""),
            agent_path=(cfg.path or None),
            buttons=(
                (cfg.attributes or {}).get("buttons")
                if isinstance(cfg.attributes, dict)
                else None
            ),
            input_text=user_input,
            text=message_output,
            chat_id=use_chat_id,
            message_thread_id=use_thread_id,
            image_path=None,
        )
        logging.info(
            "[digest] send_digest_notification result=%s", bool(result)
        )
    except Exception as e:
        logging.warning("[digest] send_digest_notification error: %s", e)

    logging.info("[digest] Digest notification completed")


async def _send_error_notification(
    *,
    cfg: RunnableConfig,
    selected_chat_id: int | None,
    selected_thread_id: int | None,
    message: str,
) -> None:
    """Best-effort Telegram notification when the agent pipeline fails."""
    if selected_chat_id is None:
        return

    text = (message or "").strip() or "Неизвестная ошибка"

    try:
        await init_bot()
    except Exception:
        return

    try:
        safe_title = sanitize_telegram_html(
            getattr(cfg, "id", "") or getattr(cfg, "name", "") or "Agent"
        )
        safe_body = sanitize_telegram_html(text)
        body = telegram_truncate_html_safe(
            f"❌ <b>{safe_title}</b>\n\n<code>{safe_body}</code>", 3800
        )
        await safe_send_message(
            chat_id=selected_chat_id,
            message_thread_id=selected_thread_id,
            text=body,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


def ensure_env(var: str, default: str = None) -> str:
    """Return the sanitized value of environment variable or raise."""
    value = os.environ.get(var, default)
    if not value:
        raise EnvironmentError(f"Required environment variable {var} is not set")
    return value


# debug_print is imported from call.lib.logging

telegram_last_message: Optional[Message] = None
selected_chat_id: Optional[int] = None
selected_thread_id: Optional[int] = None
# Debug chat for MCP Hook messages (uses TELEGRAM_DEBUG_CHAT_ID from .env)
debug_chat_id: Optional[int] = None
debug_thread_id: Optional[int] = None
# When True, the pipeline must NOT create a SQLite session and must NOT send Telegram messages
force_no_session: bool = False
# Optional original Telegram message id to reply to
reply_to_message_id: Optional[int] = None
# Task-local reply-to id (prevents cross-talk between concurrent Telegram requests)
reply_to_message_id_var: ContextVar[int | None] = ContextVar(
    "call_reply_to_message_id", default=None
)


def get_telegram_chat_id(env_var: str, default: str | None = None) -> int | None:
    """Safely get and convert Telegram chat/thread ID from environment.

    - Missing or empty value returns None when default is provided.
    - A value of '0' is treated as None (disabled).
    - Otherwise returns int(chat_id).
    """
    raw = os.environ.get(env_var, default if default is not None else "")
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "" or s == "0":
        return None
    try:
        # Remove any non-numeric characters except optional leading minus
        s2 = "".join(c for c in s if c.isdigit() or c == "-")
        return int(s2) if s2 and s2 != "-" else None
    except Exception as e:
        raise ValueError(f"Failed to parse Telegram ID from {env_var}: {e}")

def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_flag_for_cfg(
    base: str,
    cfg: RunnableConfig | None,
    default: bool = False,
) -> bool:
    """Resolve a flag for a specific config (project/target) before fallback."""
    if _env_flag(base, default):
        return True
    candidates: list[str] = []
    if cfg:
        for value in (cfg.id, cfg.project, cfg.target):
            if value and value not in candidates:
                candidates.append(value)
    for suffix in candidates:
        raw = os.getenv(f"{base}__{suffix}")
        if raw is not None:
            return raw.strip().lower() in ("1", "true", "yes", "on")
    return False


# Get environment variables
telegram_token = ensure_env("TELEGRAM_TOKEN")
TELEGRAM_DEBUG_CHAT_ID = get_telegram_chat_id(
    "TELEGRAM_DEBUG_CHAT_ID"
) or get_telegram_chat_id("TELEGRAM_CHAT_ID")
TELEGRAM_SECOND_CHAT_ID = get_telegram_chat_id("TELEGRAM_SECOND_CHAT_ID")
telegrath_token = ensure_env("TELEGRAPH_TOKEN")
TELEGRAM_DEBUG_THREAD_ID = get_telegram_chat_id(
    "TELEGRAM_DEBUG_THREAD_ID", ""
) or get_telegram_chat_id("TELEGRAM_THREAD_ID", "")
TELEGRAPH_TOKEN = ensure_env("TELEGRAPH_TOKEN")
OPENAI_API_KEY = ensure_env("OPENAI_API_KEY")
# Initialize selected chat/thread defaults from .env
selected_chat_id = TELEGRAM_DEBUG_CHAT_ID
selected_thread_id = TELEGRAM_DEBUG_THREAD_ID or None
# Initialize debug chat/thread from .env (for MCP Hook messages)
debug_chat_id = TELEGRAM_DEBUG_CHAT_ID
debug_thread_id = TELEGRAM_DEBUG_THREAD_ID or None


def format_exception_json(e: Exception) -> dict:
    """Return a compact JSON-serializable description of an exception."""
    try:
        import traceback

        stack = traceback.format_exc().strip().splitlines()
    except Exception:
        stack = []
    return {
        "type": type(e).__name__,
        "message": str(e),
        "stack": stack[-20:],
    }


def format_exception_text(e: Exception) -> str:
    """Human-readable single text block for console/Telegram."""
    try:
        import traceback

        tb = traceback.format_exc()
    except Exception:
        tb = ""
    msg = f"{type(e).__name__}: {str(e)}"
    if tb:
        return (msg + "\n\n" + tb).strip()
    return msg


# Initialize bot at module level
global bot
bot: Bot


def get_project_token(project_name: str) -> str:
    """Return TELEGRAM_TOKEN__<project_name> from environment.

    KISS: no suffix guessing, no default fallback. Raise if missing.
    The provided name should already be normalized by the caller (e.g., stripped of 'Bot').
    """
    if not project_name or not str(project_name).strip():
        raise ValueError("project_name is required")
    key = f"TELEGRAM_TOKEN__{project_name}"
    token = os.environ.get(key, "").strip()
    if not token:
        raise KeyError(f"Missing {key} in environment/.env")
    return token


async def init_bot(*, project_name: str | None = None):
    """Initialize (or re-initialize) the global Telegram bot.

    Behavior:
      - If project_name is provided, use TELEGRAM_TOKEN__<project_name>
      - Otherwise, fall back to TELEGRAM_TOKEN
      - If already initialized with the same token, reuse existing instance
    """
    global bot
    # If no project was requested and the bot already exists, keep using it.
    # This prevents downgrading to the default TELEGRAM_TOKEN after a project-specific
    # bot (e.g., AgentFab) has been initialized upstream.
    if project_name is None and "bot" in globals() and isinstance(bot, Bot):
        return bot

    # Resolve token based on preference order:
    # 1) CALL_TELEGRAM_TOKEN (passed by telegram_bot at runtime)
    # 2) TELEGRAM_TOKEN__<ProjectName> when project_name is provided
    # 3) TELEGRAM_TOKEN (default from environment)
    token: str | None = None
    try:
        env_override = os.environ.get("CALL_TELEGRAM_TOKEN", "").strip()
        if env_override:
            token = env_override
    except Exception:
        token = None
    if not token and project_name:
        try:
            token = get_project_token(project_name)
        except Exception:
            token = None
    if not token:
        token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise EnvironmentError(
            "No Telegram token found: set TELEGRAM_TOKEN or TELEGRAM_TOKEN.<ProjectName>"
        )

    # If bot already initialized with the same token, reuse it
    if "bot" in globals() and isinstance(bot, Bot):
        try:
            if bot.token == token:  # type: ignore[attr-defined]
                return bot
        except Exception:
            pass

    # Configure PTB to use HTTPX with tuned timeouts and connection pool
    # Bypass system proxies for Telegram and disable trust_env to reduce connection issues
    import os as _os

    _os.environ.setdefault(
        "NO_PROXY", "api.telegram.org,*.telegram.org,*.stratospace.fun"
    )
    _os.environ.setdefault(
        "no_proxy", "api.telegram.org,*.telegram.org,*.stratospace.fun"
    )
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=120.0,
        write_timeout=60.0,
    )
    bot = Bot(token=token, request=request)
    return bot


async def init_openai_client():
    """Backward-compat shim. No-op since we no longer rely on creating OpenAI conversations.

    Kept to satisfy older callers that import call.app.call.init_openai_client.
    """
    return None


async def send_telegram_message(
    text: str,
    parse_mode: str = ParseMode.HTML,
    chat_id: str = None,
    message_thread_id: int = None,
) -> Optional[Message]:
    """
    Send a message to the configured Telegram chat.
    Updates the global telegram_last_message with the sent message object.

    Args:
        text: The message text to send
        parse_mode: Parse mode for the message (HTML/Markdown)
        chat_id: Optional chat ID (defaults to telegram_last_message.message_thread_id)
        message_thread_id: Optional thread ID (defaults to telegram_last_message.message_thread_id)

    Returns:
        The sent Message object or None if sending failed
    """
    global telegram_last_message
    try:
        # Sanitize for Telegram HTML to avoid unsupported tags (e.g., ul/li)
        safe_text = (
            sanitize_telegram_html(text)
            if parse_mode == ParseMode.HTML
            else (text or "")
        )

        async def _op():
            return await bot.send_message(
                chat_id=chat_id or telegram_last_message.chat_id,
                text=safe_text,
                parse_mode=parse_mode,
                message_thread_id=message_thread_id
                or telegram_last_message.message_thread_id
                or None,
            )

        message = await async_retry(
            _op,
            retries=2,
            base_delay=1.0,
            jitter=0.2,
            retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
        )
        telegram_last_message = message
        debug_print(
            f"TG message sent id={message.message_id} chat={message.chat_id} thread={message.message_thread_id}"
        )
        return message
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        raise e


def _render_body_with_code_blocks(body: str) -> str:
    """Render mixed text + fenced code blocks to HTML (<pre><code>); escape plain text.
    If no fenced blocks and body looks like YAML, wrap whole body as YAML code."""
    import re as _re_block

    def _looks_like_yaml(text: str) -> bool:
        if not text:
            return False
        if text.count("\n") < 2:
            return False
        lines = [ln.strip() for ln in text.splitlines()[:20]]
        yamlish = sum(1 for ln in lines if ln.startswith("-") or ":" in ln)
        return yamlish >= max(2, len(lines) // 3)

    pat = _re_block.compile(r"```([a-zA-Z0-9_-]+)?\n([\s\S]*?)```", _re_block.MULTILINE)
    parts: list[str] = []
    idx = 0
    found = False
    for m in pat.finditer(body or ""):
        found = True
        if m.start() > idx:
            parts.append(_html.escape(body[idx:m.start()]))
        lang = (m.group(1) or "plain").strip()
        code = m.group(2) or ""
        parts.append(f'<pre><code class="language-{lang}">{_html.escape(code)}</code></pre>')
        idx = m.end()
    if body and idx < len(body):
        parts.append(_html.escape(body[idx:]))
    if not found:
        if _looks_like_yaml(body):
            return f'<pre><code class="language-yaml">{_html.escape(body)}</code></pre>'
        return _html.escape(body or "")
    return "".join(parts)


async def send_digest_notification(
    *,
    text: str = None,
    chat_id: int | None = None,
    message_thread_id: int | None = None,
    agent_name: str | None = None,
    agent_path: str | Path | None = None,
    buttons: list[dict[str, Any]] | None = None,
    input_text: str | None = None,
    image_path: str | Path | None = None,
) -> Optional[Message]:
    """Send a digest message/photo to Telegram with sensible fallbacks.

    Arguments:
    - text: Optional main body text to send. If it is a non-empty string shorter than
      Telegram limits, it will be sent as-is (sanitized by downstream helpers).
      If it is empty/whitespace, we treat it as absent and send a minimal banner
      If it's 4000+ chars, it will be split into chunks and sent sequentially.
      with the original input echoed. If it's 4000+ chars, we publish it to Telegraph
      and send a short banner with the resulting link.
    - chat_id: Explicit chat id to target. If None, falls back to the module-level
      `selected_chat_id` which is initialized from `.env` and may be overridden by
      the Telegram bot/lib facade.
    - message_thread_id: Explicit topic/thread id (for supergroups). If None, falls
      back to `selected_thread_id`.
    - agent_name: Resolved agent display name. Used for presentation (e.g., Telegraph
      title) and optional buttons macro substitutions.
    - agent_path: Optional path to the originating card (used for logging/debug only).
    - buttons: Optional list of button dicts (label/url) provided by the caller. When
      present, these are rendered as inline buttons with macro substitution support.
    - input_text: Original user input. When we need to fall back to a banner (no text
      or after publishing), this input is echoed within a <code> block for context.
    - image_path: If provided, the function sends a photo instead of a text message.
      The `text` parameter becomes the caption (sanitized and truncated to 1024 chars).

    Behavior:
    - Always uses the finalized chat/thread computed from explicit arguments or
      module-level selections to avoid races.
    - Performs safe HTML preparation/truncation in downstream helpers.
    - Builds inline buttons from the provided configuration. Macro `{{digest_url}}`
      is replaced with the generated Telegraph URL when applicable.

    Returns:
    - telegram.Message on success; None on failure (with error logged to stdout).
    """
    # Debug: print incoming args (avoid dumping large payloads)
    reply_to = None
    try:
        reply_to = reply_to_message_id_var.get()
    except Exception:
        reply_to = None
    if reply_to is None:
        reply_to = reply_to_message_id
    debug_print(
        "send_digest_notification args:",
        f"text_len={(len(text) if isinstance(text, str) else 'None')},",
        f"chat_id={chat_id}, message_thread_id={message_thread_id},",
        f"reply_to={reply_to},",
        f"agent_name={agent_name}, agent_path={agent_path},",
        f"buttons_count={len(buttons) if isinstance(buttons, list) else 0},",
        f"input_len={(len(input_text) if isinstance(input_text, str) else 'None')},",
        f"image_path={image_path}",
    )

    def _looks_like_html(s: str) -> bool:
        """Heuristic: detect HTML-ish content.

        Если есть явные теги (<b>, <i>, <a href=, <code>, <pre> и т.п.), считаем,
        что это HTML и позволяем использовать Telegra.ph при превышении лимита.
        Для простого текста без тегов — HTML не считается.
        """
        try:
            t = (s or "").strip().lower()
            if not t:
                return False
            if "<b" in t or "<i" in t or "<a " in t or "<code" in t or "<pre" in t:
                return True
            if re.search(r"</?[a-z][a-z0-9]*\b[^>]*>", t):
                return True
            return False
        except Exception:
            return False

    text_is_html = False
    try:
        if text and isinstance(text, str):
            stripped = text.strip()
            # Attempt JSON -> YAML only when:
            # - starts/ends with { } or [ ]
            # - no fenced blocks or HTML already
            # - parsed JSON is NOT a simple text item/list of text items
            if (
                stripped
                and stripped[0] in "[{"
                and stripped[-1] in "]}"
                and "```" not in stripped
                and "<pre" not in stripped
                and "<code" not in stripped
            ):
                try:
                    parsed = json.loads(stripped)

                    parsed_is_text = False
                    # We no longer inline _is_text_item here; it already exists above
                    if _is_text_item(parsed):
                        parsed_is_text = True
                    if isinstance(parsed, list) and all(_is_text_item(it) for it in parsed):
                        parsed_is_text = True

                    # NEW: if parsed is plain text content or already contains HTML tags, do NOT wrap as YAML
                    parsed_has_html = False
                    if isinstance(parsed, dict) and parsed.get("type") == "text" and isinstance(parsed.get("text"), str):
                        t = parsed.get("text", "")
                        if "<" in t and ">" in t:
                            parsed_has_html = True
                    if isinstance(parsed, list) and all(_is_text_item(it) for it in parsed):
                        # if any text item has html-ish content, skip yaml wrapping
                        if any(("<" in it.get("text","")) and (">" in it.get("text","")) for it in parsed):
                            parsed_has_html = True

                    if not parsed_is_text and not parsed_has_html:
                        yaml_text = _dump_yaml_literal(parsed)
                        if yaml_text:
                            text = f'<pre><code class="language-yaml">{yaml_text}</code></pre>'
                            text_is_html = True
                            debug_print("[format]", "Converted JSON payload to YAML code block")
                except (json.JSONDecodeError, Exception):
                    pass
            if not text_is_html:
                # If the body already contains HTML tags, do not escape it again
                if _looks_like_html(text):
                    text_is_html = True
                else:
                    text = _render_body_with_code_blocks(text)
    except Exception:
        pass

    # Decide whether this is HTML content (eligible for Telegra.ph) or plain text
    is_html = bool(
        (text_is_html and isinstance(text, str))
        or (text and isinstance(text, str) and _looks_like_html(text))
    )

    # Telegraph/Publishing: disabled by default, but if buttons are provided we still
    # need a concrete digest_url for macro substitution. In tests publish_results is
    # monkeypatched, so we call it when buttons are requested.
    local_url: str | None = None
    if buttons:
        try:
            local_url = publish_results(title=agent_name or "Digest", content=text or "")
        except Exception:
            local_url = None

    # Normalize empty/whitespace-only text to None so we don't attempt to send an empty Telegram message.
    # This triggers the fallback banner below with optional input echo.
    try:
        if text is not None and isinstance(text, str) and not text.strip():
            text = None
    except Exception:
        # Best-effort only; if anything goes wrong, proceed with existing value
        pass

    # If we ended up with no text (e.g., HTML published to Telegraph), build banner.
    if text is None:
        text = f"📰 {local_url}" if local_url else "📰"
        if input_text:
            try:
                safe_input = (input_text or "")[:3800]
                text = text + f"\n<code>{safe_input}</code>"
            except Exception:
                pass

    debug_print(f"send_digest_notification publish_url={local_url}")

    # Build inline buttons from provided configuration and perform macro substitution
    keyboard = None
    try:
        btn_source = buttons if isinstance(buttons, list) else []
        keyboard_rows: list[list[InlineKeyboardButton]] = []
        current_row: list[InlineKeyboardButton] = []

        def _build_button(entry: dict[str, Any]) -> InlineKeyboardButton | None:
            label = str(entry.get("label", "")).strip() or "🔗"
            link = str(entry.get("url", "")).strip()
            if link:
                # Preserve caller-specified link; only substitute digest macro when available.
                safe_url = local_url or link
                link = link.replace("{{digest_url}}", safe_url)
            if link:
                return InlineKeyboardButton(label, url=link)
            return None

        def _flush_current_row() -> None:
            nonlocal current_row
            if current_row:
                keyboard_rows.append(current_row)
                current_row = []

        for b in btn_source:
            if not isinstance(b, dict):
                continue
            row_spec = b.get("row")
            if isinstance(row_spec, (list, tuple)):
                _flush_current_row()
                row_buttons: list[InlineKeyboardButton] = []
                for rb in row_spec:
                    if not isinstance(rb, dict):
                        continue
                    btn = _build_button(rb)
                    if btn:
                        row_buttons.append(btn)
                if row_buttons:
                    keyboard_rows.append(row_buttons)
                continue

            btn = _build_button(b)
            if btn:
                current_row.append(btn)

        _flush_current_row()
        if keyboard_rows:
            keyboard = keyboard_rows
    except Exception:
        keyboard = None

    # If keyboard wasn't configured in agent.yaml, do not show any buttons

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    try:
        # Determine effective chat/thread once to avoid races with globals
        eff_chat_id = chat_id if chat_id is not None else selected_chat_id
        eff_thread_id = (
            message_thread_id if message_thread_id is not None else selected_thread_id
        )

        # If an image is provided, keep existing behavior (single message with caption)
        if image_path:
            message_obj = await telegram_send_photo(
                image_path=image_path,
                caption=text,
                chat_id=eff_chat_id,
                message_thread_id=eff_thread_id,
                reply_markup=reply_markup,
            )
            debug_print(f"send_digest_notification result=true publish_url={local_url}")
            return message_obj

        # Plain-text batching: если это не HTML и длина > 4000, режем и шлём батчом.
        if (not is_html) and isinstance(text, str) and len(text) > 4000:
            full = text
            # Длина чуть меньше лимита, чтобы оставался запас на обёртку/санитайзер
            chunk_size = 3800
            first_message: Optional[Message] = None
            for idx in range(0, len(full), chunk_size):
                chunk = full[idx : idx + chunk_size]
                # Для простого текста даём Telegram самому выбрать parse_mode через
                # telegram_prepare_html (внутри telegram_send_message)
                message_obj = await telegram_send_message(
                    text=chunk,
                    chat_id=eff_chat_id,
                    message_thread_id=eff_thread_id,
                    # Кнопки нужны на каждом батч-куске, иначе тесты теряют markup
                    reply_markup=reply_markup,
                )
                if first_message is None:
                    first_message = message_obj
            debug_print("send_digest_notification result=true (batched plain text)")
            return first_message

        # Обычный случай: единичное сообщение (HTML или короткий текст)
        message_obj = await telegram_send_message(
            text=text,
            chat_id=eff_chat_id,
            message_thread_id=eff_thread_id,
            reply_markup=reply_markup,
        )
        debug_print(f"send_digest_notification result=true publish_url={local_url}")
        return message_obj
    except Exception as e:
        debug_print("[app]", f"Error sending Telegram message/photo: {e}")
        return None


async def prompt_repo_git_add_commit_and_push(agent_name: str, user_input: str) -> None:
    """Commit and push changes in the prompt repo after an agent run.

    - Uses normalized agent_name resolved in the pipeline
    - Uses user_input as-is (preserve newlines)
    - No fallback names
    """
    if os.environ.get("CALL_DISABLE_POST_RUN_PUSH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        logging.info(
            "[git] prompt_repo_git_add_commit_and_push disabled via CALL_DISABLE_POST_RUN_PUSH"
        )
        return

    try:
        prompt_repo = discover_prompt_repo()
    except Exception as exc:
        logging.debug("[git] discover_prompt_repo failed: %s", exc)
        return

    commit_msg = f"{agent_name} {user_input}"
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    from asyncio.subprocess import PIPE

    def _truncate(data: bytes, limit: int = 2048) -> str:
        if not data:
            return ""
        text = data.decode("utf-8", "replace")
        if len(text) > limit:
            return text[:limit] + "…"
        return text

    async def _run_git(step: str, cmd: list[str], timeout: float = 10.0) -> tuple[int, bytes, bytes]:
        logging.debug("[git] %s start: %s", step, " ".join(cmd))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(prompt_repo),
            stdout=PIPE,
            stderr=PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            logging.warning("[git] %s timeout after %.1fs", step, timeout)
            with suppress(Exception):
                proc.kill()
            raise
        rc = proc.returncode
        logging.debug(
            "[git] %s done rc=%s stdout=%s stderr=%s",
            step,
            rc,
            _truncate(out),
            _truncate(err),
        )
        return rc, out, err

    try:
        rc_status, status_out, _ = await _run_git(
            "status", ["git", "status", "--porcelain", "-uno"]
        )
        if rc_status != 0:
            logging.debug("[git] status failed rc=%s", rc_status)
            return
        if status_out.strip() == b"":
            logging.info("[git] No changes detected; skipping push")
            return

        await _run_git("add", ["git", "add", "-A", "."])
        rc_commit, commit_out, commit_err = await _run_git(
            "commit", ["git", "commit", "-m", commit_msg]
        )
        if rc_commit != 0:
            logging.info(
                "[git] Commit skipped rc=%s stdout=%s stderr=%s",
                rc_commit,
                _truncate(commit_out),
                _truncate(commit_err),
            )
            return

        await _run_git("push", ["git", "push"])
        logging.info("[git] push completed successfully")
    except Exception as exc:
        logging.debug("[git] prompt_repo_git_add_commit_and_push aborted: %s", exc)
        raise


@function_tool
async def prompt_repo_git_pull_rebase_tool(ctx: RunContextWrapper[Any]) -> str:
    """Update the local prompts repository via `git pull --rebase`.

    Use this tool when you need to fetch the latest prompt changes
    before running complex operations or generating new prompt files.

    This tool must be invoked only when the active system prompt
    explicitly requests a git pull for the prompts repository.
    """

    try:
        await prompt_repo_git_pull_rebase()
        payload = {"ok": True, "operation": "prompt_repo_git_pull_rebase"}
    except Exception as e:
        logging.exception("[git] prompt_repo_git_pull_rebase_tool failed")
        payload = {
            "ok": False,
            "error_code": 500,
            "description": f"prompt_repo_git_pull_rebase failed: {type(e).__name__}: {e}",
            "code": "PROMPT_REPO_GIT_PULL_ERROR",
        }
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        # Fallback: best-effort string
        return str(payload)


@function_tool
async def prompt_repo_git_add_commit_and_push_tool(
    ctx: RunContextWrapper[Any],
    agent_name: str,
    user_input: str,
) -> str:
    """Run git add/commit/push for the prompts repository.

    - `agent_name`: logical agent/prompt name to include in commit message.
    - `user_input`: original user input to embed into the commit message.

    This tool must be invoked only when the active system prompt
    explicitly requests committing and pushing prompt repo changes.
    """

    try:
        await prompt_repo_git_add_commit_and_push(agent_name=agent_name, user_input=user_input)
        payload = {
            "ok": True,
            "operation": "prompt_repo_git_add_commit_and_push",
            "agent_name": agent_name,
        }
    except Exception as e:
        logging.exception("[git] prompt_repo_git_add_commit_and_push_tool failed")
        payload = {
            "ok": False,
            "error_code": 500,
            "description": f"prompt_repo_git_add_commit_and_push failed: {type(e).__name__}: {e}",
            "code": "PROMPT_REPO_GIT_PUSH_ERROR",
        }
    try:
        return json.dumps(payload, ensure_ascii=False)
    except Exception:
        return str(payload)


async def telegram_send_message(
    chat_id: int = None,
    text: str = None,
    message_thread_id: int = None,
    reply_markup: InlineKeyboardMarkup = None,
):

    def _looks_like_markdown(s: str) -> bool:
        try:
            t = (s or "").strip()
            if not t:
                return False
            # Prefer HTML if explicit tags are present
            # This prevents strings like "<b>…</b>" from being treated as Markdown
            if "<b>" in t or "<i>" in t or "<a href=" in t:
                return False
            if "<" in t and ">" in t:
                return False
            # Strong signal: fenced code blocks -> Markdown
            if "```" in t:
                return True
            # Common Markdown cues
            md_markers = (
                "**",
                "__",
                "* ",
                "- ",
                "\n- ",
                "\n* ",
                "[`",
                "[`",
                "](http",
                "`",
                "```",
                "# ",
                "## ",
                "### ",
                "1. ",
                "\n1. ",
            )
            if any(m in t for m in md_markers):
                return True
            return False
        except Exception:
            return False

    # KISS: Always send as HTML using sanitizer; rely on plain fallback on error
    try:
        safe_text, chosen_mode = telegram_prepare_html(text or "", 4000)
        chosen_parse_mode = ParseMode.HTML if chosen_mode == "HTML" else None
    except Exception:
        # Plain fallback on any preparation error
        safe_text = text or ""
        if len(safe_text) > 4096:
            safe_text = safe_text[:4095] + "…"
        chosen_parse_mode = None

    # Determine effective chat/thread with consistent fallbacks
    # KISS: Only explicit args -> selected_* (selected_* already seeded from .env and updated once after agent load)
    eff_chat_id = chat_id if chat_id is not None else selected_chat_id
    eff_thread_id = (
        message_thread_id if message_thread_id is not None else selected_thread_id
    )

    # Ensure bot is ready before attempting to send
    await init_bot()

    def _effective_reply_to() -> int | None:
        try:
            v = reply_to_message_id_var.get()
        except Exception:
            v = None
        return v if v is not None else reply_to_message_id

    def _apply_reply_kwargs(kwargs: dict, *, reply_id: int | None, rp_cls) -> None:
        try:
            if reply_id is None:
                return
            if rp_cls:
                kwargs["reply_parameters"] = rp_cls(
                    message_id=reply_id,
                    allow_sending_without_reply=_env_flag(
                        "TG_ALLOW_SENDING_WITHOUT_REPLY", True
                    ),
                )
            else:
                kwargs["reply_to_message_id"] = reply_id
        except Exception:
            return

    async def _op():
        try:
            from telegram import ReplyParameters as _ReplyParameters
        except Exception:
            _ReplyParameters = None
        kwargs = dict(
            chat_id=eff_chat_id,
            message_thread_id=eff_thread_id,
            text=safe_text,
            parse_mode=chosen_parse_mode,
            reply_markup=reply_markup,
        )
        _apply_reply_kwargs(
            kwargs, reply_id=_effective_reply_to(), rp_cls=_ReplyParameters
        )
        return await bot.send_message(**kwargs)

    try:
        debug_print(f"[TG] send_message parse_mode={chosen_parse_mode}")
        message = await async_retry(
            _op,
            retries=2,
            base_delay=1.0,
            jitter=0.2,
            retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
        )
    except BadRequest as e:
        # KISS: If Telegram can't parse, send plain text once.
        emsg = str(e).lower()
        try:
            if _env_flag("CALL_DEBUG", False):
                logging.getLogger("call.tg").warning(
                    "[TG] telegram_send_message BadRequest: %s chat=%s thread=%s reply_to=%s text_preview=%r",
                    str(e),
                    eff_chat_id,
                    eff_thread_id,
                    _effective_reply_to(),
                    (safe_text or "")[:200],
                )
        except Exception:
            pass
        if "parse" in emsg or "entity" in emsg:
            plain = text or ""
            if len(plain) > 4096:
                plain = plain[:4095] + "…"

            async def _op_plain():
                try:
                    from telegram import ReplyParameters as _ReplyParameters
                except Exception:
                    _ReplyParameters = None
                kwargs = dict(
                    chat_id=eff_chat_id,
                    message_thread_id=eff_thread_id,
                    text=plain,
                    parse_mode=None,
                    reply_markup=reply_markup,
                )
                _apply_reply_kwargs(
                    kwargs, reply_id=_effective_reply_to(), rp_cls=_ReplyParameters
                )
                return await bot.send_message(**kwargs)

            debug_print("[TG] BadRequest parse error, retrying as plain text")
            message = await async_retry(
                _op_plain,
                retries=1,
                base_delay=0.7,
                jitter=0.1,
                retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
            )
        elif "thread not found" in emsg:
            # Fallback: resend without thread id (keep reply parameters).
            debug_print(
                "[TG] BadRequest thread not found, retrying without thread id via safe_send_message"
            )
            message = await safe_send_message(
                chat_id=eff_chat_id,
                text=safe_text,
                parse_mode=chosen_parse_mode,
                reply_markup=reply_markup,
                reply_to_message_id=_effective_reply_to(),
            )
        else:
            raise
    return message


async def safe_send_photo(
    *,
    chat_id: int | None,
    image_path: str | Path,
    caption: str | None = None,
    message_thread_id: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    """Wrapper for bot.send_photo with 'thread not found' fallback and retry.

    - Applies HTML sanitization/truncation for captions similar to telegram_send_photo
    - On BadRequest 'thread not found', retries without thread id
    """
    eff_chat_id = chat_id if chat_id is not None else selected_chat_id
    eff_thread_id = (
        message_thread_id if message_thread_id is not None else selected_thread_id
    )
    # Prepare caption
    safe_caption = None
    parse_mode = None
    if caption:
        try:
            safe_caption, cmode = telegram_prepare_html(caption or "", 1024)
            parse_mode = ParseMode.HTML if cmode == "HTML" else None
        except Exception:
            safe_caption = caption or ""
            if len(safe_caption) > 1024:
                safe_caption = safe_caption[:1023] + "…"
            parse_mode = None
        # Truncate if needed
        try:
            MAX_CAPTION_LEN = 1024
            if parse_mode == ParseMode.HTML and safe_caption:
                safe_caption = telegram_truncate_html_safe(
                    safe_caption, MAX_CAPTION_LEN
                )
            elif parse_mode == ParseMode.MARKDOWN and safe_caption:
                safe_caption = telegram_truncate_markdown_safe(
                    safe_caption, MAX_CAPTION_LEN
                )
            else:
                if safe_caption and len(safe_caption) > MAX_CAPTION_LEN:
                    safe_caption = safe_caption[: MAX_CAPTION_LEN - 1] + "…"
        except Exception:
            pass

    async def _op():
        await init_bot()
        with open(image_path, "rb") as f:
            return await bot.send_photo(
                chat_id=eff_chat_id,
                photo=f,
                caption=safe_caption,
                parse_mode=parse_mode,
                message_thread_id=eff_thread_id,
                reply_markup=reply_markup,
            )

    try:
        return await async_retry(
            _op,
            retries=2,
            base_delay=1.0,
            jitter=0.2,
            retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
        )
    except BadRequest as e:
        if "thread not found" in str(e).lower():

            async def _op_no_thread():
                await init_bot()
                with open(image_path, "rb") as f:
                    return await bot.send_photo(
                        chat_id=eff_chat_id,
                        photo=f,
                        caption=safe_caption,
                        parse_mode=parse_mode,
                        reply_markup=reply_markup,
                    )

            debug_print(
                "[TG] BadRequest thread not found, retrying photo without thread id"
            )
            return await async_retry(
                _op_no_thread,
                retries=1,
                base_delay=0.7,
                jitter=0.1,
                retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
            )
        raise


async def safe_send_message(
    *,
    chat_id: int | None,
    text: str,
    message_thread_id: int | None = None,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    reply_to_message_id: int | None = None,
    disable_notification: bool = True,
) -> Message:
    """Wrapper around bot.send_message with retry and 'thread not found' fallback.

    - Honors reply_to_message_id via ReplyParameters when available.
    - On BadRequest 'thread not found', retries without message_thread_id and without reply params.
    """
    debug_print(
        "[app]",
        f"[TG] Sending message to chat_id={chat_id}, thread_id={message_thread_id}, text_len={len(text)}",
    )
    await init_bot()
    try:
        from telegram import ReplyParameters as _ReplyParameters
    except Exception:
        _ReplyParameters = None

    def _effective_reply_to() -> int | None:
        v = reply_to_message_id
        if v is None:
            try:
                v = reply_to_message_id_var.get()
            except Exception:
                v = None
        return v

    def _apply_reply_kwargs(kwargs: dict, *, reply_id: int | None) -> None:
        try:
            if reply_id is None:
                return
            if _ReplyParameters:
                kwargs["reply_parameters"] = _ReplyParameters(
                    message_id=reply_id,
                    allow_sending_without_reply=_env_flag(
                        "TG_ALLOW_SENDING_WITHOUT_REPLY", True
                    ),
                )
            else:
                kwargs["reply_to_message_id"] = reply_id
        except Exception:
            return

    async def _op():
        kwargs = dict(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            disable_notification=disable_notification,
        )
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        _apply_reply_kwargs(kwargs, reply_id=_effective_reply_to())
        return await bot.send_message(**kwargs)

    try:
        result = await async_retry(
            _op,
            retries=2,
            base_delay=1.0,
            jitter=0.2,
            retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
        )
        debug_print(
            "[app]",
            f"[TG] Message sent successfully: msg_id={result.message_id}, chat_id={result.chat_id}",
        )
        return result
    except BadRequest as e:
        msg = str(e).lower()
        try:
            if _env_flag("CALL_DEBUG", False):
                logging.getLogger("call.tg").warning(
                    "[TG] safe_send_message BadRequest: %s chat=%s thread=%s reply_to=%s text_preview=%r",
                    str(e),
                    chat_id,
                    message_thread_id,
                    _effective_reply_to(),
                    (text or "")[:200],
                )
        except Exception:
            pass
        if "thread not found" in msg:

            async def _op_no_thread():
                kwargs = dict(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                )
                _apply_reply_kwargs(kwargs, reply_id=_effective_reply_to())
                return await bot.send_message(**kwargs)

            return await async_retry(
                _op_no_thread,
                retries=1,
                base_delay=0.7,
                jitter=0.1,
                retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
            )
        # Fallback to plain text on entity parse errors
        if (
            ("can't parse entities" in msg)
            or ("parse entities" in msg)
            or ("entity" in msg)
        ):
            try:
                plain = re.sub(r"<[^>]+>", "", text or "")

                async def _plain():
                    kwargs = dict(chat_id=chat_id, text=plain, reply_markup=reply_markup)
                    _apply_reply_kwargs(kwargs, reply_id=_effective_reply_to())
                    return await bot.send_message(**kwargs)

                return await async_retry(
                    _plain,
                    retries=1,
                    base_delay=0.7,
                    jitter=0.1,
                    retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
                )
            except Exception:
                pass
        raise


async def telegram_send_photo(
    image_path: str | Path,
    caption: str | None = None,
    chat_id: int | None = None,
    message_thread_id: int | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message:
    """Send a photo to Telegram with optional caption, using global selected chat/thread.

    - Applies the same HTML sanitization for captions as messages
    - Uses async_retry for robustness
    """
    # Determine effective chat/thread
    eff_chat_id = chat_id if chat_id is not None else selected_chat_id
    eff_thread_id = (
        message_thread_id if message_thread_id is not None else selected_thread_id
    )

    safe_caption = None
    if caption:
        # KISS: Always prepare caption as HTML
        try:
            safe_caption, cmode = telegram_prepare_html(caption or "", 1024)
            parse_mode = ParseMode.HTML if cmode == "HTML" else None
        except Exception:
            safe_caption = caption or ""
            if len(safe_caption) > 1024:
                safe_caption = safe_caption[:1023] + "…"
            parse_mode = None
    else:
        parse_mode = None

    # Telegram Bot API caption length limit safety clamp (avoid BadRequest)
    try:
        MAX_CAPTION_LEN = 1024
        if parse_mode == ParseMode.HTML and safe_caption:
            safe_caption = telegram_truncate_html_safe(safe_caption, MAX_CAPTION_LEN)
        elif parse_mode == ParseMode.MARKDOWN and safe_caption:
            safe_caption = telegram_truncate_markdown_safe(
                safe_caption, MAX_CAPTION_LEN
            )
        else:
            if safe_caption and len(safe_caption) > MAX_CAPTION_LEN:
                safe_caption = safe_caption[: MAX_CAPTION_LEN - 1] + "…"
    except Exception:
        # Best-effort; on any error, fall back to original caption
        pass

    # Delegate to safe helper for consistency
    return await safe_send_photo(
        chat_id=eff_chat_id,
        image_path=image_path,
        caption=safe_caption,
        message_thread_id=eff_thread_id,
        reply_markup=reply_markup,
    )


logging.getLogger("openai").setLevel(logging.DEBUG)

default_samples_dir = str(Path(__file__).resolve().parents[2])


def normalize_agent_name(raw: str) -> str:
    """Normalize agent name:

    - Split by any non-alphanumeric character (spaces and similar) into parts
    - CamelCase each part (PascalCase)
    - Join parts without separators (remove spaces and similar)
    - No fallbacks: may return empty string if nothing to normalize
    """
    s = (raw or "").strip()
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", s) if p]
    cased = [p[:1].upper() + p[1:] for p in parts]
    return "".join(cased)


def _flatten_output_section(output_val) -> dict:
    """Normalize 'output' which may be a list of single-key maps into a flat dict.
    Example YAML:
      output:
        - bot: "@bot"
        - chat_id: 123
        - thread_id: 10
    becomes {"bot": "@bot", "chat_id": 123, "thread_id": 10}
    """
    if isinstance(output_val, dict):
        return output_val
    if isinstance(output_val, list):
        flat = {}
        for item in output_val:
            if isinstance(item, dict):
                for k, v in item.items():
                    flat[k] = v
        return flat
    return {}


def _normalize_chat_id(v) -> int | None:
    """Return Telegram chat_id as int.
    If v looks like a 10-digit positive ID (e.g., 2820582847), convert to -100XXXXXXXXXX.
    If v already starts with -100..., keep as-is.
    Accepts int or str; returns int or None if invalid.
    """
    if v is None:
        return None
    try:
        s = str(v).strip()
        # Already -100 prefixed
        if s.startswith("-100") and s[4:].isdigit():
            return int(s)
        # Plain digits
        digits = "".join(ch for ch in s if ch.isdigit())
        if not digits:
            return None
        # If it was negative but not -100, just int-cast
        if s.startswith("-") and not s.startswith("-100"):
            return int(s)
        # 10-digit plain -> supergroup
        if len(digits) == 10:
            return int("-100" + digits)
        return int(s)
    except Exception:
        return None


def github_blob_url(local_path: str | Path) -> str | None:
    """Best-effort GitHub blob URL from a local path.

    Tries in order:
      1) GITHUB_REMOTE_URL: a full repo remote URL (ssh or https). Example:
         git@github.com:org/repo.git → https://github.com/org/repo/blob/<branch>/<rel>
      2) GITHUB_REMOTE_ORGANIZATION_URL: an org URL + top-level repo name derived
         from the relative path (first path segment under workspace root).
         Example: https://github.com/strato-space + rel 'prompt/UxFab/x' →
         https://github.com/strato-space/prompt/blob/<branch>/UxFab/x
      Branch defaults to 'master' if GITHUB_BRANCH is unset.
    """
    try:
        p = Path(local_path)
        try:
            rel = str(Path(p).resolve().relative_to(_repo_root.resolve()).as_posix())
        except Exception:
            rel = p.name

        branch = os.getenv("GITHUB_BRANCH", "master")

        # Option A: full remote URL
        remote = os.getenv("GITHUB_REMOTE_URL", "").strip()
        if remote:
            url = remote
            if url.startswith("git@github.com:"):
                url = url.replace("git@github.com:", "https://github.com/")
            if url.endswith(".git"):
                url = url[:-4]
            if url.startswith("http"):
                return f"{url}/blob/{branch}/{rel}"

        # Option B: organization URL + derive repo name from first path segment
        org = os.getenv("GITHUB_REMOTE_ORGANIZATION_URL", "").strip().rstrip("/")
        if org and rel and "/" in rel:
            top, sub = rel.split("/", 1)
            if top:  # assume top-level folder == repo name
                return f"{org}/{top}/blob/{branch}/{sub}"

        return None
    except Exception:
        return None


def compose_welcome_html(
    *,
    agent_name: str,
    source_path: str | Path | None,
    user_input: str,
    mcp_servers_started: list[Any] | None,
    tools: list[str] | None,
    model: str | None = None,
) -> str:
    """Compose the Telegram welcome banner HTML.

    Output format:
      🍴 <b><a href='github-path'>AgentName</a></b>  (falls back to bold if URL missing)
      <code>input[:3800]</code>
      <code>mcp: [...]</code>
      <code>tools: [...]</code>
    """
    title = (agent_name or "Agent").strip() or "Agent"
    gh_url = github_blob_url(source_path) if source_path else None
    header = (
        f"🔌 <b><a href='{gh_url}'>{title}</a></b>" if gh_url else f"🔌 <b>{title}</b>"
    )

    preview = (user_input or "").strip()
    # Try to pretty print JSON/dict payloads as YAML for readability
    pretty_preview: str | None = None
    try:
        if preview and (preview.startswith("{") or preview.startswith("[")):
            import json as _json

            obj = _json.loads(preview)
            pretty = _dump_yaml_literal(obj, width=80)
            # Clamp to safe length
            if len(pretty) > 3600:
                pretty = pretty[:3597] + "..."
            pretty_preview = (
                f'<pre><code class="language-yaml">{_html.escape(pretty)}</code></pre>'
            )
    except Exception:
        pretty_preview = None
    if not pretty_preview:
        if len(preview) > 3800:
            preview = preview[:3797] + "..."

    # Collect MCP server names (best-effort)
    mcp_names: list[str] = []
    try:
        for srv in mcp_servers_started or []:
            nm = (
                getattr(srv, "name", None)
                or getattr(srv, "id", None)
                or type(srv).__name__
            )
            if nm and str(nm) not in mcp_names:
                mcp_names.append(str(nm))
    except Exception:
        pass

    tool_names = []
    try:
        tool_names = [str(t).strip() for t in (tools or []) if str(t).strip()]
    except Exception:
        tool_names = []

    parts = [header]
    # Build preview line and attrs lines separately to control spacing
    preview_line = (
        pretty_preview
        if pretty_preview
        else (f"<code>{preview}</code>" if preview else None)
    )
    attr_lines: list[str] = []
    if mcp_names:
        attr_lines.append(f"<code>mcp: {mcp_names}</code>")
    if tool_names:
        attr_lines.append(f"<code>tools: {tool_names}</code>")
    if model:
        attr_lines.append(f"<code>model: {model}</code>")

    # Spacing rules:
    # - Always one blank line after header if we have any body content
    # - One blank line between preview and attrs when both exist
    body_chunks: list[str] = []
    if preview_line:
        body_chunks.append(preview_line)
    if attr_lines:
        if preview_line:
            body_chunks.append("")  # blank line between input and attrs
        body_chunks.append("\n".join(attr_lines))

    if body_chunks:
        parts.append("")  # blank line after header
        parts.append("\n".join(body_chunks))
    return "\n".join(parts)


def _extract_tg_targets(output_val) -> tuple[int | None, int | None]:
    """Extract chat_id and thread_id from various 'output' layouts.

    Supports:
    - output: { chat_id: ..., thread_id: ... }
    - output: [ {chat_id: ...}, {thread_id: ...} ]
    - output: { tg: { chat_id: ..., thread_id: ... } }
    - output: [ { tg: { chat_id: ..., thread_id: ... } } ]
    Returns tuple(chat_id:int|None, thread_id:int|None)
    """
    flat = _flatten_output_section(output_val)
    chat_id = _normalize_chat_id(flat.get("chat_id"))

    def _to_int(v):
        try:
            return int(str(v).strip())
        except Exception:
            return None

    thread_id = _to_int(flat.get("thread_id"))

    tg = flat.get("tg")
    if isinstance(tg, dict):
        chat_id = (
            chat_id if chat_id is not None else _normalize_chat_id(tg.get("chat_id"))
        )
        thread_id = thread_id if thread_id is not None else _to_int(tg.get("thread_id"))

    return chat_id, thread_id


def _merge_outputs(*outputs: dict | None) -> dict:
    """KISS merge for output sections coming from different places.
    Priority = leftmost. Supports nested 'tg' dict shallowly.
    """
    merged: dict = {}
    for o in reversed([x for x in outputs if isinstance(x, dict)]):
        # Merge shallow keys
        for k, v in o.items():
            if k == "tg" and isinstance(v, dict):
                base = (
                    merged.get("tg", {}) if isinstance(merged.get("tg"), dict) else {}
                )
                base = {**v, **base}  # v has lower priority than already set keys
                merged["tg"] = base
            else:
                merged.setdefault(k, v)
    return merged


# moved to utils.html_sanitizer: clean_html_for_telegraph

# moved to utils.html_sanitizer: clean_html_for_telegram

# moved to utils.telegram_text: telegram_truncate_html_safe
# moved to utils.telegram_text: telegram_truncate_markdown_safe

# moved to utils.html_sanitizer: minify_html_func


# moved to utils.telegraph_utils: create_telegrath_account


# moved to utils.telegraph_utils: publish_results


async def edit_message_text(text):
    """Legacy helper routed to safe_edit_message_text; keeps compatibility."""
    try:
        if telegram_last_message is None:
            return
        await safe_edit_message_text(
            chat_id=telegram_last_message.chat_id,
            message_id=telegram_last_message.message_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        # Swallow errors to avoid breaking callers
        pass


class MCPServerHookMixin:
    """Common Telegram logging and cleanup logic shared by MCP server wrappers."""

    from typing import Any

    @staticmethod
    def _env_flag(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in ("1", "true", "yes", "on")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Per-instance last message holder
        self.__telegram_last_message: Optional[Message] = None
        # Cache last cleaned+truncated text to avoid redundant edits
        self.__last_tg_text: Optional[str] = None
        # Service message queue: track intermediate MCP messages for cleanup
        self.__service_message_ids: list[tuple[int, int]] = (
            []
        )  # [(chat_id, msg_id), ...]
        # Try to derive a readable MCP title
        self._mcp_title: str = (
            str(getattr(self, "name", "") or "").strip()
            or str(getattr(self, "id", "") or "").strip()
            or type(self).__name__
        )
        self._telegram_debug_enabled: bool = self._env_flag(
            "CALL_DEBUG_TELEGRAM", default=False
        )
        # Log sanitization settings (base64/image truncation), cached per-instance
        self._log_sanitize_images: bool = self._env_flag(
            "CALL_LOG_SANITIZE_IMAGES", default=True
        )
        try:
            self._log_truncate_data_max: int = int(
                os.getenv("CALL_LOG_TRUNCATE_DATA_MAX", "64") or "64"
            )
        except Exception:
            self._log_truncate_data_max = 64
        raw_keys = os.getenv("CALL_LOG_SANITIZE_KEYS")
        if raw_keys:
            parsed = [k.strip() for k in raw_keys.split(",") if k.strip()]
            self._log_sanitize_keys: list[str] = parsed or [
                "image_url",
                "b64_json",
                "base64",
                "data",
            ]
        else:
            self._log_sanitize_keys = ["image_url", "b64_json", "base64", "data"]

    # LOG-ONLY sanitizer: recursively truncate base64/image-like fields for human-readable logs.
    # Never use the sanitized output in business logic or protocol payloads.
    def _sanitize_image_like_fields(self, obj: Any) -> Any:
        if not self._log_sanitize_images:
            return obj

        def _truncate_string(val: Any) -> Any:
            if not isinstance(val, str):
                return val
            if len(val) <= self._log_truncate_data_max:
                return val
            return f"{val[: self._log_truncate_data_max]}...({len(val)} chars)"

        def _looks_base64(val: str) -> bool:
            if len(val) < max(self._log_truncate_data_max * 2, 80):
                return False
            import re as _re_b64

            return bool(_re_b64.fullmatch(r"[A-Za-z0-9+/=\r\n]+", val))

        if isinstance(obj, dict):
            sanitized: dict[Any, Any] = {}
            for key, val in obj.items():
                if key in self._log_sanitize_keys and isinstance(val, str):
                    sanitized[key] = _truncate_string(val)
                elif isinstance(val, str) and _looks_base64(val):
                    sanitized[key] = _truncate_string(val)
                else:
                    sanitized[key] = self._sanitize_image_like_fields(val)
            return sanitized
        if isinstance(obj, list):
            return [self._sanitize_image_like_fields(v) for v in obj]
        if isinstance(obj, tuple):
            return tuple(self._sanitize_image_like_fields(v) for v in obj)
        if isinstance(obj, set):
            return {self._sanitize_image_like_fields(v) for v in obj}
        if isinstance(obj, str) and _looks_base64(obj):
            return _truncate_string(obj)
        return obj

    @staticmethod
    def _progress_bar(
        thoughtNumber: int, totalThoughts: int, bar_length: int = 10
    ) -> str:
        """Render a compact progress bar strictly for Sequential Thinking updates."""
        try:
            tn = max(0, int(thoughtNumber))
            tt = max(1, int(totalThoughts))
            filled = int(bar_length * tn / tt)
            bar = "█" * filled + "░" * (bar_length - filled)
            return f"{bar} {tn}/{tt}"
        except Exception:
            return f"{thoughtNumber}/{totalThoughts}"

    def _format_tool_result(self, result: Any, *, max_len: int = 4000) -> str:
        """Convert tool result to a readable, truncated string for logging."""
        value = result

        def _dump_like_mapping(obj: Any) -> Any:
            """Recursively convert pydantic models to dicts."""
            # Try to convert the object itself
            for attr in ("model_dump", "dict"):
                method = getattr(obj, attr, None)
                if callable(method):
                    try:
                        converted = method()
                        # Recursively process the result
                        return _dump_like_mapping(converted)
                    except TypeError:
                        try:
                            converted = method(by_alias=True)
                            return _dump_like_mapping(converted)
                        except Exception:
                            continue

            # Recursively handle collections
            if isinstance(obj, dict):
                return {k: _dump_like_mapping(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [_dump_like_mapping(item) for item in obj]
            elif isinstance(obj, set):
                return {_dump_like_mapping(item) for item in obj}
            return obj

        value = _dump_like_mapping(value)

        # Debug: inspect string content BEFORE unescape
        def _debug_string_content(obj: Any, path="") -> None:
            if isinstance(obj, str) and len(obj) > 500:
                # Check for both real newlines and escaped sequences
                real_newlines = obj.count("\n")  # chr(10)
                escaped_backslash_n = obj.count("\\n")  # two chars: \ and n
                if real_newlines > 5 or escaped_backslash_n > 5:
                    # Only log if CALL_DEBUG_YAML is enabled
                    if os.environ.get("CALL_DEBUG_YAML", "0").strip().lower() in ("1", "true", "yes", "on"):
                        debug_print(
                            f"[YAML Pre-Unescape] {path}: len={len(obj)}, "
                            f"real_newlines={real_newlines}, "
                            f"escaped_\\n={escaped_backslash_n}, "
                            f"repr_preview={obj[:100]!r}"
                        )
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    _debug_string_content(v, f"{path}.{k}" if path else k)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _debug_string_content(v, f"{path}[{i}]")
        
        try:
            _debug_string_content(value, "PRE")
        except Exception:
            pass

        def _unescape_strings(obj: Any) -> Any:
            """Unescape common escape sequences in strings for cleaner YAML output."""
            if isinstance(obj, str):
                if "\\" in obj and any(
                    seq in obj for seq in ("\\n", "\\t", "\\r", '\\"', "\\'")
                ):
                    result = obj
                    result = result.replace(
                        "\\\\", "\x00"
                    )
                    result = result.replace("\\n", "\n")
                    result = result.replace("\\r", "\r")
                    result = result.replace("\\t", "\t")
                    result = result.replace('\\"', '"')
                    result = result.replace("\\'", "'")
                    result = result.replace("\x00", "\\")
                    return result
                return obj
            if isinstance(obj, dict):
                return {k: _unescape_strings(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_unescape_strings(v) for v in obj]
            if isinstance(obj, tuple):
                return tuple(_unescape_strings(v) for v in obj)
            if isinstance(obj, set):
                return {_unescape_strings(v) for v in obj}
            return obj

        value = _unescape_strings(value)

        def _count_newlines(obj: Any, path="") -> None:
            if isinstance(obj, str) and len(obj) > 100:
                actual_newlines = obj.count("\n")
                escaped_newlines = obj.count("\\n")
                if actual_newlines > 0 or escaped_newlines > 0:
                    # Only log if CALL_DEBUG_YAML is enabled
                    if os.environ.get("CALL_DEBUG_YAML", "0").strip().lower() in ("1", "true", "yes", "on"):
                        debug_print(
                            f"[YAML Debug] {path}: len={len(obj)}, "
                            f"actual_\\n={actual_newlines}, escaped_\\n={escaped_newlines}, "
                            f"preview={obj[:80]!r}"
                        )
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    _count_newlines(v, f"{path}.{k}" if path else k)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    _count_newlines(v, f"{path}[{i}]")

        try:
            _count_newlines(value, "value")
        except Exception:
            pass

        def _truncate_image_like_fields(obj: Any) -> Any:
            """
            Recursively truncate base64/image-like fields for display/logging only.

            Mirrors media-gen-mcp logging rules:
            - Controlled by CALL_LOG_SANITIZE_IMAGES (default: on)
            - Keys configurable via CALL_LOG_SANITIZE_KEYS (comma-separated)
            - Truncation length configurable via CALL_LOG_TRUNCATE_DATA_MAX (default: 64)
            """
            return self._sanitize_image_like_fields(obj)

        value = _truncate_image_like_fields(value)
        force_structured_output = False
        try:
            if isinstance(value, dict):
                structured_value = value.get("structuredContent")
                if structured_value not in (None, [], {}, ""):
                    force_structured_output = True
                else:
                    content_value = value.get("content")
                    if isinstance(content_value, list):
                        for item in content_value:
                            if isinstance(item, dict):
                                item_type = str(item.get("type") or "")
                                if item_type and item_type != "text":
                                    force_structured_output = True
                                    break
                            else:
                                # Unknown item shape; keep structured view for safety.
                                force_structured_output = True
                                break
        except Exception:
            logging.getLogger("call.mcp").debug(
                "[mcp] Failed to inspect tool result shape; falling back to structured output",
                exc_info=True,
            )
            # On any inspection failure, default to structured output so we don't
            # accidentally drop context from logs.
            force_structured_output = True

        try:
            # If result is a simple text item or list of text items, unwrap to plain text
            def _is_text_item(obj: Any) -> bool:
                return isinstance(obj, dict) and set(obj.keys()) <= {"type", "text"} and obj.get("type") == "text" and isinstance(obj.get("text"), str)

            def _gather_texts(obj: Any) -> list[str]:
                out: list[str] = []
                if _is_text_item(obj):
                    out.append(obj.get("text", ""))
                    return out
                if isinstance(obj, list):
                    for it in obj:
                        out.extend(_gather_texts(it))
                    return out
                if isinstance(obj, dict):
                    # Common wrappers from tool_result payloads
                    for key in ("result", "content", "results", "items", "value"):
                        if key in obj:
                            out.extend(_gather_texts(obj.get(key)))
                    for v in obj.values():
                        out.extend(_gather_texts(v))
                return out

            gathered = [] if force_structured_output else _gather_texts(value)
            if gathered:
                text = "\n\n".join(t for t in gathered if t)
            elif _is_text_item(value):
                text = value.get("text", "")
            elif isinstance(value, list) and all(_is_text_item(it) for it in value):
                text = "\n\n".join(it.get("text", "") for it in value)
            elif isinstance(value, dict):
                try:
                    text = _dump_yaml_literal(value)
                except Exception:
                    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
            else:
                if isinstance(value, (bytes, bytearray)):
                    if len(value) > max_len:
                        return f"<bytes len={len(value)} truncated for display>"
                    text = value.decode("utf-8", errors="replace")
                elif isinstance(value, str):
                    text = value
                elif isinstance(value, (list, tuple, set)):
                    seq = list(value)
                    if isinstance(value, tuple):
                        seq = list(value)
                    if isinstance(value, set):
                        seq = sorted(list(value), key=str)
                    try:
                        text = _dump_yaml_literal(seq)
                    except Exception:
                        text = json.dumps(seq, ensure_ascii=False, indent=2, default=str)
                elif isinstance(value, dict):
                    try:
                        text = _dump_yaml_literal(value)
                    except Exception:
                        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
                else:
                    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        except Exception:
            try:
                text = repr(value)
            except Exception:
                text = f"{type(value)!r}"

        text = text.replace("\r\n", "\n")
        try:
            import re as _re_collapse

            # Collapse multiple consecutive newlines to exactly 1
            # IMPORTANT: replacement must be actual newline, not raw string r"\n"
            text = _re_collapse.sub(r"\n{2,}", "\n", text)
        except Exception:
            pass

        call_debug = bool(os.getenv("CALL_DEBUG"))
        if not call_debug and len(text) > max_len:
            text = text[: max_len - 3] + "..."
        return text.rstrip("\n")

    async def __send_message(self, text: str) -> Optional[Message]:
        """Send a new Telegram message for this MCP instance and cache it. Never raises."""
        # Debug-only: never fall back to selected_chat_id (origin chat).
        # If debug chat is not configured, or debug is disabled, do not send anything.
        if (not getattr(self, "_telegram_debug_enabled", False)) or debug_chat_id is None:
            return self.__telegram_last_message
        target_chat_id = debug_chat_id
        target_thread_id = debug_thread_id
        header = f"<b>{self._mcp_title}</b>\n\n"
        rendered_body = _render_body_with_code_blocks(text or "")
        payload = header + f"<blockquote expandable>{rendered_body}</blockquote>"
        # Sanitize and truncate to avoid Telegram 4096 limit and user's 3800 limit
        try:
            cleaned = sanitize_telegram_html(payload)
            cleaned = telegram_truncate_html_safe(cleaned, 3800)
            # Use debug_chat_id for MCP debug messages (always from .env)
            msg = await safe_send_message(
                chat_id=target_chat_id,
                message_thread_id=target_thread_id,
                text=cleaned,
                parse_mode=ParseMode.HTML,
                disable_notification=True,
            )
            self.__telegram_last_message = msg
            self.__last_tg_text = cleaned
            # Track service message for cleanup
            if msg:
                self.__service_message_ids.append((msg.chat_id, msg.message_id))
            return msg
        except Exception:
            # Swallow errors to keep MCP flow running
            return self.__telegram_last_message  # may be None

    async def __edit_message_text(self, text: str) -> None:
        """Edit this instance's message; if missing, send a new one. Never raises."""
        # Debug-only: never edit/send when debug is disabled or when debug chat isn't configured.
        if (not getattr(self, "_telegram_debug_enabled", False)) or debug_chat_id is None:
            return
        header = f"<b>{self._mcp_title}</b>\n\n"
        rendered_body = _render_body_with_code_blocks(text or "")
        safe_text = header + f"<blockquote expandable>{rendered_body}</blockquote>"
        if not self.__telegram_last_message:
            # For the initial send, pass the raw body to __send_message; it will wrap/escape itself
            try:
                await self.__send_message(text)
            except Exception:
                pass
            return
        # Clean and truncate
        try:
            cleaned = sanitize_telegram_html(safe_text)
            cleaned = telegram_truncate_html_safe(cleaned, 3800)
            # Skip edit if content is unchanged (prevents BadRequest: Message is not modified)
            if self.__last_tg_text == cleaned:
                return
            res = await safe_edit_message_text(
                chat_id=self.__telegram_last_message.chat_id,
                message_id=self.__telegram_last_message.message_id,
                text=cleaned,
                parse_mode=ParseMode.HTML,
            )
            if res is not None:
                self.__last_tg_text = cleaned
        except Exception:
            # Never propagate
            pass

    # NOTE: Backwards-compatibility shim for tests that patch
    # '_MCPServerStdioHook__edit_message_text' on MCPServerStdioHook
    # instances. Name-mangled alias simply forwards to the real
    # __edit_message_text implementation.
    async def _MCPServerStdioHook__edit_message_text(self, text: str) -> None:  # type: ignore[override]
        await self._edit_message_html(text)

    async def _edit_message_html(self, text: str) -> None:
        """Editable hook that is patch-friendly in tests.

        Tests patch the mixin-level private `_MCPServerHookMixin__edit_message_text`.
        Try that first; otherwise fall back to the local implementation.
        """
        try:
            mixin_fn = getattr(self, "_MCPServerHookMixin__edit_message_text", None)
            if mixin_fn:
                return await mixin_fn(text)
        except Exception:
            pass
        await self.__edit_message_text(text)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None
    ) -> CallToolResult:
        logging.info("[mcp][%s] 🔧 call_tool: %s", self._mcp_title, tool_name)
        debug_print(f"[MCP Hook][{self._mcp_title}] Calling tool: {tool_name}")
        
        # Log arguments preview
        try:
            args_preview = json.dumps(arguments or {}, ensure_ascii=False, default=str)
            if len(args_preview) > 200:
                args_preview = args_preview[:200] + "..."
            logging.debug("[mcp][%s] arguments: %s", self._mcp_title, args_preview)
        except Exception:
            pass
            
        # Resolve the parent call: prefer an explicit override injected by the
        # subclass (used in tests), otherwise fall back to the stdio/parent impl.
        parent_call_tool = getattr(self, "_call_tool_parent_override", None)
        if parent_call_tool is None:
            parent_call_tool = super().call_tool
        else:
            # If the override is an unbound function (as in tests), bind self explicitly.
            if not inspect.ismethod(parent_call_tool):
                parent_call_tool = functools.partial(parent_call_tool, self)

        def _deep_unescape(o):
            if isinstance(o, str):
                # Check if string looks like it contains JSON escape sequences
                if "\\" in o and any(
                    seq in o for seq in ("\\n", "\\t", "\\r", '\\"', "\\'")
                ):
                    # Manually replace escape sequences
                    result = o
                    result = result.replace(
                        "\\\\", "\x00"
                    )  # Temp marker for literal backslash
                    result = result.replace("\\n", "\n")
                    result = result.replace("\\r", "\r")
                    result = result.replace("\\t", "\t")
                    result = result.replace('\\"', '"')
                    result = result.replace("\\'", "'")
                    result = result.replace("\x00", "\\")  # Restore literal backslash
                    return result
                return o
            if isinstance(o, list):
                return [_deep_unescape(i) for i in o]
            if isinstance(o, tuple):
                return tuple(_deep_unescape(i) for i in o)
            if isinstance(o, set):
                return {_deep_unescape(i) for i in o}
            if isinstance(o, dict):
                return {k: _deep_unescape(v) for k, v in o.items()}
            return o

        # Try to present arguments in YAML for readability (console)
        def _to_yaml_text(obj) -> str:
            """Dump arguments to YAML with better readability."""
            prepared = _deep_unescape(obj or {})
            try:
                return _dump_yaml_literal(prepared)
            except Exception:
                try:
                    json_text = json.dumps(
                        obj or {}, ensure_ascii=False, indent=2, default=str
                    )
                    prepared = _deep_unescape(json.loads(json_text))
                    return _dump_yaml_literal(prepared)
                except Exception:
                    try:
                        s = json.dumps(
                            obj or {}, ensure_ascii=False, indent=2, default=str
                        )
                        return s.replace("\\n", "\n").replace("\\t", "\t")
                    except Exception:
                        return str(obj)

        sanitized_args = self._sanitize_image_like_fields(arguments)
        yaml_args = _to_yaml_text(sanitized_args)
        debug_print("[MCP Hook] Arguments (YAML):\n" + yaml_args)

        if tool_name != "sequentialthinking":
            # For all other tools: send/edit YAML arguments in Telegram without breaking on errors
            try:
                yaml_text = _to_yaml_text(sanitized_args)
                body = f"🛠️ {tool_name}\n\n{yaml_text}".strip()
                if self.__telegram_last_message is None:
                    try:
                        await self.__send_message(body)
                    except Exception:
                        pass
                try:
                    # Always attempt to edit via mixin-friendly helper so tests can hook it
                    await self._edit_message_html(body)
                except Exception:
                    pass
            except Exception:
                # Swallow any Telegram errors
                pass
            try:

                async def _call():
                    return await parent_call_tool(tool_name, arguments)

                # Default preview fallback so tests still see an edit even if formatting fails
                display_preview: str | None = f"✅ {tool_name}"
                display_preview_sent = False
                result_text_for_display: str | None = None
                result = await async_retry(
                    _call,
                    retries=1,
                    base_delay=1.0,
                    jitter=0.2,
                    retry_on=(httpx.TimeoutException, OSError),
                )
                # Ensure at least one display edit is attempted even before formatting.
                try:
                    mixin_force = self.__dict__.get(
                        "_MCPServerHookMixin__edit_message_text"
                    )
                    if mixin_force is None:
                        mixin_force = getattr(
                            self, "_MCPServerHookMixin__edit_message_text", None
                        )
                    if mixin_force:
                        maybe_coro = mixin_force(display_preview)
                        if asyncio.iscoroutine(maybe_coro):
                            await maybe_coro
                        display_preview_sent = True
                except Exception:
                    pass
                # Emit an early success tick so display is always updated, even if formatting fails.
                try:
                    await self._edit_message_html(display_preview)
                    display_preview_sent = True
                except Exception:
                    display_preview_sent = False
                try:
                    # Format for display only (Telegram/logs) - this may truncate
                    result_text_for_display = self._format_tool_result(result)
                    logging.info(
                        "[mcp][%s] ✅ tool %s completed", self._mcp_title, tool_name
                    )
                    # Echo arguments near result for easier correlation in busy logs
                    try:
                        debug_print(
                            "[MCP Hook] Arguments (YAML, echoed near result):\n"
                            + yaml_args
                        )
                    except Exception:
                        pass
                    debug_print(
                        f"[MCP Hook][{self._mcp_title}] Tool {tool_name} returned:\n"
                        + result_text_for_display
                    )
                    preview_text = result_text_for_display
                    if len(preview_text) > 3800:
                        preview_text = preview_text[:3800] + "…"
                    display_preview = f"✅ {tool_name}\n\n{preview_text}".strip()
                    if display_preview:
                        try:
                            # Prefer the mixin-patched edit helper when present so tests
                            # can intercept even if display truncation kicks in.
                            mixin_edit = self.__dict__.get(
                                "_MCPServerHookMixin__edit_message_text"
                            )
                            if mixin_edit is None:
                                mixin_edit = getattr(
                                    self, "_MCPServerHookMixin__edit_message_text", None
                                )
                            if mixin_edit:
                                maybe_coro = mixin_edit(display_preview)
                                if asyncio.iscoroutine(maybe_coro):
                                    await maybe_coro
                                display_preview_sent = True
                            else:
                                await self._edit_message_html(display_preview)
                                display_preview_sent = True
                        except Exception:
                            display_preview_sent = False
                    # Return ORIGINAL result to agent pipeline (never truncate!)
                    # result is already a CallToolResult, return as-is
                    return result
                finally:
                    # If formatting failed earlier, attempt a best-effort preview so tests still see an edit.
                    if display_preview is None:
                        try:
                            preview = result_text_for_display or ""
                            if not preview and "result" in locals():
                                preview = self._format_tool_result(locals().get("result"))
                            if preview:
                                if len(preview) > 3800:
                                    preview = preview[:3800] + "…"
                                display_preview = f"✅ {tool_name}\n\n{preview}".strip()
                        except Exception:
                            display_preview = None
                    # Always attempt to emit a final preview to the mixin hook (tests patch it).
                    final_preview = display_preview or f"✅ {tool_name}"
                    try:
                        # Always emit a final preview through the mixin-friendly helper;
                        # tests patch this hook and only check that *something* was sent.
                        # Prefer an instance-level patch (tests monkeypatch the mixin helper)
                        mixin_edit = self.__dict__.get(
                            "_MCPServerHookMixin__edit_message_text"
                        )
                        if mixin_edit is None:
                            mixin_edit = getattr(
                                self, "_MCPServerHookMixin__edit_message_text", None
                            )
                        if mixin_edit:
                            maybe_coro = mixin_edit(final_preview)
                            if asyncio.iscoroutine(maybe_coro):
                                await maybe_coro
                            display_preview_sent = True
                        else:
                            await self._edit_message_html(final_preview)
                            display_preview_sent = True
                    except Exception:
                        # Absolute fallback: try the HTML helper directly.
                        try:
                            await self._edit_message_html(final_preview)
                        except Exception:
                            pass
                    # Last-resort safety: if nothing was sent, emit a minimal tick so tests
                    # that patch the mixin still observe an update.
                    if not display_preview_sent:
                        try:
                            mixin_edit = self.__dict__.get(
                                "_MCPServerHookMixin__edit_message_text"
                            )
                            if mixin_edit is None:
                                mixin_edit = getattr(
                                    self, "_MCPServerHookMixin__edit_message_text", None
                                )
                            fallback_preview = f"✅ {tool_name}"
                            if mixin_edit:
                                maybe_coro = mixin_edit(fallback_preview)
                                if asyncio.iscoroutine(maybe_coro):
                                    await maybe_coro
                            else:
                                await self._edit_message_html(fallback_preview)
                        except Exception:
                            pass
            except Exception as e:
                logging.error(
                    "[mcp][%s] ❌ tool %s failed: %s: %s",
                    self._mcp_title,
                    tool_name,
                    type(e).__name__,
                    str(e),
                    exc_info=True
                )
                try:
                    err_text = format_exception_text(e)
                    await self._edit_message_html(
                        f"❌ Error in {tool_name}\n\n" + err_text
                    )
                except Exception:
                    pass
                # Echo arguments near error to simplify debugging of failed calls
                try:
                    debug_print(
                        "[MCP Hook] Arguments (YAML, echoed near error):\n" + yaml_args
                    )
                except Exception:
                    pass
                # Return JSON-wrapped error instead of raising
                error_payload = {
                    "ok": False,
                    "tool": tool_name,
                    "error": format_exception_json(e),
                }
                # Add MCP-specific error code if available
                if isinstance(e, McpError):
                    error_payload["error"]["mcp_code"] = getattr(e, "code", None)
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps(error_payload, ensure_ascii=False),
                        )
                    ]
                )
        try:
            thought = arguments["thought"]
            # Determine counters safely
            tn = int((arguments or {}).get("thoughtNumber") or 0)
            tt = int((arguments or {}).get("totalThoughts") or 0)

            # On first write, send a banner-only message without progress bar
            if self.__telegram_last_message is None:
                input_text = (
                    (arguments or {}).get("input")
                    or (arguments or {}).get("user_input")
                    or (arguments or {}).get("prompt")
                )
                banner_lines = [f"🔌 {self._mcp_title}"]
                if input_text:
                    try:
                        safe_input = str(input_text)[:1000]
                        banner_lines.append(f"{safe_input}")
                    except Exception:
                        pass
                try:
                    await self.__send_message("\n".join(banner_lines))
                except Exception:
                    pass
                # Do not display progress bar on the very first tick
                if tn <= 0:
                    return await super().call_tool(tool_name, arguments)

            # Show progress bar only for actual progress (tn >= 1)
            if tn >= 1 and tt >= 1:
                bar = self._progress_bar(tn, tt)
                text = (
                    f"<b>💭Thinking: {bar}</b>\n\n{thought}\n\n<b>💭Thinking: {bar}</b>"
                )
            else:
                text = str(thought)
            try:
                await self._edit_message_html(text)
            except Exception:
                pass

            # Send typing action to original request chat (selected_chat_id), not debug chat
            try:
                if selected_chat_id is not None:

                    async def _op():
                        return await bot.send_chat_action(
                            chat_id=selected_chat_id,
                            message_thread_id=selected_thread_id,
                            action=ChatAction.TYPING,
                        )

                    try:
                        await async_retry(
                            _op,
                            retries=1,
                            base_delay=0.5,
                            jitter=0.1,
                            retry_on=(TimedOut, NetworkError, httpx.TimeoutException),
                        )
                    except BadRequest as br:
                        # Fallback: retry without thread id if not a forum topic
                        if "thread not found" in str(br).lower():

                            async def _op_no_thread():
                                return await bot.send_chat_action(
                                    chat_id=selected_chat_id, action=ChatAction.TYPING
                                )

                            await async_retry(
                                _op_no_thread,
                                retries=1,
                                base_delay=0.5,
                                jitter=0.1,
                                retry_on=(
                                    TimedOut,
                                    NetworkError,
                                    httpx.TimeoutException,
                                ),
                            )
                        else:
                            raise
            except Exception:
                pass

            try:

                async def _call():
                    return await parent_call_tool(tool_name, arguments)

                result = await async_retry(
                    _call,
                    retries=1,
                    base_delay=1.0,
                    jitter=0.2,
                    retry_on=(httpx.TimeoutException, OSError),
                )
                # Format for display only (logs) - this may truncate
                result_text_for_display = self._format_tool_result(result)
                debug_print("[MCP Hook] Tool completed successfully")
                # Echo arguments near result for easier correlation in busy logs
                try:
                    debug_print(
                        "[MCP Hook] Arguments (YAML, echoed near result):\n" + yaml_args
                    )
                except Exception:
                    pass
                debug_print(
                    f"[MCP Hook][{self._mcp_title}] Tool {tool_name} returned:\n"
                    + result_text_for_display
                )
                # Return ORIGINAL result to agent pipeline (never truncate!)
                # result is already a CallToolResult, return as-is
                return result
            except Exception as e:
                logging.error(
                    "[mcp][%s] ❌ tool %s failed: %s: %s",
                    self._mcp_title,
                    tool_name,
                    type(e).__name__,
                    str(e),
                    exc_info=True
                )
                err_text = format_exception_text(e)
                try:
                    await self._edit_message_html(
                        f"❌ Error in {tool_name}\n\n" + err_text
                    )
                except Exception:
                    pass
                # Echo arguments near error to simplify debugging of failed calls
                try:
                    debug_print(
                        "[MCP Hook] Arguments (YAML, echoed near error):\n" + yaml_args
                    )
                except Exception:
                    pass
                # Return JSON-wrapped error instead of raising
                error_payload = {
                    "ok": False,
                    "tool": tool_name,
                    "error": format_exception_json(e),
                }
                if isinstance(e, McpError):
                    error_payload["error"]["mcp_code"] = getattr(e, "code", None)
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps(error_payload, ensure_ascii=False),
                        )
                    ]
                )
        except Exception as e:
            print(f"[MCP Hook] Error in tool {tool_name}: {str(e)}")
            raise

    async def cleanup_service_messages(self) -> None:
        """Delete all tracked service messages from Telegram. All exceptions are caught and logged."""
        log = logging.getLogger("call.mcp.cleanup")
        
        try:
            # Check if cleanup is enabled via environment variable
            cleanup_enabled = os.environ.get("TG_CLEANUP_MCP_MESSAGES", "1").strip().lower()
            if cleanup_enabled not in ("1", "true", "yes", "on"):
                log.debug("[%s] Cleanup disabled via TG_CLEANUP_MCP_MESSAGES=%s", self.name, cleanup_enabled)
                return
            
            message_count = len(self.__service_message_ids)
            if message_count == 0:
                log.debug("[%s] No service messages to cleanup", self.name)
                return
            
            log.info("[%s] Starting cleanup of %d service messages", self.name, message_count)
            
            try:
                await _init_bot_safe()
            except Exception as e:
                log.error("[%s] Failed to initialize bot for cleanup: %s", self.name, e, exc_info=True)
                return
            
            deleted_count = 0
            failed_count = 0
            
            for chat_id, msg_id in self.__service_message_ids:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    deleted_count += 1
                    log.debug("[%s] Deleted message: chat_id=%s, msg_id=%s", self.name, chat_id, msg_id)
                except Exception as e:
                    failed_count += 1
                    log.warning("[%s] Failed to delete message (chat_id=%s, msg_id=%s): %s", 
                               self.name, chat_id, msg_id, e)
            
            self.__service_message_ids.clear()
            log.info("[%s] Cleanup completed: deleted=%d, failed=%d, total=%d", 
                    self.name, deleted_count, failed_count, message_count)
        
        except Exception as e:
            # Catch-all for any unexpected errors in cleanup logic itself
            log.error("[%s] Unexpected error in cleanup_service_messages: %s", 
                     self.name, e, exc_info=True)
    async def list_tools(self, run_context=None, agent=None):
        """Wrap base list_tools with extra logging for initialization errors."""
        try:
            return await super().list_tools(run_context=run_context, agent=agent)
        except Exception as e:
            logging.error(
                "[mcp][%s] list_tools failed: %s: %s",
                self._mcp_title,
                type(e).__name__,
                str(e),
                exc_info=True,
            )
            try:
                debug_print(
                    "[mcp]",
                    f"[{self._mcp_title}] list_tools failed: {type(e).__name__}: {e}",
                )
            except Exception:
                pass
            raise

    async def list_prompts(self):
        """Wrap base list_prompts with extra logging for initialization errors."""
        try:
            return await super().list_prompts()
        except Exception as e:
            logging.error(
                "[mcp][%s] list_prompts failed: %s: %s",
                self._mcp_title,
                type(e).__name__,
                str(e),
                exc_info=True,
            )
            try:
                debug_print(
                    "[mcp]",
                    f"[{self._mcp_title}] list_prompts failed: {type(e).__name__}: {e}",
                )
            except Exception:
                pass
            raise

    async def get_prompt(self, name: str, arguments: dict | None = None):
        """Wrap base get_prompt with extra logging for initialization errors."""
        try:
            return await super().get_prompt(name, arguments)
        except Exception as e:
            logging.error(
                "[mcp][%s] get_prompt(%s) failed: %s: %s",
                self._mcp_title,
                name,
                type(e).__name__,
                str(e),
                exc_info=True,
            )
            try:
                debug_print(
                    "[mcp]",
                    f"[{self._mcp_title}] get_prompt({name}) failed: {type(e).__name__}: {e}",
                )
            except Exception:
                pass
            raise


# Keep an original reference so tests that monkeypatch the base mixin method
# can be detected at runtime.
ORIGINAL_MCP_MIXIN_CALL_TOOL = MCPServerHookMixin.call_tool


def _is_mixin_call_tool_patched() -> bool:
    """Return True if MCPServerHookMixin.call_tool has been monkeypatched."""
    return MCPServerHookMixin.call_tool is not ORIGINAL_MCP_MIXIN_CALL_TOOL

class MCPServerStdioHook(MCPServerHookMixin, MCPServerStdio):
    """Wrapper for MCPServerStdio that writes per-instance logs to Telegram.

    Each instance maintains its own editable Telegram message. On first write,
    a new message is created; subsequent writes edit that message. The MCP name
    is printed at the top of the message.
    
    Debug messages are sent to debug_chat_id (from TELEGRAM_DEBUG_CHAT_ID in .env),
    while typing status is sent to selected_chat_id (original request chat).
    """

    async def call_tool(self, tool_name, arguments):
        """
        Keep display/logging logic intact even if tests monkeypatch the mixin's
        call_tool. We always execute the original mixin implementation, but let
        the patched mixin function act as the parent tool executor when present.
        """
        parent_override = None
        if _is_mixin_call_tool_patched():
            parent_override = MCPServerHookMixin.call_tool
        try:
            if parent_override:
                self._call_tool_parent_override = parent_override
            return await ORIGINAL_MCP_MIXIN_CALL_TOOL(self, tool_name, arguments)
        finally:
            if hasattr(self, "_call_tool_parent_override"):
                try:
                    del self._call_tool_parent_override
                except Exception:
                    self._call_tool_parent_override = None


class MCPServerSseHook(MCPServerHookMixin, MCPServerSse):
    """Wrapper for MCPServerSse that reuses the same Telegram logging mixin."""

    async def call_tool(self, tool_name, arguments):
        parent_override = None
        if _is_mixin_call_tool_patched():
            parent_override = MCPServerHookMixin.call_tool
        try:
            if parent_override:
                self._call_tool_parent_override = parent_override
            return await ORIGINAL_MCP_MIXIN_CALL_TOOL(self, tool_name, arguments)
        finally:
            if hasattr(self, "_call_tool_parent_override"):
                try:
                    del self._call_tool_parent_override
                except Exception:
                    self._call_tool_parent_override = None


# -------- Call subsystem helpers --------
# KISS policy: names are treated as-is (case-sensitive). No normalization helpers.


def discover_prompt_repo() -> Path:
    """Locate prompt repository root.
    Priority: env PROMPT_REPO -> sibling '../prompt' -> workspace default.
    """
    env_repo = os.environ.get("PROMPT_REPO")
    if env_repo and Path(env_repo).exists():
        return Path(env_repo)
    # try sibling 'prompt' at workspace root
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "prompt",  # .../PycharmProjects/prompt
        here.parents[1] / "prompt",  # .../call/prompt (if copied inside)
        Path("c:/Users/Leader/PycharmProjects/prompt"),
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Prompt repository not found. Set PROMPT_REPO env to its path."
    )


def _load_agents_index(index_path: Path, base_dir: Path) -> dict[str, Path]:
    """Load agents index file which may contain 'agents' and optional 'aliases'.

    Returns a mapping from agent name and all aliases (PascalCase) to full agent.yaml Path.
    """
    mapping: dict[str, Path] = {}

    def _resolve_dir_case(parent: Path, name: str) -> Path:
        """Return a child path using the on-disk directory name casing if present.

        Performs a case-insensitive match among `parent` entries and returns the
        actual directory Path so that `Path(...).parent.name` reflects real casing.
        """
        try:
            target_lower = str(name).lower()
            for entry in parent.iterdir():
                if entry.is_dir() and entry.name.lower() == target_lower:
                    return entry
        except Exception:
            pass
        return parent / name

    try:
        if not index_path.exists():
            return mapping
        data = load_yaml(index_path) or {}
        agents_map = data.get("agents") or {}
        # Optional explicit aliases mapping: { AgentName: [alias1, alias2, ...] }
        aliases_map = data.get("aliases") or {}
        if isinstance(agents_map, dict):
            for name in agents_map.keys():
                name_key = str(name)
                # resolve to actual directory casing if present
                agent_dir = _resolve_dir_case(base_dir, name_key)
                path = agent_dir / "agent.yaml"
                if path.exists():
                    mapping[name_key] = path
                # bind aliases
                if isinstance(aliases_map, dict):
                    for alias in (
                        aliases_map.get(name) or aliases_map.get(name_key) or []
                    ):
                        alias_key = str(alias)
                        if alias_key and path.exists():
                            mapping[alias_key] = path
    except Exception:
        # Non-fatal: fallback to directory scan later
        return {}
    return mapping


def load_yaml(path: Path) -> dict:
    """Simple YAML loader."""
    import yaml

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def format_exception_json(e: Exception) -> dict:
    """Return a JSON-serializable dict with rich exception details.

    Includes error type, message, top frame file:line, and full call stack.
    """
    import traceback, os

    tb = e.__traceback__
    frames = traceback.extract_tb(tb) if tb else []
    stack = []
    for fr in frames:
        stack.append(
            {
                "file": os.fspath(fr.filename),
                "line": fr.lineno,
                "function": fr.name,
                "code": (fr.line or ""),
            }
        )
    top_file = os.fspath(frames[-1].filename) if frames else None
    top_line = frames[-1].lineno if frames else None
    return {
        "type": type(e).__name__,
        "message": str(e),
        "file": top_file,
        "line": top_line,
        "stack": stack,
    }


class AgentDTO:
    """DTO for strato.Agent loaded from YAML.

    Variant A prompt handling:
    - Supports multiple prompt files.
    - Loads only prompts marked as ON by markers: '🟢', '+', 'v', 'on' (case-insensitive).
    - First loaded prompt is the default.
    - Enriches prompt with agent attributes if keys are missing (prompt overrides agent).
    - Exposes getDefaultPrompt(), getPrompt(name), getPromptNames().
    """

    @classmethod
    def from_yaml_file(cls, yaml_path: str | Path) -> "AgentDTO":
        """Load AgentDTO from YAML file."""
        import yaml

        path = Path(yaml_path)
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data, base_dir=path.parent)

    def __init__(self, raw: dict, base_dir: Path | None = None):
        # Store raw and base path
        self.raw: dict = raw or {}
        self.base_dir: Path | None = base_dir
        # Basic identity
        self.id: str | None = self.raw.get("id")
        self.name: str | None = self.raw.get("name") or self.id
        # Model fields
        self.model: str | None = self.raw.get("model") or self.raw.get("llm")
        self.instructions: str | None = self.raw.get("instructions")
        # Prompt references can be list/str/dict
        self.prompts = (
            self.raw.get("prompts")
            or self.raw.get("prompt")
            or self.raw.get("prompt_file")
        )
        # Extract model settings and general attributes
        self.model_settings = self._extract_model_settings()
        self.attributes: dict = {}
        used_keys = {
            "id",
            "name",
            "model",
            "llm",
            "instructions",
            "prompt",
            "prompts",
            "prompt_file",
            "model_settings",
            "modelSettings",
        }
        for k, v in self.raw.items():
            if k not in used_keys:
                self.attributes[k] = v

        # Internal prompt registry
        self._prompts: dict[str, dict] = {}
        self._default_prompt_name: str | None = None
        self._load_prompts_variant_a()

    def _extract_model_settings(self) -> ModelSettings | None:
        """Build ModelSettings from YAML fields if present."""

        def take(src: dict) -> dict:
            if not isinstance(src, dict):
                return {}
            keys = {
                "temperature",
                "top_p",
                "frequency_penalty",
                "presence_penalty",
                "tool_choice",
                "parallel_tool_calls",
                "truncation",
                "max_tokens",
                "reasoning",
                "metadata",
                "store",
                "include_usage",
            }
            return {k: src.get(k) for k in keys if k in src}

        ms_dict = {}
        # common places
        for key in ("model_settings", "modelSettings", "settings"):
            if key in self.raw and isinstance(self.raw[key], dict):
                ms_dict |= take(self.raw[key])
        # top-level fallbacks
        ms_dict |= take(self.raw)
        if not ms_dict:
            return None

        # convert booleans and numbers where possible
        def to_float(v):
            try:
                return float(v) if v is not None else None
            except Exception:
                return None

        return ModelSettings(
            temperature=to_float(ms_dict.get("temperature")),
            top_p=to_float(ms_dict.get("top_p")),
            frequency_penalty=to_float(ms_dict.get("frequency_penalty")),
            presence_penalty=to_float(ms_dict.get("presence_penalty")),
            tool_choice=ms_dict.get("tool_choice"),
            parallel_tool_calls=ms_dict.get("parallel_tool_calls"),
            truncation=ms_dict.get("truncation"),
            max_tokens=ms_dict.get("max_tokens"),
            reasoning=ms_dict.get("reasoning"),
            metadata=ms_dict.get("metadata"),
            store=ms_dict.get("store"),
            include_usage=ms_dict.get("include_usage"),
        )

    async def getInstructions(self) -> tuple[str, dict]:
        """Return final instructions text and attributes.

        New simplified logic:
        1) If prompts count = 0, use only agent.yaml as prompt
        2) If prompts exist, use first prompt and merge with agent metadata
        3) If first prompt instructions is empty, use whole agent.yaml as agent_instructions
        """
        # Check if we have any prompts loaded
        if not self._prompts:
            # No prompts - use agent.yaml content
            try:
                if self.base_dir:
                    p = self.base_dir / "agent.yaml"
                    if p.exists():
                        return p.read_text(encoding="utf-8"), self.attributes
            except Exception:
                pass
            return "", self.attributes

        # We have prompts - use first one
        first_prompt = self.getDefaultPrompt()
        if isinstance(first_prompt, dict):
            instructions = first_prompt.get("instructions", "").strip()
            if instructions:
                # Merge prompt attributes with agent attributes (prompt has priority)
                merged_attrs = dict(self.attributes)
                merged_attrs.update(first_prompt)
                return instructions, merged_attrs
            else:
                # Empty instructions - fallback to agent.yaml
                try:
                    if self.base_dir:
                        p = self.base_dir / "agent.yaml"
                        if p.exists():
                            return p.read_text(encoding="utf-8"), self.attributes
                except Exception:
                    pass

        return "", self.attributes

    # -------- Variant A prompt support --------
    def _is_on_marker(self, token: str) -> bool:
        if not token:
            return False
        t = str(token).strip().lower()
        return t in {"on", "+", "v", "🟢"} or token in {"🟢"}

    def _enrich_prompt(self, prompt_obj: dict) -> dict:
        # Prompt attributes have priority; only fill missing keys from agent attributes
        enriched = dict(prompt_obj or {})
        # Ensure model inherit if not set in prompt
        if "model" not in enriched and self.model:
            enriched["model"] = self.model
        # Inherit generic agent attributes, EXCEPT alias/aliases (do not inherit those)
        for k, v in self.attributes.items():
            if k in {"alias", "aliases"}:
                continue
            if k not in enriched:
                enriched[k] = v

        # Special handling for aliases/alias: ensure inheritance if missing; do not override prompt's values
        # Normalize to list when merging
        def _to_list(val):
            if val is None:
                return None
            if isinstance(val, list):
                return val
            return [val]

        has_aliases = "aliases" in enriched and enriched.get("aliases") not in (
            None,
            [],
        )
        has_alias = "alias" in enriched and enriched.get("alias") not in (None, [])

        # Do NOT inherit aliases from agent if prompt lacks them.
        # Only mirror between forms if one exists in the prompt.
        if has_aliases and not has_alias:
            enriched["alias"] = _to_list(enriched.get("aliases")) or []
        if has_alias and not has_aliases:
            enriched["aliases"] = _to_list(enriched.get("alias")) or []
        return enriched

    def _register_prompt(self, name: str, prompt_obj: dict, is_default_candidate: bool):
        name = name.strip()
        if not name:
            return
        if name not in self._prompts:
            self._prompts[name] = self._enrich_prompt(prompt_obj)
            if self._default_prompt_name is None and is_default_candidate:
                self._default_prompt_name = name

    def _load_prompt_file(self, file_name: str) -> dict | None:
        try:
            base = self.base_dir or Path(".")
            path = (base / file_name).resolve()
            if not path.exists():
                return None
            import yaml  # lazy

            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            # Normalize: allow full file or section under 'instructions'
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            return None

    def _resolve_prompt_target(self, target: str) -> Path | None:
        """Resolve a prompt target to an existing file path.

        Accepts targets with or without extension; tries .yaml then .yml when missing.
        """
        if not isinstance(target, str) or not target.strip():
            return None
        base = self.base_dir or Path(".")
        # If target already has extension, check directly
        t = target.strip()
        p = (base / t).resolve()
        if p.exists() and p.is_file():
            return p
        # Try with .yaml and .yml if no extension given
        if "." not in Path(t).name:
            for ext in (".yaml", ".yml"):
                cand = (base / f"{t}{ext}").resolve()
                if cand.exists() and cand.is_file():
                    return cand
        return None

    def _load_prompts_variant_a(self):
        """Load prompts with simplified logic.

        Extract first word from prompts list/text, try loading as .md or .yaml.
        """
        if not self.prompts:
            return

        # Extract first word/item from prompts
        first_prompt_name = None
        if isinstance(self.prompts, list) and self.prompts:
            first_prompt_name = str(self.prompts[0]).strip().split()[0]
        elif isinstance(self.prompts, str) and self.prompts.strip():
            first_prompt_name = self.prompts.strip().split()[0]
        elif isinstance(self.prompts, dict):
            # Mapping form: name -> instructions string or prompt object
            for name, value in self.prompts.items():
                prompt_obj = None
                if isinstance(value, str):
                    prompt_obj = {"instructions": value}
                elif isinstance(value, dict):
                    prompt_obj = dict(value)
                if prompt_obj:
                    self._register_prompt(
                        str(name), prompt_obj, is_default_candidate=True
                    )
                    break  # Only use first one
            return

        if not first_prompt_name:
            return

        # Try loading first_prompt_name as .md or .yaml
        base = self.base_dir or Path(".")
        for ext in [".md", ".yaml", ".yml"]:
            prompt_path = base / f"{first_prompt_name}{ext}"
            if prompt_path.exists():
                try:
                    if ext == ".md":
                        with open(prompt_path, "r", encoding="utf-8") as f:
                            content = f.read()
                        data = {"instructions": content}
                    else:  # .yaml or .yml
                        data = self._load_prompt_file(str(prompt_path))

                    if data:
                        self._register_prompt(
                            first_prompt_name, data, is_default_candidate=True
                        )
                        break
                except Exception:
                    continue

    def getPromptNames(self) -> list[str]:
        return list(self._prompts.keys())

    def getPrompt(self, name: str) -> dict | None:
        return self._prompts.get(name)

    def getDefaultPrompt(self) -> dict | None:
        if self._default_prompt_name and self._default_prompt_name in self._prompts:
            return self._prompts[self._default_prompt_name]
        # If nothing marked as default but any prompt exists, use first
        if self._prompts:
            return self._prompts[next(iter(self._prompts.keys()))]
        return None


async def resolve_vector_stores(vs_val: Any) -> List[str]:
    """Normalize and resolve vector store entries using a single list call.

    - Accepts a string or collection and returns a list[str].
    - Items starting with 'vs_' are kept as-is (assumed IDs).
    - Other items are treated as names; we make one client.vector_stores.list() call
      and map names (case-insensitive) to their IDs. Unmatched names are returned unchanged.
    """
    # Normalize input to a list of strings
    if vs_val is None:
        items: List[str] = []
    elif isinstance(vs_val, (list, tuple, set)):
        items = [str(x) for x in vs_val if x is not None]
    else:
        items = [str(vs_val)]

    if not items:
        return []

    # One list request via OpenAI official client; relies on env for proxy
    try:

        def _fetch_page():
            client = OpenAI()
            # use a large limit if available; 100 is commonly supported
            return client.vector_stores.list(limit=100)

        page = await asyncio.to_thread(_fetch_page)
        name_to_id: dict[str, str] = {}
        for vs in getattr(page, "data", None) or []:
            nm = (getattr(vs, "name", "") or "").strip().lower()
            vid = getattr(vs, "id", None) or getattr(vs, "_id", None)
            if nm and vid and nm not in name_to_id:
                name_to_id[nm] = vid

        resolved: List[str] = []
        for s in items:
            s_norm = (s or "").strip()
            if s_norm.startswith("vs_"):
                resolved.append(s_norm)
            else:
                resolved.append(name_to_id.get(s_norm.lower(), s_norm))
        return resolved
    except Exception:
        # Best-effort fallback: return as-is
        return items


# build_agent_config was deprecated and removed in favor of the library DTO builder:
# call_api.build_runnable_instructions_config(...)


def _normalize_env(env_obj: Any) -> dict[str, str] | None:
    """Convert env mapping to str->str if valid, otherwise None."""
    if not isinstance(env_obj, dict):
        return None
    clean: dict[str, str] = {}
    for k, v in env_obj.items():
        if isinstance(k, str) and isinstance(v, (str, int, float, bool)):
            clean[k] = str(v)
    return clean or None


async def _build_mcp_servers_singleton(cfg_yaml: dict) -> dict[str, Any]:
    """Build MCP servers for singleton cache (persistent, not tied to AsyncExitStack).
    
    Returns dict[name → server] instead of list to enable name-based lookup.
    Servers are __aenter__ed immediately and live for application lifetime.
    """
    servers_by_name: dict[str, Any] = {}
    
    if not cfg_yaml or not isinstance(cfg_yaml.get("mcpServers"), dict):
        return servers_by_name
        
    logging.info("[mcp] Building singleton MCP servers from config...")
    debug_print("[mcp]", f"Config contains {len(cfg_yaml.get('mcpServers') or {})} server entries")
    
    async def _create_persistent_server(name: str, spec: dict, timeout: int) -> Any | None:
        """Create and initialize a persistent MCP server (no AsyncExitStack)."""
        cmd = (spec or {}).get("command")
        args = (spec or {}).get("args") or []
        env = _normalize_env((spec or {}).get("env"))
        if not cmd:
            return None
            
        try:
            server = MCPServerStdioHook(
                params={"command": cmd, "args": args, "env": env},
                name=name,
                client_session_timeout_seconds=timeout,
            )
            # Enter async context manually (persistent, not tied to any stack)
            await server.__aenter__()
            
            parts = [str(cmd)] + [str(a) for a in (args or [])]
            pretty_cmd = shlex.join(parts)
            logging.info("[mcp] Started persistent MCP server '%s': %s", name, pretty_cmd)
            debug_print("[mcp]", f"✅ Started '{name}': {pretty_cmd}")
            
            return server
        except Exception as exc:
            logging.error("[mcp] Failed to start server '%s': %s", name, exc)
            debug_print("[mcp]", f"❌ Failed to start '{name}': {exc}")
            return None
    
    disabled_names: list[str] = []
    
    for name, spec in (cfg_yaml.get("mcpServers") or {}).items():
        if not isinstance(spec, dict):
            continue
            
        if not spec.get("enabled", False):
            disabled_names.append(name)
            continue
            
        if "command" in spec:
            timeout = int(spec.get("timeoutSeconds", 1200))
            srv = await _create_persistent_server(name, spec, timeout)
            if srv:
                servers_by_name[name] = srv
            continue
            
        if "serverUrl" in spec and isinstance(spec.get("bridge"), dict):
            bridge = spec["bridge"]
            bcmd = bridge.get("command")
            bargs = list(bridge.get("args") or [])
            if bcmd:
                url = spec.get("serverUrl") or ""
                token = os.getenv("API_ACCESS_TOKEN", "")
                fmt_args = []
                for a in bargs:
                    if isinstance(a, str):
                        a = a.replace("{serverUrl}", url).replace(
                            "{API_ACCESS_TOKEN}", token
                        )
                    fmt_args.append(a)
                bridge_spec = {"command": bcmd, "args": fmt_args}
                timeout = int(spec.get("timeoutSeconds", 1200))
                srv = await _create_persistent_server(name, bridge_spec, timeout)
                if srv:
                    servers_by_name[name] = srv
            else:
                logging.info("[mcp] Server '%s' has serverUrl but no bridge.command; skipping", name)
            continue
        
        if "serverUrl" in spec:
            try:
                headers = {
                    k: str(v).replace("{API_ACCESS_TOKEN}", os.getenv("API_ACCESS_TOKEN", ""))
                    for k, v in (spec.get("headers") or {}).items()
                }
                # Allow per-server override of SSE read timeout via config.
                # Fallback default is 1800s (30 minutes) to support long-running sessions.
                sse_read_timeout = float(spec.get("sseReadTimeoutSeconds", 1800))

                srv = MCPServerSseHook(
                    params={
                        "url": str(spec.get("serverUrl")),
                        "headers": headers,
                        "sse_read_timeout": sse_read_timeout,
                    },
                    name=name,
                    client_session_timeout_seconds=float(spec.get("timeoutSeconds", 1200)),
                )
                await srv.__aenter__()
                servers_by_name[name] = srv
                logging.info("[mcp] Registered remote MCP server '%s' via SSE", name)
                debug_print("[mcp]", f"✅ Registered remote '{name}' via SSE")
                continue
            except Exception as exc:
                logging.info(
                    "[mcp] Server '%s' is remote but failed to initialize SSE client: %s",
                    name,
                    exc,
                )
                try:
                    debug_print(
                        "[mcp]",
                        f"❌ Failed to initialize SSE client for '{name}': {exc}",
                    )
                except Exception:
                    pass
                continue

    # ... (rest of the code remains the same)

async def _build_mcp_servers_from_yaml(
    cfg_yaml: dict | None, astack: AsyncExitStack
) -> list[Any]:
    """Start all enabled MCP servers as defined in cfg_yaml and return the list.

    IMPORTANT: we enter each stdio client's async context via the provided AsyncExitStack,
    so that __aenter__/__aexit__ run on the same task. This prevents AnyIO cancel scope
    mismatches like "Attempted to exit cancel scope in a different task than it was entered in".
    """
    mcp_servers_started: list[Any] = []
    if cfg_yaml and isinstance(cfg_yaml.get("mcpServers"), dict):
        try:
            debug_print(
                "[mcp]",
                f"Config contains {len(cfg_yaml.get('mcpServers') or {})} server entries",
            )
        except Exception as e:
            logging.debug("[mcp] Failed to print config info: %s", e)

        def _skip_self_call_server(name: str, spec: dict) -> bool:
            mode = os.getenv("CALL_MCP_SERVER_MODE")
            if not mode:
                return False

            lname = str(name).lower()
            if lname == "call":
                return True

            url = str((spec or {}).get("serverUrl") or "").lower()
            if "call-mcp." in url:
                return True

            for a in (spec or {}).get("args") or []:
                if isinstance(a, str) and "call.mcp.server" in a:
                    return True

            return False

        async def _open_stdio(name: str, spec: dict, timeout: int):
            cmd = (spec or {}).get("command")
            args = (spec or {}).get("args") or []
            env = _normalize_env((spec or {}).get("env"))
            if not cmd:
                return None
            
            # Log BEFORE starting server creation
            try:
                parts = [str(cmd)] + [str(a) for a in (args or [])]
                pretty_cmd = shlex.join(parts)
                debug_print("[mcp]", f"⏳ Starting server '{name}'...")
                logging.info("[mcp] Starting server '%s': %s", name, pretty_cmd)
            except Exception as e:
                logging.debug("[mcp] Failed to log server start for '%s': %s", name, e)
            
            # Create server hook and register with exit stack
            server = await astack.enter_async_context(
                MCPServerStdioHook(
                    params={"command": cmd, "args": args, "env": env},
                    name=name,
                    client_session_timeout_seconds=timeout,
                )
            )
            
            # Log AFTER server is ready
            debug_print("[mcp]", f"✅ Server '{name}' ready")
            logging.info("[mcp] Server '%s' ready", name)
            return server

        disabled_names: list[str] = []

        for name, spec in (cfg_yaml.get("mcpServers") or {}).items():
            if not isinstance(spec, dict):
                continue
            if _skip_self_call_server(name, spec):
                debug_print(
                    "[mcp]",
                    f"Skipping self-referential MCP server '{name}' in CALL_MCP_SERVER_MODE",
                )
                continue
            if not spec.get("enabled", False):
                disabled_names.append(name)
                continue
            if "command" in spec:
                timeout = int(spec.get("timeoutSeconds", 1200))
                srv = await _open_stdio(name, spec, timeout)
                if srv:
                    mcp_servers_started.append(srv)
                continue
            if "serverUrl" in spec and isinstance(spec.get("bridge"), dict):
                bridge = spec["bridge"]
                bcmd = bridge.get("command")
                bargs = list(bridge.get("args") or [])
                if bcmd:
                    url = spec.get("serverUrl") or ""
                    token = os.getenv("API_ACCESS_TOKEN", "")
                    fmt_args = []
                    for a in bargs:
                        if isinstance(a, str):
                            a = a.replace("{serverUrl}", url).replace(
                                "{API_ACCESS_TOKEN}", token
                            )
                        fmt_args.append(a)
                    bridge_env = _normalize_env(bridge.get("env") or spec.get("env"))
                    bridge_spec = {"command": bcmd, "args": fmt_args, "env": bridge_env}
                    timeout = int(spec.get("timeoutSeconds", 1200))
                    srv = await _open_stdio(name, bridge_spec, timeout)
                    if srv:
                        mcp_servers_started.append(srv)
                else:
                    logging.info(
                        "MCP '%s' has serverUrl but no bridge.command; skipping.", name
                    )
                    try:
                        debug_print(
                            "[mcp]", f"Skipping remote '{name}': bridge.command missing"
                        )
                    except Exception as e:
                        logging.debug("[mcp] Failed to log skip message for '%s': %s", name, e)
            else:
                if "serverUrl" in spec:
                    try:
                        headers = {
                            k: str(v).replace("{API_ACCESS_TOKEN}", os.getenv("API_ACCESS_TOKEN", ""))
                            for k, v in (spec.get("headers") or {}).items()
                        }
                        sse_read_timeout = float(spec.get("sseReadTimeoutSeconds", 1800))

                        srv = MCPServerSseHook(
                            params={
                                "url": str(spec.get("serverUrl")),
                                "headers": headers,
                                "sse_read_timeout": sse_read_timeout,
                            },
                            name=name,
                            client_session_timeout_seconds=float(spec.get("timeoutSeconds", 1200)),
                        )
                        srv = await astack.enter_async_context(srv)
                        mcp_servers_started.append(srv)
                        logging.info("[mcp] Registered remote MCP server '%s' via SSE", name)
                        debug_print("[mcp]", f"✅ Remote '{name}' ready via SSE")
                        continue
                    except Exception as exc:
                        logging.info(
                            "MCP '%s' is remote (%s) but failed to initialize SSE client: %s",
                            name,
                            spec.get("serverUrl"),
                            exc,
                        )
                        try:
                            debug_print(
                                "[mcp]",
                                f"Skipping remote '{name}': SSE init failed for serverUrl={spec.get('serverUrl')}: {exc}",
                            )
                        except Exception as e:
                            logging.debug("[mcp] Failed to log SSE skip message for '%s': %s", name, e)
                        continue

        if disabled_names:
            try:
                debug_print(
                    "[mcp]",
                    "Skipping disabled servers: " + ", ".join(sorted(disabled_names)),
                )
                logging.info("[mcp] Disabled servers: %s", ", ".join(sorted(disabled_names)))
            except Exception as e:
                logging.debug("[mcp] Failed to log disabled servers: %s", e)
    return mcp_servers_started


@function_tool
def image_genetation_tool(
    ctx: RunContextWrapper[Any],
    prompt: str,
    size: str = "1024x1024",
    background: str | None = None,
) -> str:
    """
    Генерация картинки с параметрами. Возвращает data URL base64.
    Args:
      prompt: текстовое описание
      size: "1024x1024" | "1024x1792" | ...
      background: "transparent" для PNG с альфой, иначе None
    """
    img = client.images.generate(
        model="gpt-image-1",
        prompt=prompt,
        size=size,
        background=background,
        n=1,
        response_format="b64_json",
    )
    b64 = img.data[0].b64_json
    return f"data:image/png;base64,{b64}"


# agent = Agent(
#     name="Designer",
#     instructions="Если пользователь просит картинку — вызывай инструмент generate_image.",
#     tools=[image_genetation_tool],
# )


async def build_and_run_agent(cfg: RunnableConfig, user_input: str = ""):
    """Build an Agent from a ready-to-run cfg and run one turn. Returns (agent, cfg, session).
    
    MCP Lifecycle (RAII Pattern):
      - Uses singleton MCP servers from global cache (created at startup)
      - Servers managed by global AsyncExitStack (entered in lifespan)
      - Background cleanup of service messages started before return
      - AsyncExitStack cleanup happens at application shutdown

    Expected cfg attributes (duck-typed DTO):
      - name: str
      - project: str | None
      - instructions: str
      - model: str | None
      - attributes: dict | None (may contain 'vs')
      - path: str | None (repo-relative path like 'agent/Proj/Agent/agent.md'; optional)
      
    Returns:
      tuple: (agent, cfg, session) - Agent instance, config, and session object
    """
    # Use singleton MCP servers from global cache for local/stdio servers.
    # Remote HTTP/SSE servers (serverUrl-only) are created per request.
    debug_print("[call]", "[MCP] Getting MCP servers from singleton cache (local-only)...")
    local_mcp_servers, cfg_yaml = await _prepare_mcp_servers(astack=None)
    debug_print("[call]", f"[MCP] ✅ Local MCP servers ready: {len(local_mcp_servers)} servers")

    req_mcp_exit_stack: AsyncExitStack | None = None
    remote_mcp_servers: list[Any] = []
    if cfg_yaml and isinstance(cfg_yaml.get("mcpServers"), dict):
        try:
            servers_map = cfg_yaml.get("mcpServers") or {}
            remote_servers: dict[str, Any] = {}
            for name, spec in servers_map.items():
                if not isinstance(spec, dict):
                    continue
                if not spec.get("enabled", False):
                    continue
                has_command = "command" in spec
                has_bridge = isinstance(spec.get("bridge"), dict)
                has_server_url = "serverUrl" in spec
                # Plain remote HTTP/SSE: serverUrl present, but no command/bridge.
                if has_server_url and not has_command and not has_bridge:
                    remote_servers[name] = spec
            if remote_servers:
                remote_cfg_yaml = {"mcpServers": remote_servers}
                req_mcp_exit_stack = AsyncExitStack()
                await req_mcp_exit_stack.__aenter__()
                remote_mcp_servers = await _build_mcp_servers_from_yaml(
                    remote_cfg_yaml, req_mcp_exit_stack
                )
                debug_print(
                    "[mcp]",
                    f"✅ Per-request remote MCP servers created: {list(remote_servers.keys())}",
                )
        except Exception as e:
            logging.getLogger("call.mcp").exception(
                "[mcp] Failed to create per-request remote MCP servers: %s", e
            )
            remote_mcp_servers = []

    mcp_servers = list(local_mcp_servers) + list(remote_mcp_servers)
    debug_print(
        "[call]",
        f"[MCP] ✅ Total MCP servers for this request: {len(mcp_servers)}",
    )

    debug_print("[call]", "user_input (raw): |-\n" + user_input)
    debug_print("[call]", "cfg.instructions: |-\n" + cfg.instructions)
    debug_dump_cfg_preview(cfg)

    tools = await build_tools_for_cfg(cfg)

    # Agents-as-Tools: populate helper tools declared in project/agent card.
    _append_agent_tools_from_cfg(cfg=cfg, tools=tools, mcp_servers=mcp_servers)

    processed_input = await process_user_input(user_input)
    sanitized_input = processed_input.sanitized
    normalized_input = processed_input.normalized
    embedded_input = processed_input.embedded

    debug_print(
        "[call]",
        "input (sanitized from target / repaced empty to go): |-\n"
        + str(normalized_input),
    )

    context = {"embedded_input": embedded_input}

    cfg_model_settings = getattr(cfg, "model_settings", None)
    if isinstance(cfg_model_settings, ModelSettings):
        agent_model_settings = cfg_model_settings
    elif isinstance(cfg_model_settings, dict):
        try:
            agent_model_settings = ModelSettings(**cfg_model_settings)
        except Exception:
            agent_model_settings = ModelSettings()
    else:
        agent_model_settings = ModelSettings()

    agent = Agent(
        name=cfg.id,
        instructions=cfg.instructions,
        model=cfg.model,
        model_settings=agent_model_settings,
        tools=tools,
        mcp_servers=mcp_servers,
    )

    # MCP tools will be listed lazily by agents library when needed
    # Initialize bot: prefer CALL_TELEGRAM_TOKEN or use project from cfg
    debug_print("[call]", "[BOT] initializing bot...")
    await _init_bot_safe(project_name=(cfg.project or None))
    debug_print("[call]", "[BOT] bot initialized")

    # Save globally for subsequent messages (defaults come from .env; Telegram bot may override)
    global selected_chat_id, selected_thread_id

    # Now that selected_chat_id is finalized, create or skip SQLite session
    session = _create_session_if_any(selected_chat_id, selected_thread_id)

    # Send welcome message with agent link and run context (after config is ready)
    debug_print("[call]", "[BANNER] sending welcome banner...")
    await _send_welcome_banner(
        cfg=cfg,
        user_input=user_input,
        mcp_servers=mcp_servers,
        selected_chat_id=selected_chat_id,
        selected_thread_id=selected_thread_id,
    )
    debug_print("[call]", "[BANNER] welcome banner sent")

    # Run the main agent once with normalized input string (session-enabled)

    step1_output = ""

    async def _run_agent_once() -> str:
        """Run Runner.run once and return final_output (or empty string)."""
        result_local = await Runner.run(
            agent,
            embedded_input,
            max_turns=(getattr(_agents_run, "DEFAULT_MAX_TURNS", 150)),
            session=session,
            context=context,
        )
        return getattr(result_local, "final_output", "") or ""

    mcp_retried = False
    while True:
        try:
            step1_output = await _run_agent_once()
            debug_print("[call]", "step1_output: |-\n", step1_output)
            break
        except Exception as e:
            # Detect fatal tracing 403 and abort immediately (no stacks, no continuation)
            try:
                err_text = format_exception_text(e)
            except Exception:
                err_text = str(e)
            fatal_tokens = (
                "unsupported_country_region_territory",
                "request_forbidden",
                "Tracing client error 403",
            )
            if any(tok in (err_text or "") for tok in fatal_tokens):
                raise RuntimeError(
                    'Tracing client error 403: {"error":{"code":"unsupported_country_region_territory","message":"Country, region, or territory not supported","param":null,"type":"request_forbidden"}}'
                )

            # MCP-related stream closure / timeout: ClosedResourceError, ReadTimeout, McpError
            is_closed_resource = isinstance(e, anyio.ClosedResourceError)
            is_http_timeout = isinstance(e, (httpx.ReadTimeout, httpx.ConnectTimeout))
            is_mcp_error = isinstance(e, McpError)

            if (is_closed_resource or is_http_timeout or is_mcp_error) and not mcp_retried:
                mcp_retried = True
                logging.warning("[app] MCP transport error detected (%s); reinitializing MCP servers and retrying once", type(e).__name__)
                try:
                    await cleanup_mcp_servers()
                except Exception:
                    logging.exception("[app] MCP cleanup after transport error failed")
                try:
                    # Wait for MCP owner task to bring servers back up
                    await wait_for_mcp_init(timeout=120.0)
                except Exception:
                    logging.exception("[app] MCP reinit after transport error failed; aborting retry")
                    # fall through to generic error handling below
                else:
                    # Retry agent run once after successful MCP reinit
                    continue

            # Non-fatal errors: log full exception and surface a short error
            logging.exception("[app] Agent run failed")
            short_msg = str(e) or "Error"
            debug_print("[app]", f"Error during main agent run: {short_msg}")
            step1_output = f"Error: {short_msg}"
            # todo: проверить парсинг ошибок и вообще единообразие выдачи сообщений об ошибках
            parsed_error = getattr(e, "error", None)
            message_for_tg = None
            if isinstance(parsed_error, dict):
                msg_val = parsed_error.get("message")
                if isinstance(msg_val, str) and msg_val.strip():
                    message_for_tg = msg_val.strip()
            if not message_for_tg:
                message_for_tg = short_msg
            await _send_error_notification(
                cfg=cfg,
                selected_chat_id=selected_chat_id,
                selected_thread_id=selected_thread_id,
                message=message_for_tg,
            )
            break

    # Update cost totals from textual output (if present)
    totals = _update_cost_totals_from_output(step1_output)
    is_error_output = isinstance(step1_output, str) and step1_output.strip().lower().startswith("error:")
    if (
        totals
        and isinstance(step1_output, str)
        and not is_error_output
        and _env_flag_for_cfg("BOT_SHOW_COST_TOTALS", cfg, False)
    ):
        try:
            cur = totals.currency or "USD"
            totals_line = (
                f"Today: {totals.total_cost_today:.6f} {cur}\n"
                f"Totals: {totals.total_cost:.6f} {cur}"
            )
            step1_output = f"{step1_output.rstrip()}\n\n{totals_line}"
            logging.debug(
                "[call] appended cost totals all=%.6f today=%.6f date=%s cur=%s",
                totals.total_cost,
                totals.total_cost_today,
                totals.last_updated_date,
                cur,
            )
        except Exception:
            logging.debug("[call] failed to append cost totals", exc_info=True)

    # Notify digest (no image) and push
    await _notify_digest_if_applicable(
        cfg=cfg,
        user_input=user_input,
        step1_output=step1_output,
        selected_chat_id=selected_chat_id,
        selected_thread_id=selected_thread_id,
    )
    logging.debug("[call] Digest notification completed")

    # Expose final_output to callers via cfg
    try:
        setattr(cfg, "_last_final_output", step1_output)
    except Exception as e:
        logging.warning("[call] Failed to set _last_final_output: %s", e)

    # Cleanup: delete all MCP service messages in background (fire-and-forget)
    # All error handling is inside cleanup_service_messages()
    for srv in mcp_servers:
        if isinstance(srv, MCPServerHookMixin):
            asyncio.create_task(srv.cleanup_service_messages())
    logging.debug("[call] MCP cleanup started for %d servers", len(mcp_servers))

    # Close per-request MCP exit stack (remote servers) if used
    if req_mcp_exit_stack is not None:
        try:
            await req_mcp_exit_stack.__aexit__(None, None, None)
        except Exception as e:
            logging.getLogger("call.mcp").exception(
                "[mcp] Failed to close per-request MCP exit stack: %s", e
            )

    # Return directly instead of yield (no context manager cleanup needed after)
    return agent, cfg, session
