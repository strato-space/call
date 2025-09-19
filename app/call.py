from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any

@dataclass
class AgentConfig:
    """Lightweight config used by the runtime to run an Agent.

    Note: This mirrors the minimal fields the pipeline relies on and intentionally
    avoids coupling to external DTOs. It is sufficient for building the Agent instance.
    """
    name: str = ""
    instructions: str = ""
    model: str | None = None
    model_settings: Any | None = None
    vs_list: list[str] | None = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    agent_yaml_path: Path | None = None
    base_dir: Path | None = None
    _last_final_output: Any | None = None


def _parse_md_metadata_and_prompt(md_text: str) -> tuple[Dict[str, Any], str]:
    """Extract metadata YAML (between <!-- METADATA:START --> fenced ```yaml ... ```)
    and prompt body (prefer content between <!-- PROMPT:START --> and <!-- PROMPT:END -->).
    Falls back to the whole MD text as prompt instructions when tags are absent.
    """
    meta: Dict[str, Any] = {}
    body: str = md_text or ""
    try:
        start_tag = "<!-- METADATA:START -->"
        if start_tag in md_text:
            y0 = md_text.index(start_tag)
            y1 = md_text.index("```yaml", y0) + len("```yaml")
            y2 = md_text.index("```", y1)
            import yaml as _yaml
            meta = _yaml.safe_load(md_text[y1:y2]) or {}
            if not isinstance(meta, dict):
                meta = {}
    except Exception:
        meta = {}
    try:
        p0_tag = "<!-- PROMPT:START -->"
        p1_tag = "<!-- PROMPT:END -->"
        if p0_tag in md_text and p1_tag in md_text:
            p0 = md_text.index(p0_tag) + len(p0_tag)
            p1 = md_text.index(p1_tag, p0)
            body = md_text[p0:p1].strip()
    except Exception:
        body = (md_text or "").strip()
    return meta, body


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml as _yaml
        return _yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load_card(path: Path) -> tuple[Dict[str, Any], str, str]:
    """Load a YAML or MD card.
    Returns (attributes_dict, instructions_text, raw_dump_for_embed)
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return {}, "", ""
    if path.suffix.lower() in {".md", ".markdown"}:
        meta, body = _parse_md_metadata_and_prompt(text)
        raw_dump = text
        return meta, body, raw_dump
    # YAML
    data = _load_yaml(path)
    raw_dump = ""
    try:
        import yaml as _yaml
        raw_dump = _yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    except Exception:
        raw_dump = text
    instructions = str(data.get("instructions") or data.get("goal") or "")
    return (data if isinstance(data, dict) else {}), instructions, raw_dump

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
import logging
from typing import Optional, Dict, Any, List, Callable, Awaitable, Type, Union
import base64
import re
from contextlib import asynccontextmanager, ExitStack, AsyncExitStack
import urllib.parse
from pathlib import Path

import json
import tempfile
import yaml
import inspect
import httpx
from openai import OpenAI

# Import agent utilities (internal copy)
try:
    from .utils.agent_utils import extract_agent_attributes, get_agent_instructions
except ImportError:
    # Fallback for when running as script directly
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))
    from agent_utils import extract_agent_attributes, get_agent_instructions
import shutil

# Import HTML/Telegram text utilities from package-relative utils
from .utils.html_sanitizer import clean_html_for_telegram, clean_html_for_telegraph, minify_html_func
from .utils.telegram_text import (
    telegram_truncate_html_safe,
    telegram_truncate_markdown_safe,
    telegram_prepare_html,
    telegram_prepare_markdown,
)
from .utils.telegraph_utils import publish_results, create_telegrath_account


from mcp.types import CallToolResult

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
from agents.tool import FileSearchTool
from agents.run_context import RunContextWrapper
from agents.mcp import MCPServerStdio
from agents.model_settings import ModelSettings

 # Telegraph usage is handled via utils.telegraph_utils

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Bot, Message
from telegram.error import TelegramError, TimedOut, NetworkError, BadRequest
from telegram.request import HTTPXRequest
from telegram.constants import ParseMode, ChatAction
from dotenv import load_dotenv
from call.lib.logging import debug_print

load_dotenv(dotenv_path=str(_env_file), override=True)


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
            delay = base_delay * (2 ** attempt)
            # Apply jitter within ±jitter seconds
            if jitter:
                delay = max(0.0, delay + random.uniform(-jitter, jitter))
            try:
                await asyncio.sleep(delay)
            except Exception:
                # If sleep fails for some reason, proceed immediately
                pass
            attempt += 1

def ensure_env(var: str, default: str = None) -> str:
    """Return the sanitized value of environment variable or raise."""
    value = os.environ.get(var, default)
    if not value:
        raise EnvironmentError(f"Required environment variable {var} is not set")
    # Remove any whitespace and control characters
    if value:
        value = ''.join(char for char in value if char.isprintable() and not char.isspace())
    return value


# debug_print is imported from call.lib.logging

telegram_last_message: Optional[Message] = None
selected_chat_id: Optional[int] = None
selected_thread_id: Optional[int] = None
# When True, the pipeline must NOT create a SQLite session and must NOT send Telegram messages
force_no_session: bool = False
# Optional original Telegram message id to reply to
reply_to_message_id: Optional[int] = None

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
        s2 = ''.join(c for c in s if c.isdigit() or c == '-')
        return int(s2) if s2 and s2 != '-' else None
    except Exception as e:
        raise ValueError(f"Failed to parse Telegram ID from {env_var}: {e}")

# Get environment variables
telegram_token = ensure_env("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = get_telegram_chat_id("TELEGRAM_CHAT_ID")
TELEGRAM_SECOND_CHAT_ID = get_telegram_chat_id("TELEGRAM_SECOND_CHAT_ID")
telegrath_token = ensure_env("TELEGRAPH_TOKEN")
TELEGRAM_THREAD_ID = get_telegram_chat_id("TELEGRAM_THREAD_ID", "")
TELEGRAPH_TOKEN = ensure_env("TELEGRAPH_TOKEN")
OPENAI_API_KEY = ensure_env("OPENAI_API_KEY")
# Initialize selected chat/thread defaults from .env
selected_chat_id = TELEGRAM_CHAT_ID
selected_thread_id = TELEGRAM_THREAD_ID or None

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
    """Return TELEGRAM_TOKEN.<project_name> from environment.

    KISS: no suffix guessing, no default fallback. Raise if missing.
    The provided name should already be normalized by the caller (e.g., stripped of 'Bot').
    """
    if not project_name or not str(project_name).strip():
        raise ValueError("project_name is required")
    key = f"TELEGRAM_TOKEN.{project_name}"
    token = os.environ.get(key, "").strip()
    if not token:
        raise KeyError(f"Missing {key} in environment/.env")
    return token


async def init_bot(*, project_name: str | None = None):
    """Initialize (or re-initialize) the global Telegram bot.

    Behavior:
      - If project_name is provided, use TELEGRAM_TOKEN.<project_name>
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
    # 2) TELEGRAM_TOKEN.<ProjectName> when project_name is provided
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
        raise EnvironmentError("No Telegram token found: set TELEGRAM_TOKEN or TELEGRAM_TOKEN.<ProjectName>")

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
    _os.environ.setdefault("NO_PROXY", "api.telegram.org,*.telegram.org,*.stratospace.fun")
    _os.environ.setdefault("no_proxy", "api.telegram.org,*.telegram.org,*.stratospace.fun")
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

async def send_telegram_message(text: str, parse_mode: str = ParseMode.HTML, chat_id: str = None, message_thread_id: int = None) -> Optional[Message]:
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
        safe_text = clean_html_for_telegram(text) if parse_mode == ParseMode.HTML else (text or "")

        async def _op():
            return await bot.send_message(
                chat_id=chat_id or telegram_last_message.chat_id,
                text=safe_text,
                parse_mode=parse_mode,
                message_thread_id=message_thread_id or telegram_last_message.message_thread_id or None,
            )

        message = await async_retry(_op, retries=2, base_delay=1.0, jitter=0.2, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
        telegram_last_message = message
        debug_print(f"TG message sent id={message.message_id} chat={message.chat_id} thread={message.message_thread_id}")
        return message
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        raise e


async def send_digest_notification(
    *,
    text: str = None,
    chat_id: int | None = None,
    message_thread_id: int | None = None,
    agent_name: str | None = None,
    agent_path: str | Path | None = None,
    input_text: str | None = None,
    image_path: str | Path | None = None,
) -> Optional[Message]:
    """Send a digest message/photo to Telegram with sensible fallbacks.

    Arguments:
    - text: Optional main body text to send. If it is a non-empty string shorter than
      Telegram limits, it will be sent as-is (sanitized by downstream helpers).
      If it is empty/whitespace, we treat it as absent and send a minimal banner
      with the original input echoed. If it's 4000+ chars, we publish it to Telegraph
      and send a short banner with the resulting link.
    - chat_id: Explicit chat id to target. If None, falls back to the module-level
      `selected_chat_id` which is initialized from `.env` and may be overridden by
      the Telegram bot/lib facade.
    - message_thread_id: Explicit topic/thread id (for supergroups). If None, falls
      back to `selected_thread_id`.
    - agent_name: Resolved agent display name. Used for presentation (e.g., Telegraph
      title) and optional buttons macro substitutions.
    - agent_path: Path to `agent.yaml` (string or Path). If provided and exists,
      the function will try to read `buttons` section to build inline buttons; it
      also allows macro expansion for `{{digest_url}}` if a Telegraph link was created.
    - input_text: Original user input. When we need to fall back to a banner (no text
      or after publishing), this input is echoed within a <code> block for context.
    - image_path: If provided, the function sends a photo instead of a text message.
      The `text` parameter becomes the caption (sanitized and truncated to 1024 chars).

    Behavior:
    - Always uses the finalized chat/thread computed from explicit arguments or
      module-level selections to avoid races.
    - Performs safe HTML preparation/truncation in downstream helpers.
    - Builds inline buttons from `agent.yaml` if present. Macro `{{digest_url}}` is
      replaced with the generated Telegraph URL when applicable.

    Returns:
    - telegram.Message on success; None on failure (with error logged to stdout).
    """
    # Debug: print incoming args (avoid dumping large payloads)
    debug_print(
        "send_digest_notification args:",
        f"text_len={(len(text) if isinstance(text, str) else 'None')},",
        f"chat_id={chat_id}, message_thread_id={message_thread_id},",
        f"agent_name={agent_name}, agent_path={agent_path},",
        f"input_len={(len(input_text) if isinstance(input_text, str) else 'None')},",
        f"image_path={image_path}"
    )

    # If content is too long for Telegram, publish and use resulting URL
    local_url: str | None = None
    try:
        if (text is not None) and isinstance(text, str) and len(text) >= 4000:
            pub_title = (agent_name or "Agent")
            # Support both async and sync publish_results in tests/runtime
            if inspect.iscoroutinefunction(publish_results):
                local_url = await publish_results(title=pub_title, content=text)
            else:
                local_url = publish_results(title=pub_title, content=text)
            text = None  # switch to link mode
    except Exception:
        # On failure to publish, fall back to sending as-is (may get truncated by Telegram)
        pass

    # Normalize empty/whitespace-only text to None so we don't attempt to send an empty Telegram message.
    # This triggers the fallback banner below with optional input echo.
    try:
        if text is not None and isinstance(text, str) and not text.strip():
            text = None
    except Exception:
        # Best-effort only; if anything goes wrong, proceed with existing value
        pass

    # Prepare final text
    if text is None:
        text = f"📰 {local_url}" if local_url else "📰"
        if input_text:
            try:
                safe_input = (input_text or "")[:3800]
                text = text + f"\n<code>{safe_input}</code>"
            except Exception:
                pass

    debug_print(f"send_digest_notification publish_url={local_url}")

    # Try to load buttons configuration from agent.yaml and perform macro substitution
    keyboard = None
    try:
        resolved_yaml: Path | None = None
        if agent_path and Path(agent_path).exists():
            p = Path(agent_path)
            resolved_yaml = p if p.name.lower().endswith('.yaml') else (p / 'agent.yaml')
            if not resolved_yaml.exists():
                resolved_yaml = None
        if resolved_yaml is None:
            agent_name_exact = (agent_name or "").strip() if agent_name else None
            if agent_name_exact:
                try:
                    resolved_yaml = discover_agent_yaml(agent_name_exact)
                except Exception:
                    resolved_yaml = None
        if resolved_yaml:
            agent_cfg = _load_yaml(resolved_yaml) or {}
            btns = agent_cfg.get("buttons")
            if isinstance(btns, list) and btns:
                row = []
                for b in btns:
                    if not isinstance(b, dict):
                        continue
                    label = str(b.get("label", "")).strip() or "🔗"
                    link = str(b.get("url", "")).strip()
                    # Macro substitutions
                    if link:
                        safe_url = local_url or ""
                        link = link.replace("{{digest_url}}", safe_url)
                    if link:
                        row.append(InlineKeyboardButton(label, url=link))
                if row:
                    keyboard = [row]
    except Exception:
        # Silent fallback to static buttons below
        keyboard = None

    # If keyboard wasn't configured in agent.yaml, do not show any buttons

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    try:
        # If an image is provided, send it as a photo with optional caption
        # Determine effective chat/thread once to avoid races with globals
        eff_chat_id = chat_id if chat_id is not None else selected_chat_id
        eff_thread_id = message_thread_id if message_thread_id is not None else selected_thread_id

        if image_path:
            # Use the legacy function that tests monkeypatch; thread fallback is not needed in unit tests
            message_obj = await telegram_send_photo(
                image_path=image_path,
                caption=text,
                chat_id=eff_chat_id,
                message_thread_id=eff_thread_id,
                reply_markup=reply_markup,
            )
        else:
            # Use the legacy function that tests monkeypatch; parse mode is handled downstream
            message_obj = await telegram_send_message(
                text=text,
                chat_id=eff_chat_id,
                message_thread_id=eff_thread_id,
            )
        debug_print(f"send_digest_notification result=true publish_url={local_url}")
        return message_obj
    except Exception as e:
        debug_print("[app]", f"Error sending Telegram message/photo: {e}")
        return None


async def post_run_git_push(agent_name: str, user_input: str) -> None:
    """Commit and push changes in the prompt repo after the run.

    - Uses normalized agent_name resolved in the pipeline
    - Uses user_input as-is (preserve newlines)
    - No fallback names
    """
    try:
        prompt_repo = discover_prompt_repo()
        commit_msg = f"{agent_name} {user_input}"

        from asyncio.subprocess import PIPE

        async def _run_git(cmd: list[str]) -> tuple[int, bytes, bytes]:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(prompt_repo),
                stdout=PIPE,
                stderr=PIPE,
            )
            out, err = await proc.communicate()
            return proc.returncode, out, err

        # Check if there are any changes first; if none, return silently
        rc, out, _ = await _run_git(["git", "status", "--porcelain", "-uno"])
        if rc != 0:
            return  # fail silently per requirement to avoid stdout logging
        if out.strip() == b"":
            return  # no changes

        # Changes exist: add, commit, and push
        await _run_git(["git", "add", "-A", "."])
        rc_commit, _, _ = await _run_git(["git", "commit", "-m", commit_msg])
        if rc_commit == 0:
            await _run_git(["git", "push"])
        # else: nothing to commit; return silently
    except Exception:
        # Fail silently to avoid writing to stdout as per requirements
        return


async def telegram_send_message(chat_id: int = None, text: str = None, message_thread_id: int = None, reply_markup: InlineKeyboardMarkup = None):
    
    def _looks_like_markdown(s: str) -> bool:
        try:
            t = (s or "").strip()
            if not t:
                return False
            # Prefer HTML if explicit tags are present
            # This prevents strings like "<b>…</b>" from being treated as Markdown
            if "<b>" in t or "<i>" in t or "<a href=" in t:
                return False; 
            if "<" in t and ">" in t:
                return False
            # Strong signal: fenced code blocks -> Markdown
            if "```" in t:
                return True
            # Common Markdown cues
            md_markers = (
                "**", "__", "* ", "- ", "\n- ", "\n* ", "[`", "[`", "](http", "`", "```", "# ", "## ", "### ", "1. ", "\n1. "
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
        safe_text = (text or "")
        if len(safe_text) > 4096:
            safe_text = safe_text[: 4095] + "…"
        chosen_parse_mode = None

    # Determine effective chat/thread with consistent fallbacks
    # KISS: Only explicit args -> selected_* (selected_* already seeded from .env and updated once after agent load)
    eff_chat_id = chat_id if chat_id is not None else selected_chat_id
    eff_thread_id = message_thread_id if message_thread_id is not None else selected_thread_id

    # Ensure bot is ready before attempting to send
    await init_bot()

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
        try:
            if reply_to_message_id is not None:
                if _ReplyParameters:
                    kwargs["reply_parameters"] = _ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
                else:
                    kwargs["reply_to_message_id"] = reply_to_message_id
        except Exception:
            pass
        return await bot.send_message(**kwargs)
    try:
        debug_print(f"[TG] send_message parse_mode={chosen_parse_mode}")
        message = await async_retry(_op, retries=2, base_delay=1.0, jitter=0.2, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
    except BadRequest as e:
        # KISS: If Telegram can't parse, send plain text once.
        emsg = str(e).lower()
        if "parse" in emsg or "entity" in emsg:
            plain = (text or "")
            if len(plain) > 4096:
                plain = plain[: 4095] + "…"
            def _op_plain():
                return bot.send_message(
                    chat_id=eff_chat_id,
                    message_thread_id=eff_thread_id,
                    text=plain,
                    parse_mode=None,
                    reply_markup=reply_markup,
                )
            debug_print("[TG] BadRequest parse error, retrying as plain text")
            message = await async_retry(_op_plain, retries=1, base_delay=0.7, jitter=0.1, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
        elif "thread not found" in emsg:
            # Fallback: resend without thread id and without reply parameters
            debug_print("[TG] BadRequest thread not found, retrying without thread id via safe_send_message")
            message = await safe_send_message(chat_id=eff_chat_id, text=safe_text, parse_mode=chosen_parse_mode, reply_markup=reply_markup)
        else:
            raise
    return message


async def safe_send_photo(*, chat_id: int | None, image_path: str | Path, caption: str | None = None, message_thread_id: int | None = None, reply_markup: InlineKeyboardMarkup | None = None) -> Message:
    """Wrapper for bot.send_photo with 'thread not found' fallback and retry.

    - Applies HTML sanitization/truncation for captions similar to telegram_send_photo
    - On BadRequest 'thread not found', retries without thread id
    """
    eff_chat_id = chat_id if chat_id is not None else selected_chat_id
    eff_thread_id = message_thread_id if message_thread_id is not None else selected_thread_id
    # Prepare caption
    safe_caption = None
    parse_mode = None
    if caption:
        try:
            safe_caption, cmode = telegram_prepare_html(caption or "", 1024)
            parse_mode = ParseMode.HTML if cmode == "HTML" else None
        except Exception:
            safe_caption = (caption or "")
            if len(safe_caption) > 1024:
                safe_caption = safe_caption[: 1023] + "…"
            parse_mode = None
        # Truncate if needed
        try:
            MAX_CAPTION_LEN = 1024
            if parse_mode == ParseMode.HTML and safe_caption:
                safe_caption = telegram_truncate_html_safe(safe_caption, MAX_CAPTION_LEN)
            elif parse_mode == ParseMode.MARKDOWN and safe_caption:
                safe_caption = telegram_truncate_markdown_safe(safe_caption, MAX_CAPTION_LEN)
            else:
                if safe_caption and len(safe_caption) > MAX_CAPTION_LEN:
                    safe_caption = safe_caption[: MAX_CAPTION_LEN - 1] + "…"
        except Exception:
            pass
    async def _op():
        await init_bot()
        with open(image_path, 'rb') as f:
            return await bot.send_photo(chat_id=eff_chat_id, photo=f, caption=safe_caption, parse_mode=parse_mode, message_thread_id=eff_thread_id, reply_markup=reply_markup)
    try:
        return await async_retry(_op, retries=2, base_delay=1.0, jitter=0.2, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
    except BadRequest as e:
        if "thread not found" in str(e).lower():
            async def _op_no_thread():
                await init_bot()
                with open(image_path, 'rb') as f:
                    return await bot.send_photo(chat_id=eff_chat_id, photo=f, caption=safe_caption, parse_mode=parse_mode, reply_markup=reply_markup)
            debug_print("[TG] BadRequest thread not found, retrying photo without thread id")
            return await async_retry(_op_no_thread, retries=1, base_delay=0.7, jitter=0.1, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
        raise

async def safe_send_message(*, chat_id: int | None, text: str, message_thread_id: int | None = None, parse_mode: str | None = None, reply_markup: InlineKeyboardMarkup | None = None, reply_to_message_id: int | None = None) -> Message:
    """Wrapper around bot.send_message with retry and 'thread not found' fallback.

    - Honors reply_to_message_id via ReplyParameters when available.
    - On BadRequest 'thread not found', retries without message_thread_id and without reply params.
    """
    await init_bot()
    try:
        from telegram import ReplyParameters as _ReplyParameters
    except Exception:
        _ReplyParameters = None
    async def _op():
        kwargs = dict(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        if reply_to_message_id is not None:
            if _ReplyParameters:
                kwargs["reply_parameters"] = _ReplyParameters(message_id=reply_to_message_id, allow_sending_without_reply=True)
            else:
                kwargs["reply_to_message_id"] = reply_to_message_id
        return await bot.send_message(**kwargs)
    try:
        return await async_retry(_op, retries=2, base_delay=1.0, jitter=0.2, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
    except BadRequest as e:
        if "thread not found" in str(e).lower():
            async def _op_no_thread():
                return await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
            return await async_retry(_op_no_thread, retries=1, base_delay=0.7, jitter=0.1, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
        raise


async def telegram_send_photo(image_path: str | Path, caption: str | None = None, chat_id: int | None = None, message_thread_id: int | None = None, reply_markup: InlineKeyboardMarkup | None = None) -> Message:
    """Send a photo to Telegram with optional caption, using global selected chat/thread.

    - Applies the same HTML sanitization for captions as messages
    - Uses async_retry for robustness
    """
    # Determine effective chat/thread
    eff_chat_id = chat_id if chat_id is not None else selected_chat_id
    eff_thread_id = message_thread_id if message_thread_id is not None else selected_thread_id

    safe_caption = None
    if caption:
        # KISS: Always prepare caption as HTML
        try:
            safe_caption, cmode = telegram_prepare_html(caption or "", 1024)
            parse_mode = ParseMode.HTML if cmode == "HTML" else None
        except Exception:
            safe_caption = (caption or "")
            if len(safe_caption) > 1024:
                safe_caption = safe_caption[: 1023] + "…"
            parse_mode = None
    else:
        parse_mode = None

    # Telegram Bot API caption length limit safety clamp (avoid BadRequest)
    try:
        MAX_CAPTION_LEN = 1024
        if parse_mode == ParseMode.HTML and safe_caption:
            safe_caption = telegram_truncate_html_safe(safe_caption, MAX_CAPTION_LEN)
        elif parse_mode == ParseMode.MARKDOWN and safe_caption:
            safe_caption = telegram_truncate_markdown_safe(safe_caption, MAX_CAPTION_LEN)
        else:
            if safe_caption and len(safe_caption) > MAX_CAPTION_LEN:
                safe_caption = safe_caption[: MAX_CAPTION_LEN - 1] + "…"
    except Exception:
        # Best-effort; on any error, fall back to original caption
        pass

    # Delegate to safe helper for consistency
    return await safe_send_photo(chat_id=eff_chat_id, image_path=image_path, caption=safe_caption, message_thread_id=eff_thread_id, reply_markup=reply_markup)


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
        digits = ''.join(ch for ch in s if ch.isdigit())
        if not digits:
            return None
        # If it was negative but not -100, just int-cast
        if s.startswith('-') and not s.startswith('-100'):
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
    agent_yaml_path: str | Path | None,
    user_input: str,
    mcp_servers_started: list[Any] | None,
    vs_list: list[str] | None,
    model: str | None = None,
) -> str:
    """Compose the Telegram welcome banner HTML.

    Output format:
      🍴 <b><a href='github-path'>AgentName</a></b>  (falls back to bold if URL missing)
      <code>input[:3800]</code>
      <code>mcp: [...]</code>
      <code>vs: [...]</code>
    """
    title = (agent_name or "Agent").strip() or "Agent"
    gh_url = github_blob_url(agent_yaml_path) if agent_yaml_path else None
    header = f"🔌 <b><a href='{gh_url}'>{title}</a></b>" if gh_url else f"🔌 <b>{title}</b>"

    preview = (user_input or "").strip()
    # Try to pretty print JSON payloads for readability
    pretty_preview: str | None = None
    try:
        if preview and (preview.startswith("{") or preview.startswith("[")):
            import json as _json
            obj = _json.loads(preview)
            pretty = _json.dumps(obj, ensure_ascii=False, indent=2)
            # Clamp to safe length
            if len(pretty) > 3600:
                pretty = pretty[:3597] + "..."
            # Escape for HTML inside code block and add Telegram-supported language tag
            import html as _html
            pretty_preview = f"<pre><code class=\"language-json\">{_html.escape(pretty)}</code></pre>"
    except Exception:
        pretty_preview = None
    if not pretty_preview:
        if len(preview) > 3800:
            preview = preview[:3797] + "..."

    # Collect MCP server names (best-effort)
    mcp_names: list[str] = []
    try:
        for srv in (mcp_servers_started or []):
            nm = getattr(srv, 'name', None) or getattr(srv, 'id', None) or type(srv).__name__
            if nm and str(nm) not in mcp_names:
                mcp_names.append(str(nm))
    except Exception:
        pass

    vs_ids = list(vs_list or []) if isinstance(vs_list, list) else []

    parts = [header]
    # Build preview line and attrs lines separately to control spacing
    preview_line = pretty_preview if pretty_preview else (f"<code>{preview}</code>" if preview else None)
    attr_lines: list[str] = []
    if mcp_names:
        attr_lines.append(f"<code>mcp: {mcp_names}</code>")
    if vs_ids:
        attr_lines.append(f"<code>vs: {vs_ids}</code>")
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
        chat_id = chat_id if chat_id is not None else _normalize_chat_id(tg.get("chat_id"))
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
                base = merged.get("tg", {}) if isinstance(merged.get("tg"), dict) else {}
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
    async def _op():
        return await bot.edit_message_text(
            chat_id=telegram_last_message.chat_id,
            message_id=telegram_last_message.message_id,
            text=text,
            parse_mode="HTML")
    await async_retry(_op, retries=2, base_delay=1.0, jitter=0.2, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))

class MCPServerStdioHook(MCPServerStdio):
    """Wrapper for MCPServerStdio that writes per-instance logs to Telegram.

    Each instance maintains its own editable Telegram message. On first write,
    a new message is created; subsequent writes edit that message. The MCP name
    is printed at the top of the message.
    """
    from typing import Any

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Per-instance last message holder
        self.__telegram_last_message: Optional[Message] = None
        # Cache last cleaned+truncated text to avoid redundant edits
        self.__last_tg_text: Optional[str] = None
        # Try to derive a readable MCP title
        self._mcp_title: str = (
            str(getattr(self, 'name', '') or '').strip()
            or str(getattr(self, 'id', '') or '').strip()
            or type(self).__name__
        )

    @staticmethod
    def _progress_bar(thoughtNumber: int, totalThoughts: int, bar_length: int = 10) -> str:
        """Render a compact progress bar strictly for Sequential Thinking updates."""
        try:
            tn = max(0, int(thoughtNumber))
            tt = max(1, int(totalThoughts))
            filled = int(bar_length * tn / tt)
            bar = "█" * filled + "░" * (bar_length - filled)
            return f"{bar} {tn}/{tt}"
        except Exception:
            return f"{thoughtNumber}/{totalThoughts}"

    async def __send_message(self, text: str) -> Message:
        """Send a new Telegram message for this MCP instance and cache it."""
        # Prefix with MCP title and sanitize; use common send path with consistent target selection
        header = f"<b>{self._mcp_title}</b>\n\n"
        safe_text = header + (text or "")
        # Clean and truncate to avoid Telegram 4096 limit and user's 3800 limit
        
        if len(cleaned) > 3800:
            cleaned = cleaned[:3797] + "..."
        cleaned = '<code>' + cleaned(safe_text) + '</code>'
        msg = await safe_send_message(chat_id=selected_chat_id, message_thread_id=selected_thread_id, text=cleaned, parse_mode=ParseMode.HTML)
        self.__telegram_last_message = msg
        self.__last_tg_text = cleaned
        return msg

    async def __edit_message_text(self, text: str) -> None:
        """Edit this instance's message; if missing, send a new one."""
        header = f"<b>{self._mcp_title}</b>\n\n"
        safe_text = header + (text or "")
        if not self.__telegram_last_message:
            await self.__send_message(safe_text)
            return
        # Clean and truncate
        cleaned = clean_html_for_telegram(safe_text)
        if len(cleaned) > 3800:
            cleaned = cleaned[:3797] + "..."
        # Skip edit if content is unchanged (prevents BadRequest: Message is not modified)
        if self.__last_tg_text == cleaned:
            return
        async def _op():
            return await bot.edit_message_text(
                chat_id=self.__telegram_last_message.chat_id,
                message_id=self.__telegram_last_message.message_id,
                text=cleaned,
                parse_mode=ParseMode.HTML,
            )
        try:
            await async_retry(_op, retries=2, base_delay=1.0, jitter=0.2, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
            self.__last_tg_text = cleaned
        except BadRequest as br:
            # Ignore 'Message is not modified' just in case race conditions occur
            if 'Message is not modified' in str(br):
                return
            raise

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None) -> CallToolResult:
        debug_print(f"[MCP Hook] Calling tool: {tool_name}")
        # Bind parent method to avoid 'super(): no arguments' inside nested closures
        parent_call_tool = super(MCPServerStdioHook, self).call_tool
        # Try to present arguments in YAML for readability (console)
        def _to_yaml_text(obj) -> str:
            """Dump arguments to YAML with better readability:
            - Convert literal "\\n" sequences inside strings to real newlines
            - Use YAML block style (|) for multiline strings
            """
            def _deep_unescape(o):
                if isinstance(o, str):
                    # Only basic escapes to improve readability
                    return o.replace("\\n", "\n").replace("\\t", "\t")
                if isinstance(o, list):
                    return [_deep_unescape(i) for i in o]
                if isinstance(o, dict):
                    return {k: _deep_unescape(v) for k, v in o.items()}
                return o

            class _BlockStrDumper(yaml.SafeDumper):
                pass

            def str_representer(dumper, data):
                style = '|' if ('\n' in data) else None
                return dumper.represent_scalar('tag:yaml.org,2002:str', data, style=style)

            _BlockStrDumper.add_representer(str, str_representer)

            try:
                prepared = _deep_unescape(obj or {})
                return yaml.dump(prepared, Dumper=_BlockStrDumper, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)
            except Exception:
                try:
                    # JSON roundtrip with default=str to sanitize non-serializable objects
                    json_text = json.dumps(obj or {}, ensure_ascii=False, indent=2, default=str)
                    prepared = _deep_unescape(json.loads(json_text))
                    return yaml.dump(prepared, Dumper=_BlockStrDumper, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)
                except Exception:
                    # Last resort: pretty JSON string (also unescape newlines)
                    try:
                        s = json.dumps(obj or {}, ensure_ascii=False, indent=2, default=str)
                        return s.replace("\\n", "\n").replace("\\t", "\t")
                    except Exception:
                        return str(obj)

        yaml_args = _to_yaml_text(arguments)
        debug_print("[MCP Hook] Arguments (YAML):\n" + yaml_args)

        if tool_name != 'sequentialthinking':
            # For all other tools: send/edit YAML arguments in Telegram without progress bar
            yaml_text = _to_yaml_text(arguments)

            body = f"🛠️ {tool_name}\n\n{yaml_text}".strip()
            # Ensure message exists, then edit
            if self.__telegram_last_message is None:
                await self.__send_message(body)
            else:
                await self.__edit_message_text(body)
            try:
                async def _call():
                    return await parent_call_tool(tool_name, arguments)
                return await async_retry(_call, retries=1, base_delay=1.0, jitter=0.2, retry_on=(httpx.TimeoutException, OSError))
            except Exception as e:
                err_text = format_exception_text(e)
                try:
                    await self.__edit_message_text(f"❌ Error in {tool_name}\n\n" + err_text)
                except Exception:
                    pass
                raise
        try:
            thought = arguments['thought']
            # Determine counters safely
            tn = int((arguments or {}).get('thoughtNumber') or 0)
            tt = int((arguments or {}).get('totalThoughts') or 0)

            # On first write, send a banner-only message without progress bar
            if self.__telegram_last_message is None:
                input_text = (arguments or {}).get('input') or (arguments or {}).get('user_input') or (arguments or {}).get('prompt')
                banner_lines = [f"🔌 {self._mcp_title}"]
                if input_text:
                    try:
                        safe_input = str(input_text)[:1000]
                        banner_lines.append(f"{safe_input}")
                    except Exception:
                        pass
                await self.__send_message("\n".join(banner_lines))
                # Do not display progress bar on the very first tick
                if tn <= 0:
                    return await super().call_tool(tool_name, arguments)

            # Show progress bar only for actual progress (tn >= 1)
            if tn >= 1 and tt >= 1:
                bar = self._progress_bar(tn, tt)
                text = f"<b>💭Thinking: {bar}</b>\n\n{thought}\n\n<b>💭Thinking: {bar}</b>"
            else:
                text = str(thought)
            await self.__edit_message_text(text)

            # Send typing action on the same chat/thread when possible
            try:
                msg = self.__telegram_last_message
                if msg:
                    async def _op():
                        return await bot.send_chat_action(chat_id=msg.chat_id,
                                                           message_thread_id=msg.message_thread_id,
                                                           action=ChatAction.TYPING)
                    try:
                        await async_retry(_op, retries=1, base_delay=0.5, jitter=0.1, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
                    except BadRequest as br:
                        # Fallback: retry without thread id if not a forum topic
                        if 'thread not found' in str(br).lower():
                            async def _op_no_thread():
                                return await bot.send_chat_action(chat_id=msg.chat_id,
                                                                   action=ChatAction.TYPING)
                            await async_retry(_op_no_thread, retries=1, base_delay=0.5, jitter=0.1, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
                        else:
                            raise
            except Exception:
                pass

            try:
                async def _call():
                    return await parent_call_tool(tool_name, arguments)
                result = await async_retry(_call, retries=1, base_delay=1.0, jitter=0.2, retry_on=(httpx.TimeoutException, OSError))
                debug_print(f"[MCP Hook] Tool {tool_name} completed successfully")
                return result
            except Exception as e:
                err_text = format_exception_text(e)
                try:
                    await self.__edit_message_text(f"❌ Error in {tool_name}\n\n" + err_text)
                except Exception:
                    pass
                raise
        except Exception as e:
            print(f"[MCP Hook] Error in tool {tool_name}: {str(e)}")
            raise


# -------- Call subsystem helpers --------
# KISS policy: names are treated as-is (case-sensitive). No normalization helpers.


def discover_prompt_repo() -> Path:
    """Locate prompt repository root.
    Priority: env PROMPT_REPO -> sibling '../prompt' -> workspace default.
    """
    env_repo = os.environ.get('PROMPT_REPO')
    if env_repo and Path(env_repo).exists():
        return Path(env_repo)
    # try sibling 'prompt' at workspace root
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / 'prompt',  # .../PycharmProjects/prompt
        here.parents[1] / 'prompt',  # .../call/prompt (if copied inside)
        Path('c:/Users/Leader/PycharmProjects/prompt')
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("Prompt repository not found. Set PROMPT_REPO env to its path.")


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
        agents_map = data.get('agents') or {}
        # Optional explicit aliases mapping: { AgentName: [alias1, alias2, ...] }
        aliases_map = data.get('aliases') or {}
        if isinstance(agents_map, dict):
            for name in agents_map.keys():
                name_key = str(name)
                # resolve to actual directory casing if present
                agent_dir = _resolve_dir_case(base_dir, name_key)
                path = (agent_dir / 'agent.yaml')
                if path.exists():
                    mapping[name_key] = path
                # bind aliases
                if isinstance(aliases_map, dict):
                    for alias in (aliases_map.get(name) or aliases_map.get(name_key) or []):
                        alias_key = str(alias)
                        if alias_key and path.exists():
                            mapping[alias_key] = path
    except Exception:
        # Non-fatal: fallback to directory scan later
        return {}
    return mapping


def _scan_agents_dir(base_dir: Path) -> dict[str, tuple[Path, list[str]]]:
    """Scan a directory for subfolders with agent.yaml.

    Returns mapping: AgentName -> (agent_yaml_path, aliases[])
    """
    result: dict[str, tuple[Path, list[str]]] = {}
    if not base_dir.exists():
        return result
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        ay = child / 'agent.yaml'
        if ay.exists():
            try:
                y = load_yaml(ay) or {}
                name = str(y.get('id') or y.get('name') or child.name)
                aliases = []
                raw_aliases = y.get('aliases') or []
                if isinstance(raw_aliases, list):
                    aliases = [str(a) for a in raw_aliases if str(a).strip()]
                result[name] = (ay, aliases)
            except Exception:
                result[child.name] = (ay, [])
    return result

# legacy _ensure_indices removed; centralized in call.lib.discovery._ensure_indices

"""Centralized discovery is provided by call.lib.discovery.discover_agent_yaml; use the top-of-file wrapper."""


def _resolve_output_file_path(agent_yaml_path: Path | None, file_name: str) -> Path:
    """Resolve an output file path for an agent.

    Preference:
    - <agent_dir>/memories/<file_name> if 'memories/' exists
    - else <agent_dir>/memory/<file_name> if 'memory/' exists
    - else <agent_dir>/<file_name>
    """
    base_dir = (agent_yaml_path.parent if agent_yaml_path else Path('.')).resolve()
    cand1 = base_dir / 'memories'
    cand2 = base_dir / 'memory'
    if cand1.exists() and cand1.is_dir():
        return (cand1 / file_name).resolve()
    if cand2.exists() and cand2.is_dir():
        return (cand2 / file_name).resolve()
    return (base_dir / file_name).resolve()


# image generation tool factory removed


def load_yaml(path: Path) -> dict:
    """Simple YAML loader."""
    import yaml
    with open(path, 'r', encoding='utf-8') as f:
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
        stack.append({
            "file": os.fspath(fr.filename),
            "line": fr.lineno,
            "function": fr.name,
            "code": (fr.line or "")
        })
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
    def from_yaml_file(cls, yaml_path: str | Path) -> 'AgentDTO':
        """Load AgentDTO from YAML file."""
        import yaml
        path = Path(yaml_path)
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        return cls(data, base_dir=path.parent)
    
    def __init__(self, raw: dict, base_dir: Path | None = None):
        # Store raw and base path
        self.raw: dict = raw or {}
        self.base_dir: Path | None = base_dir
        # Basic identity
        self.id: str | None = self.raw.get('id')
        self.name: str | None = self.raw.get('name') or self.id
        # Model fields
        self.model: str | None = self.raw.get('model') or self.raw.get('llm')
        self.instructions: str | None = self.raw.get('instructions')
        # Prompt references can be list/str/dict
        self.prompts = self.raw.get('prompts') or self.raw.get('prompt') or self.raw.get('prompt_file')
        # Extract model settings and general attributes
        self.model_settings = self._extract_model_settings()
        self.attributes: dict = {}
        used_keys = {
            'id', 'name', 'model', 'llm', 'instructions', 'prompt', 'prompts', 'prompt_file',
            'model_settings', 'modelSettings'
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
                'temperature', 'top_p', 'frequency_penalty', 'presence_penalty', 'tool_choice',
                'parallel_tool_calls', 'truncation', 'max_tokens', 'reasoning', 'metadata',
                'store', 'include_usage'
            }
            return {k: src.get(k) for k in keys if k in src}

        ms_dict = {}
        # common places
        for key in ('model_settings', 'modelSettings', 'settings'):
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
            temperature=to_float(ms_dict.get('temperature')),
            top_p=to_float(ms_dict.get('top_p')),
            frequency_penalty=to_float(ms_dict.get('frequency_penalty')),
            presence_penalty=to_float(ms_dict.get('presence_penalty')),
            tool_choice=ms_dict.get('tool_choice'),
            parallel_tool_calls=ms_dict.get('parallel_tool_calls'),
            truncation=ms_dict.get('truncation'),
            max_tokens=ms_dict.get('max_tokens'),
            reasoning=ms_dict.get('reasoning'),
            metadata=ms_dict.get('metadata'),
            store=ms_dict.get('store'),
            include_usage=ms_dict.get('include_usage'),
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
                    p = (self.base_dir / 'agent.yaml')
                    if p.exists():
                        return p.read_text(encoding='utf-8'), self.attributes
            except Exception:
                pass
            return "", self.attributes
        
        # We have prompts - use first one
        first_prompt = self.getDefaultPrompt()
        if isinstance(first_prompt, dict):
            instructions = first_prompt.get('instructions', '').strip()
            if instructions:
                # Merge prompt attributes with agent attributes (prompt has priority)
                merged_attrs = dict(self.attributes)
                merged_attrs.update(first_prompt)
                return instructions, merged_attrs
            else:
                # Empty instructions - fallback to agent.yaml
                try:
                    if self.base_dir:
                        p = (self.base_dir / 'agent.yaml')
                        if p.exists():
                            return p.read_text(encoding='utf-8'), self.attributes
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
        if 'model' not in enriched and self.model:
            enriched['model'] = self.model
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

        has_aliases = 'aliases' in enriched and enriched.get('aliases') not in (None, [])
        has_alias = 'alias' in enriched and enriched.get('alias') not in (None, [])

        # Do NOT inherit aliases from agent if prompt lacks them.
        # Only mirror between forms if one exists in the prompt.
        if has_aliases and not has_alias:
            enriched['alias'] = _to_list(enriched.get('aliases')) or []
        if has_alias and not has_aliases:
            enriched['aliases'] = _to_list(enriched.get('alias')) or []
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
            base = self.base_dir or Path('.')
            path = (base / file_name).resolve()
            if not path.exists():
                return None
            import yaml  # lazy
            with open(path, 'r', encoding='utf-8') as f:
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
        base = self.base_dir or Path('.')
        # If target already has extension, check directly
        t = target.strip()
        p = (base / t).resolve()
        if p.exists() and p.is_file():
            return p
        # Try with .yaml and .yml if no extension given
        if '.' not in Path(t).name:
            for ext in ('.yaml', '.yml'):
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
                    self._register_prompt(str(name), prompt_obj, is_default_candidate=True)
                    break  # Only use first one
            return
        
        if not first_prompt_name:
            return
        
        # Try loading first_prompt_name as .md or .yaml
        base = self.base_dir or Path('.')
        for ext in ['.md', '.yaml', '.yml']:
            prompt_path = base / f"{first_prompt_name}{ext}"
            if prompt_path.exists():
                try:
                    if ext == '.md':
                        with open(prompt_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        data = {"instructions": content}
                    else:  # .yaml or .yml
                        data = self._load_prompt_file(str(prompt_path))
                    
                    if data:
                        self._register_prompt(first_prompt_name, data, is_default_candidate=True)
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
        for vs in (getattr(page, "data", None) or []):
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
# call.lib.api.build_runnable_instructions_config(...)


async def _build_mcp_servers_from_yaml(cfg_yaml: dict | None, astack: AsyncExitStack) -> list[Any]:
    """Start all enabled MCP servers as defined in cfg_yaml and return the list.

    IMPORTANT: we enter each stdio client's async context via the provided AsyncExitStack,
    so that __aenter__/__aexit__ run on the same task. This prevents AnyIO cancel scope
    mismatches like "Attempted to exit cancel scope in a different task than it was entered in".
    """
    mcp_servers_started: list[Any] = []
    if cfg_yaml and isinstance(cfg_yaml.get("mcpServers"), dict):

        async def _open_stdio(name: str, spec: dict, timeout: int):
            cmd = (spec or {}).get("command")
            args = (spec or {}).get("args") or []
            if not cmd:
                return None
            # Use our Telegram-integrated hook and ensure lifecycle is tied to astack
            server = await astack.enter_async_context(
                MCPServerStdioHook(
                    params={"command": cmd, "args": args},
                    name=name,
                    client_session_timeout_seconds=timeout,
                )
            )
            return server

        for name, spec in (cfg_yaml.get("mcpServers") or {}).items():
            if not isinstance(spec, dict):
                continue
            if not spec.get("enabled", False):
                continue
            if "command" in spec:
                timeout = int(spec.get("timeoutSeconds", 120))
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
                            a = a.replace("{serverUrl}", url).replace("{API_ACCESS_TOKEN}", token)
                        fmt_args.append(a)
                    bridge_spec = {"command": bcmd, "args": fmt_args}
                    timeout = int(spec.get("timeoutSeconds", 120))
                    srv = await _open_stdio(name, bridge_spec, timeout)
                    if srv:
                        mcp_servers_started.append(srv)
                else:
                    logging.info("MCP '%s' has serverUrl but no bridge.command; skipping.", name)
            else:
                if "serverUrl" in spec:
                    logging.info("MCP '%s' is remote (%s) but no bridge is defined; skipping.", name, spec.get("serverUrl"))
    return mcp_servers_started


@asynccontextmanager
async def build_and_run_agent(cfg, user_input: str = ""):
    """Async context manager that builds an Agent from a ready-to-run cfg and runs one turn.

    Expected cfg attributes (duck-typed DTO):
      - name: str
      - project: str | None
      - instructions: str
      - model: str | None
      - attributes: dict | None (may contain 'vs')
      - agent_yaml_path: str | None
    """
    # Optional YAML config to control MCP servers
    cfg_yaml: dict | None = None
    yaml_path = _call_dir / "mcp_config.yaml"
    # Start MCP servers only if we have a meaningful selection (KISS)
    name_hint = str(cfg.name or "").strip()
    proj_hint = str(cfg.project or "").strip()
    prompt_hint = str(getattr(cfg, "prompt_override", "") or "").strip()
    should_start_mcp = bool(name_hint or proj_hint or prompt_hint)
    if should_start_mcp and yaml_path.exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                cfg_yaml = yaml.safe_load(f) or {}
        except Exception:
            cfg_yaml = None

    async with AsyncExitStack() as astack:
        servers = []

        def _spec(name: str) -> dict | None:
            if not cfg_yaml:
                return None
            return (cfg_yaml.get("mcpServers") or {}).get(name)

        # Start ALL enabled servers from YAML via helper (lifecycle bound to astack)
        mcp_servers_started: list[Any] = []
        if should_start_mcp and cfg_yaml:
            try:
                mcp_servers_started = await _build_mcp_servers_from_yaml(cfg_yaml, astack)
            except FileNotFoundError:
                # Graceful: skip missing commands on Windows
                mcp_servers_started = []
            except Exception:
                mcp_servers_started = []

        # Build tools based on cfg.attributes.vs (resolve via vector store index)
        tools = [WebSearchTool()]
        try:
            vs_ids = await resolve_vector_stores((cfg.attributes or {}).get("vs"))
            if vs_ids:
                tools.append(FileSearchTool(vector_store_ids=vs_ids))
        except Exception:
            pass
        # If YAML provided, use what we started; otherwise none
        mcp_servers = mcp_servers_started

        # Debug: print instructions length and a short preview
        try:
            _instr = cfg.instructions or ""
            _instr_preview = _instr[:4096] + ("…" if len(_instr) > 4096 else "")
            debug_print("[app]", "Agent instructions len=", str(len(_instr)))
            debug_print("[app]", "Agent instructions preview=\n" + _instr_preview)
        except Exception:
            pass

        # Agents-as-Tools: if the project card exposes 'agents' or 'prompts',
        # create sub-agents as tools so the main agent can call them.
        try:
            from call.lib.api import build_runnable_instructions_config as _build_cfg
        except Exception:
            _build_cfg = None  # graceful

        try:
            # Snapshot current toolset to avoid recursive growth
            base_tools_snapshot = list(tools)
            entries: list[tuple[str, str]] = []
            attrs = (cfg.attributes or {}) if hasattr(cfg, 'attributes') else {}
            # 'agents' may be a dict name->description
            ag_map = attrs.get('agents') if isinstance(attrs, dict) else None
            if isinstance(ag_map, dict):
                for nm, desc in ag_map.items():
                    try:
                        entries.append((str(nm), str(desc) if desc is not None else ""))
                    except Exception:
                        continue
            # 'prompts' may be a list or dict; prefer keys/names
            pr_map = attrs.get('prompts') if isinstance(attrs, dict) else None
            try:
                debug_print("[tools]",
                            f"Scanning project attributes for agents/prompts; has_agents={isinstance(ag_map, dict)} has_prompts={isinstance(pr_map, (dict, list))}")
            except Exception:
                pass
            if isinstance(pr_map, dict):
                for nm, desc in pr_map.items():
                    try:
                        entries.append((str(nm), str(desc) if desc is not None else ""))
                    except Exception:
                        continue
            elif isinstance(pr_map, list):
                for nm in pr_map:
                    try:
                        entries.append((str(nm), ""))
                    except Exception:
                        continue

            # Build a sub-agent for each entry and expose as tool
            if _build_cfg and entries:
                try:
                    debug_print("[tools]", f"Found {len(entries)} tool entries: {[n for n,_ in entries][:10]}" )
                except Exception:
                    pass
                for sub_name, sub_desc in entries:
                    try:
                        debug_print("[tools]", f"Building sub-config for entry: {sub_name}")
                        sub_cfg, sub_err = _build_cfg(
                            project=(cfg.project or None),
                            agent=None,
                            prompt=None,
                            target=sub_name,
                            input=None,
                            merge=bool(getattr(cfg, 'merge', False)),
                        )
                        if sub_err or not sub_cfg:
                            try:
                                debug_print("[tools]", f"Skip entry {sub_name}: error={getattr(sub_err,'description', None) or (sub_err.get('description') if isinstance(sub_err, dict) else sub_err)}")
                            except Exception:
                                pass
                            continue
                        try:
                            debug_print("[tools]", f"Sub-cfg built: name={sub_cfg.name} prompt={sub_cfg.prompt_override} instr_len={len(sub_cfg.instructions or '')}")
                        except Exception:
                            pass
                        sub_agent = Agent(
                            name=sub_cfg.name or sub_name,
                            instructions=sub_cfg.instructions or "",
                            model_settings=ModelSettings(model=cfg.model),
                            tools=base_tools_snapshot,
                            mcp_servers=mcp_servers,
                        )
                        tool = sub_agent.as_tool(
                            tool_name=sub_cfg.name or sub_name,
                            tool_description=(sub_desc or f"Invoke agent '{sub_name}'"),
                        )
                        tools.append(tool)
                        try:
                            debug_print("[tools]", f"Tool added: {sub_cfg.name or sub_name}; tools_count={len(tools)}")
                        except Exception:
                            pass
                    except Exception:
                        try:
                            from call.app.utils.common import format_exception_text as _fmt
                        except Exception:
                            _fmt = None
                        try:
                            debug_print("[tools]", f"Error building tool for {sub_name}: " + (_fmt(Exception()) if _fmt else ""))
                        except Exception:
                            pass
            elif _build_cfg and not entries:
                try:
                    debug_print("[tools]", "No tool entries found in cfg.attributes")
                except Exception:
                    pass
        except Exception:
            pass

        agent = Agent(
            name=f"{cfg.name}",
            instructions=(cfg.instructions or ""),
            model_settings=ModelSettings(
                model=cfg.model,
            ),
            tools=tools,
            mcp_servers=mcp_servers,
        )
        run_context = RunContextWrapper(context=None)
        for srv in mcp_servers:
            _ = await srv.list_tools(run_context, agent)

        # Initialize bot: prefer CALL_TELEGRAM_TOKEN or use project from cfg
        try:
            await init_bot(project_name=(cfg.project or None))
        except Exception:
            pass
        
        merged_output = _merge_outputs(
            (_load_yaml(cfg.agent_yaml_path).get("output") if cfg.agent_yaml_path else None),
            None,
        )
        m_chat, m_thread = _extract_tg_targets(merged_output)
        prompt_chat_id = m_chat
        prompt_thread_id = m_thread


        # Save globally for subsequent messages
        global selected_chat_id, selected_thread_id, force_no_session
        # Respect previously selected targets (e.g., set by lib.api from Telegram update).
        # If current value equals env default, allow agent YAML/output to override.
        # Otherwise, keep the explicit value set by the caller.
        env_chat = TELEGRAM_CHAT_ID
        env_thread = (TELEGRAM_THREAD_ID or None)

        no_session = bool(force_no_session)
        if not no_session:
            if selected_chat_id is None or selected_chat_id == env_chat:
                selected_chat_id = (prompt_chat_id or env_chat)
            if selected_thread_id is None or selected_thread_id == env_thread:
                selected_thread_id = (prompt_thread_id or env_thread)
        else:
            # Explicitly disable routing and sessions
            selected_chat_id = None
            selected_thread_id = None

        # Now that selected_chat_id is finalized, create or skip SQLite session
        if (selected_chat_id is not None):
            # Deterministic and unique per dialog thread (agent:chat[:thread])
            if selected_thread_id is not None:
                session_id = f"{cfg.name}:{selected_chat_id}:{selected_thread_id}"
            else:
                session_id = f"{cfg.name}:{selected_chat_id}"

            db_path = os.getenv("CALL_DB", "call/call.db")
            try:
                db_dir = os.path.dirname(db_path)
                if db_dir:
                    os.makedirs(db_dir, exist_ok=True)
            except Exception:
                pass

            session = SQLiteSession(session_id, db_path)
            debug_print(f"[INFO] Session id: {session_id} @ {db_path}")
        else:
            session = None

        debug_print(f"[INFO] Agent yaml: {cfg.agent_yaml_path}")
        debug_print(f"[INFO] Target: chat_id={selected_chat_id if selected_chat_id is not None else '(disabled)'}, thread_id={selected_thread_id if selected_thread_id is not None else '(disabled)'}")

        # Send welcome message with agent link and run context (after config is ready)
        if selected_chat_id is not None:
            try:
                welcome_html = compose_welcome_html(
                    agent_name=(cfg.name or ''),
                    agent_yaml_path=(cfg.agent_yaml_path or None),
                    user_input=user_input,
                    mcp_servers_started=mcp_servers_started,
                    vs_list=((cfg.attributes or {}).get('vs')),
                    model=(cfg.model or None),
                )
                # Debug log the welcome HTML only when CALL_DEBUG is enabled
                debug_print("[app]", "welcome_html=\n" + (welcome_html or ""))

                await send_telegram_welcome_message(
                    text=welcome_html,
                    chat_id=selected_chat_id,
                    message_thread_id=selected_thread_id,
                )
            except Exception as e:
                # Do not block run on welcome banner failures, but log the exception in debug mode
                try:
                    err_text = format_exception_text(e)
                    debug_print("[app]", "[WARN] welcome message send failed:\n" + err_text)
                except Exception:
                    pass

        # Enrich JSON user_input: if it contains context items of type 'file',
        # try to download by URL and attach a 'base64' field next to the URL.
        # Errors are swallowed; original input is kept on failure.
        async def _embed_files_in_user_input_if_any(raw: str) -> str:
            try:
                import json as _json
                import base64 as _b64
                data = _json.loads(raw)
                ctx = data.get("context")
                if not isinstance(ctx, list):
                    return raw
                # Only process first-level items with type 'file'
                found = False
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    for it in ctx:
                        try:
                            if not isinstance(it, dict):
                                continue
                            if str(it.get("type")) != "file":
                                continue
                            url = str(it.get("url") or "").strip()
                            if not url:
                                continue
                            # Skip if already present
                            if isinstance(it.get("base64"), str) and it["base64"]:
                                continue
                            resp = await client.get(url)
                            if resp.status_code == 200:
                                b64 = _b64.b64encode(resp.content).decode("ascii")
                                it["base64"] = b64
                                found = True
                        except Exception:
                            # best-effort per item
                            continue
                if found:
                    try:
                        out = _json.dumps(data, ensure_ascii=False)
                        debug_print("[app]", "[PAYLOAD] embedded base64 for files")
                        return out
                    except Exception:
                        return raw
                return raw
            except Exception:
                return raw

        try:
            if isinstance(user_input, str) and user_input.strip().startswith(('{','[')):
                user_input = await _embed_files_in_user_input_if_any(user_input)
        except Exception:
            pass

        # Run the main agent once with pure user_input string (session-enabled)
        initial_input = (user_input or "go")
        try:
            result1 = await Runner.run(
                agent,
                initial_input,
                max_turns=150,
                session=session,
            )
            step1_output = getattr(result1, "final_output", None)
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
            # Non-fatal errors: log a concise one-liner and surface a short error (no stack)
            short_msg = str(e) or "Error"
            debug_print("[app]", f"Error during main agent run: {short_msg}")
            step1_output = f"Error: {short_msg}"

        # Notify digest (no image) and push
        # Only notify/push when we have a non-error output
        is_error_output = isinstance(step1_output, str) and step1_output.strip().lower().startswith("error:")
        if not is_error_output and (selected_chat_id is not None):
            try:
                # Capture targets locally to avoid races with global changes
                use_chat_id = selected_chat_id
                use_thread_id = selected_thread_id
                await send_digest_notification(
                    agent_name=(cfg.name or ''),
                    agent_path=(str(cfg.agent_yaml_path) if cfg.agent_yaml_path else None),
                    input_text=initial_input,
                    text=(step1_output or ""),
                    chat_id=use_chat_id,
                    message_thread_id=use_thread_id,
                    image_path=None,
                )
            except Exception:
                pass
            try:
                await post_run_git_push(agent_name=(cfg.name or ''), user_input=user_input)
            except Exception:
                pass

        # Expose final_output to callers via cfg
        try:
            setattr(cfg, "_last_final_output", step1_output)
        except Exception:
            pass

        yield agent, cfg, session
        

async def main(agent_path: str = None, user_input: str = "", agent_name: str = "", project_name: str = "", prompt_name: str = "", merge: bool = False):
    """Legacy entrypoint: delegate to lib.api.call for simplicity."""
    try:
        from call.lib import api as _api
        await _api.call_async(project=(project_name or None), agent=(agent_name or None), prompt=(prompt_name or None), input=(user_input or ""), merge=bool(merge))
    except Exception:
        pass


async def republish_results() -> str:
    # loaf from file logs/x.html
    output = open("logs/2025-07-12/ai_news_report.html", encoding="utf-8").read()
    return await publish_results(content=output)

async def send_telegram_welcome_message(text: str = '', *, chat_id: int | None = None, message_thread_id: int | None = None):
    # Send initial message and store its ID
    global telegram_last_message
    # Choose chat: prefer explicit override; else use selected_* initialized from .env and possibly overridden by agent
    if chat_id is None:
        chat_id = selected_chat_id or TELEGRAM_CHAT_ID
    # Ensure bot exists before sending welcome
    await init_bot()
    # Send clean welcome banner without any progress bar
    telegram_last_message = await safe_send_message(
        chat_id=chat_id or telegram_last_message.chat_id,
        text=text,
        message_thread_id=(message_thread_id if message_thread_id is not None else (selected_thread_id or TELEGRAM_THREAD_ID or None)),
        parse_mode=ParseMode.HTML,
    )
    debug_print("[app]",
        f"Last message set. ID: {telegram_last_message.message_id}, Chat ID: {telegram_last_message.chat_id}, Thread ID: {telegram_last_message.message_thread_id}")


if __name__ == "__main__":
    # Entrypoint policy:
    # - If --cli flag is present: delegate to call.cli.main AFTER removing the flag
    # - Otherwise, run local async main() with legacy args: <AgentName> [<input>]
    import sys
    import asyncio as _asyncio

    args = sys.argv[1:]

    # Fast-path: --echo prints parsed legacy args as JSON and exits
    if "--echo" in args:
        import json as _json
        # Remove known flags but DO NOT change order of the remaining args
        known_flags = {"--echo", "--cli"}
        args_wo_flags = [a for a in args if a not in known_flags]
        agent_name_echo = args_wo_flags[0] if args_wo_flags else ""
        user_input_echo = " ".join(args_wo_flags[1:]) if len(args_wo_flags) > 1 else ""
        # Try to discover the agent YAML path
        try:
            agent_yaml_path = discover_agent_yaml(agent_name_echo) if agent_name_echo else None
            agent_yaml_str = str(agent_yaml_path) if agent_yaml_path else None
        except Exception:
            agent_yaml_str = None
        payload = {
            "AgentName": agent_name_echo,
            "Input": user_input_echo,
            "ArgsNoFlags": args_wo_flags,
            "AllArgs": args,
            "AgentPath": agent_yaml_str,
            "Note": "Echo mode – no run performed"
        }
        print(_json.dumps(payload, ensure_ascii=False))
        sys.exit(0)

    if "--cli" in args:
        # Strip the flag and forward to CLI
        args_wo = [a for a in args if a != "--cli"]
        from call.cli.main import main as cli_main
        sys.argv = [sys.argv[0]] + args_wo
        sys.exit(cli_main())

    # No --cli: run our own async main()
    # Use argparse to support optional named args and legacy positionals.
    # Forms supported:
    #   1) python -m call.app.call <AgentName> [<input>]
    #   2) python -m call.app.call --name <AgentName> [<input...>]
    #   3) python -m call.app.call --input <input...>         (no agent)
    #   4) python -m call.app.call -- <input...>              (no agent)
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument('--name', dest='name', default="", help='Agent name (optional)')
    parser.add_argument('--project', dest='project', default="", help='Project name (optional, restrict discovery to this project)')
    # Capture the rest of the command line after --input verbatim
    parser.add_argument('--input', dest='input_words', nargs=argparse.REMAINDER, help='Input text (optional, multi-word)')
    # Legacy positionals: agent and optional input (quoted or space-separated)
    parser.add_argument('positional', nargs='*')

    ns = parser.parse_args(args)

    agent_name = str(ns.name or "")
    project_name = str(ns.project or "")
    user_input = ""

    if ns.input_words is not None:
        # Everything after --input is input
        user_input = " ".join(ns.input_words).strip()
    elif ns.positional:
        # If agent name already provided via --name, treat all positionals as input
        if agent_name:
            user_input = " ".join(ns.positional).strip()
        else:
            agent_name = ns.positional[0]
            user_input = " ".join(ns.positional[1:]).strip()
    # Debug print: show agent name parsed from arguments (empty string if absent)
    debug_print(f"call AgentName=\"{agent_name}\"")
    debug_print(f"call input=\"{user_input}\"")
    if project_name:
        debug_print(f"call project=\"{project_name}\"")

    _asyncio.run(main(agent_name=agent_name, user_input=user_input, project_name=project_name))
