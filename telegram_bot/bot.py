"""
Telegram bot for the call subsystem.

Commands:
- /call [@Name] <input>        Execute agent/prompt/project via target resolution
- /agents [--aliases] [--q]    List available agents
- /prompts [filters]           List prompts with optional filters
- /projects                    List projects (StratoSpaceAiBot only)
- /reload                      Rescan repositories and rebuild index
- /clear [@Name]               Clear conversation session

Plain text handling:
- Private chats: "@Name <input>" is equivalent to "/call @Name <input>"
- Private chats: "plain text" is equivalent to "/call <text>" (input-only)
- Group chats: Only "@Name <input>" triggers execution (to avoid reacting to every message)

Architecture:
- Target resolution delegated to call_api.call_async() (prompt > agent > project hierarchy)
- No pre-validation of targets in bot layer - library handles resolution and errors
- Bot only interacts with call.lib.api facade, not directly with OpenAI or Telegraph
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
from dataclasses import dataclass
import logging
from typing import Callable, Awaitable, Optional
import re
import html as py_html
import argparse
import json
import sys
import time
import httpx


def _env_flag(name: str, default: str = "0") -> bool:
    """Return True if environment flag is enabled."""
    value = os.environ.get(name, default)
    return value.strip().lower() in ("1", "true", "yes", "on")


async def _typing_loop(
    bot: object,
    *,
    chat_id: int,
    thread_id: int | None,
    stop_event: asyncio.Event,
) -> None:
    try:
        while not stop_event.is_set():
            await bot.send_chat_action(
                chat_id=chat_id,
                action=ChatAction.TYPING,
                message_thread_id=thread_id,
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
    except Exception:
        pass


LOG_EMPTY_GETUPDATES = _env_flag("TELEGRAM_LOG_EMPTY_UPDATES", "0")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Telegram bot entrypoint for Call")
    p.add_argument("bot_name_pos", nargs="?", help="Bot handle, e.g. StratoSpaceAiBot")
    p.add_argument(
        "--bot-name",
        dest="bot_name",
        default=None,
        help="Bot handle (same as positional)",
    )
    p.add_argument(
        "--echo", action="store_true", help="Print effective config and exit"
    )
    return p


from dotenv import load_dotenv
from pathlib import Path
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.error import BadRequest, TimedOut, NetworkError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    TypeHandler,
)
from telegram.request import HTTPXRequest

# Library facade
from call.lib import api as call_api
from call.lib.logging import debug_print, configure_logging as call_logging, get_logger
from call.lib.utils import parse_metadata_and_prompt
from call.app.call import create_mcp_lifespan_callbacks
from call.app.utils.telegram_text import (
    telegram_truncate_html_safe,
    telegram_prepare_html,
    telegram_prepare_markdown,
)
from call.app.call import get_project_token
from call.app import call as app_call

# Use API-only facade; no direct repo_db/repo_fs imports
from call.telegram_bot.filters import (
    parse_prompts_filters as _parse_filters_mod,
    parse_prompts_and_state as _parse_filters_state_mod,
)


# Simple DI container for services (thin wrapper around call_api by default)
@dataclass
class _Services:
    call_api: object


_services = _Services(call_api=call_api)


def set_services(*, call_api_module: object) -> None:
    """Replace default services (for tests/mocking)."""
    global _services
    _services = _Services(call_api=call_api_module)


@dataclass(frozen=True)
class _CommandSpec:
    name: str
    description: str | None = None


def _normalize_command_name(value: str) -> str:
    token = (value or "").strip()
    if token.startswith("/"):
        token = token[1:]
    return token.strip().lower()


def _coerce_command_specs(commands: object) -> list[_CommandSpec]:
    specs: list[_CommandSpec] = []

    def add(name: object, desc: object | None = None) -> None:
        normalized = _normalize_command_name(str(name))
        if not normalized:
            return
        description = None
        if desc is not None:
            desc_text = str(desc).strip()
            if desc_text:
                description = desc_text
        specs.append(_CommandSpec(name=normalized, description=description))

    def parse_item(item: object) -> None:
        if isinstance(item, dict):
            for key, value in item.items():
                add(key, value)
            return
        if isinstance(item, str):
            text = item.strip()
            if not text:
                return
            if "," in text and ":" not in text:
                for token in [chunk.strip() for chunk in text.split(",")]:
                    if token:
                        add(token)
                return
            if ":" in text:
                head, tail = text.split(":", 1)
                if head.strip():
                    add(head, tail)
                    return
            add(text)
            return

    if isinstance(commands, dict):
        for key, value in commands.items():
            add(key, value)
    elif isinstance(commands, list):
        for item in commands:
            parse_item(item)
    elif isinstance(commands, str):
        parse_item(commands)

    return specs


def _dedupe_command_specs(specs: list[_CommandSpec]) -> list[_CommandSpec]:
    out: list[_CommandSpec] = []
    index: dict[str, int] = {}
    for spec in specs:
        if spec.name in index:
            idx = index[spec.name]
            if out[idx].description is None and spec.description:
                out[idx] = spec
            continue
        index[spec.name] = len(out)
        out.append(spec)
    return out


def _get_project_command_specs(project_name: str) -> list[_CommandSpec]:
    try:
        if not project_name:
            return []
        raw = _services.call_api.read(project_name)
        meta = parse_metadata_and_prompt(raw or "")
        commands = meta.get("commands") if isinstance(meta, dict) else None
        return _dedupe_command_specs(_coerce_command_specs(commands))
    except Exception:
        return []


def _format_command_specs(specs: list[_CommandSpec]) -> str:
    lines: list[str] = []
    for spec in specs:
        if spec.description:
            lines.append(f"- /{spec.name} — {spec.description}")
        else:
            lines.append(f"- /{spec.name}")
    return "\n".join(lines)


def _filter_custom_commands(commands: list[str]) -> list[str]:
    builtins = {
        "start",
        "help",
        "call",
        "reload",
        "prompts",
        "prompts_ready",
        "prompts_draft",
        "agents",
        "list",
        "projects",
        "clear",
    }
    normalized: list[str] = []
    for cmd in commands:
        token = _normalize_command_name(cmd)
        if not token or token in builtins:
            continue
        if token not in normalized:
            normalized.append(token)
    return normalized


@_require_allowed_users
async def handle_project_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = Messenger(context=context, update=update)
    msg = getattr(update, "effective_message", None)
    text = (msg.text or msg.caption or "").strip() if msg else ""
    if not text:
        return
    # Extract command token and ignore commands addressed to other bots
    cmd_token = text.split(maxsplit=1)[0]
    if "@" in cmd_token:
        cmd_name, mentioned = cmd_token.split("@", 1)
        own = (SELECTED_BOT_NAME or "").strip() or _project_to_bot_handle(PROJECT_NAME)
        if mentioned and own and mentioned.lower() != own.lower():
            return
    else:
        cmd_name = cmd_token
    cmd_name = cmd_name.split("@", 1)[0]
    args = text[len(cmd_token) :].lstrip() if len(text) > len(cmd_token) else ""
    input_text = f"{cmd_name} {args}".strip()

    try:
        base = _get_bot_project(update)
    except Exception:
        base = ""
    name = base or PROJECT_NAME or ""
    cid = update.effective_chat.id if update and update.effective_chat else None
    tid = msg.message_thread_id if msg else None
    input_arg, _ = await build_input_payload_from_reply(
        name or None, input_text, update, context
    )
    asyncio.create_task(
        _call_task(
            m,
            name or None,
            input_arg,
            echo=False,
            chat_id=cid,
            thread_id=tid,
        )
    )


# Load environment from call/.env first (module-relative), then allow process env to override
_CALL_DIR = Path(__file__).resolve().parent.parent  # .../call/
_CALL_ENV = _CALL_DIR / ".env"
if _CALL_ENV.exists():
    load_dotenv(dotenv_path=str(_CALL_ENV), override=True)
# Load default .env (cwd) and OS env; allow overriding too
load_dotenv(override=True)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
INCLUDE_TELEGRAM_MESSAGE_CONTEXT = _env_flag(
    "CALL_INCLUDE_TELEGRAM_MESSAGE", "0"
)
INCLUDE_TELEGRAM_BOT_CONTEXT = _env_flag(
    "CALL_INCLUDE_TELEGRAM_BOT", "0"
)
TELEGRAM_PHOTO_VARIANT = (
    os.environ.get("TELEGRAM_PHOTO_VARIANT", "largest").strip().lower()
    or "largest"
)
if TELEGRAM_PHOTO_VARIANT not in {"smallest", "min", "largest", "max", "first", "last"}:
    TELEGRAM_PHOTO_VARIANT = "largest"
# Optional selected bot name from CLI (set later in main) or env
SELECTED_BOT_NAME: str = os.environ.get("BOT_NAME", "").strip()
# Derived project name is computed in main after parsing args
PROJECT_NAME: str = ""
_ALLOWED_USERS_RAW = os.environ.get("ALLOWED_USERS", "").strip()
DROP_PENDING_UPDATES_RAW = os.environ.get("DROP_PENDING_UPDATES", "").strip()

_MEDIA_GROUP_CACHE: dict[str, list] = {}
_MEDIA_GROUP_MEMORY: dict[str, dict] = {}
_MEDIA_GROUP_MEMORY_TTL_SECONDS = 60 * 60


def _prune_media_group_memory(now: float | None = None) -> None:
    if not _MEDIA_GROUP_MEMORY:
        return
    if now is None:
        now = time.time()
    cutoff = now - _MEDIA_GROUP_MEMORY_TTL_SECONDS
    stale = [
        key
        for key, entry in _MEDIA_GROUP_MEMORY.items()
        if (entry.get("last_seen") or 0) < cutoff
    ]
    for key in stale:
        _MEDIA_GROUP_MEMORY.pop(key, None)


def _store_media_group_message(message) -> None:
    try:
        media_group_id = getattr(message, "media_group_id", None)
        if not media_group_id:
            return
        key = str(media_group_id)
        now = time.time()
        _MEDIA_GROUP_CACHE.setdefault(key, []).append(message)
        entry = _MEDIA_GROUP_MEMORY.get(key)
        if entry is None:
            entry = {"messages": [], "last_seen": now}
            _MEDIA_GROUP_MEMORY[key] = entry
        else:
            entry["last_seen"] = now
        messages = entry.get("messages")
        if not isinstance(messages, list):
            messages = []
            entry["messages"] = messages
        msg_id = getattr(message, "message_id", None)
        if msg_id is None:
            messages.append(message)
            return
        for idx, existing in enumerate(messages):
            if getattr(existing, "message_id", None) == msg_id:
                messages[idx] = message
                break
        else:
            messages.append(message)
        _prune_media_group_memory(now)
    except Exception:
        log.debug("Failed to cache Telegram media_group message", exc_info=True)


def _get_media_group_messages(message) -> list:
    if not message:
        return []
    try:
        media_group_id = getattr(message, "media_group_id", None)
        if not media_group_id:
            return [message]
        key = str(media_group_id)
        now = time.time()
        _prune_media_group_memory(now)
        group_messages: list = []
        entry = _MEDIA_GROUP_MEMORY.get(key)
        if entry:
            memory_messages = entry.get("messages")
            if isinstance(memory_messages, list):
                group_messages.extend(memory_messages)
        cached = _MEDIA_GROUP_CACHE.pop(key, [])
        if cached:
            group_messages.extend(cached)
        if message not in group_messages:
            group_messages.append(message)
        by_id: dict[int, object] = {}
        extras: list = []
        for msg in group_messages:
            msg_id = getattr(msg, "message_id", None)
            if msg_id is None:
                extras.append(msg)
                continue
            by_id[msg_id] = msg
        merged = list(by_id.values()) + extras
        if entry is None:
            _MEDIA_GROUP_MEMORY[key] = {"messages": merged, "last_seen": now}
        else:
            entry["messages"] = merged
            entry["last_seen"] = now
        return merged
    except Exception:
        log.debug("Failed to gather Telegram media_group messages", exc_info=True)
        return [message]

# Module logger (emits once configure_logging() is called by the entrypoint)
# Use the exact logger name the tests capture; avoid extra prefixes from get_logger
log = logging.getLogger("call.bot")
log.propagate = True

log.info("Loaded env: call/.env exists=%s", _CALL_ENV.exists())
masked = TELEGRAM_TOKEN[:6] + "..." if TELEGRAM_TOKEN else "<empty>"
log.info(
    "TELEGRAM_TOKEN(prefix)=%s, ALLOWED_USERS_raw_len=%d",
    masked,
    len(_ALLOWED_USERS_RAW),
)


def _parse_allowed_users(raw: str) -> set[int]:
    out: set[int] = set()
    if not raw:
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except Exception:
            # Ignore invalid entries
            pass
    return out


def _env_to_bool(raw: str, default: bool = False) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return default
    v = raw.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


# Materialize parsed envs
_ALLOWED_USERS = _parse_allowed_users(_ALLOWED_USERS_RAW)
_DROP_PENDING_UPDATES = _env_to_bool(DROP_PENDING_UPDATES_RAW, default=False)


def _current_config_dict() -> dict:
    """Collect key startup parameters for echo/debug output."""
    try:
        allowed = sorted(list(_ALLOWED_USERS)) if "_ALLOWED_USERS" in globals() else []
    except Exception:
        allowed = []
    return {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "ALLOWED_USERS": allowed,
        "CALL_DEBUG": os.environ.get("CALL_DEBUG", ""),
        "CALL_LOG_JSON": os.environ.get("CALL_LOG_JSON", ""),
        "CALL_ENV": str(_CALL_ENV),
        "CALL_ENV_exists": _CALL_ENV.exists(),
        "NO_PROXY": os.environ.get("NO_PROXY", ""),
        "no_proxy": os.environ.get("no_proxy", ""),
        "HTTPX": {
            "connect_timeout": 20.0,
            "read_timeout": 120.0,
            "write_timeout": 60.0,
        },
        "drop_pending_updates": _DROP_PENDING_UPDATES,
        "BOT_NAME": SELECTED_BOT_NAME,
        "PROJECT_NAME": PROJECT_NAME,
    }


def _summarize_update(update: Update) -> str:
    """Return a compact one-line summary of the Update for debug logging."""
    try:
        uid = getattr(update, "update_id", None)
        chat_id = getattr(getattr(update, "effective_chat", None), "id", None)
        user_id = getattr(getattr(update, "effective_user", None), "id", None)
        thread_id = None
        try:
            msg = getattr(update, "effective_message", None)
            thread_id = getattr(msg, "message_thread_id", None)
        except Exception:
            thread_id = None
        kind = None
        data = None
        if update.message:
            kind = "message"
            data = (update.message.text or update.message.caption or "").strip()
        elif update.edited_message:
            kind = "edited_message"
            data = (
                update.edited_message.text or update.edited_message.caption or ""
            ).strip()
        elif update.callback_query:
            kind = "callback_query"
            data = (getattr(update.callback_query, "data", "") or "").strip()
        elif update.channel_post:
            kind = "channel_post"
            data = (
                update.channel_post.text or update.channel_post.caption or ""
            ).strip()
        else:
            kind = "other"
            data = ""
        data = (data or "")[:120].replace("\n", " ")
        thread_part = f" thread={thread_id}" if thread_id is not None else ""
        return f"uid={uid} chat={chat_id} user={user_id}{thread_part} kind={kind} data={data!r}"
    except Exception:
        return "<unavailable>"


def _sanitize_false_fields(obj):
    """Recursively remove fields with False values from dicts to reduce log noise."""
    if isinstance(obj, dict):
        return {
            k: _sanitize_false_fields(v)
            for k, v in obj.items()
            if v is not False  # Remove fields with literal False value
        }
    elif isinstance(obj, list):
        return [_sanitize_false_fields(item) for item in obj]
    return obj


async def _log_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """TypeHandler callback to log every incoming update."""
    summary = _summarize_update(update)
    debug_print("[bot]", "[UPDATE]", summary)
    try:
        msg = getattr(update, "effective_message", None)
        if msg is None:
            msg = getattr(update, "message", None) or getattr(update, "edited_message", None)
        if msg is not None:
            _store_media_group_message(msg)
    except Exception:
        log.debug("Failed to cache Telegram media_group update", exc_info=True)
    try:
        update_dict = update.to_dict()
        # Remove False-valued fields to reduce log noise
        sanitized = _sanitize_false_fields(update_dict)
        raw_json = json.dumps(sanitized, ensure_ascii=False)
        # Emit on the exact logger name the tests capture
        logger = logging.getLogger("call.bot")
        # Ensure the logger (and its parents) are not filtered/disabled so caplog or other
        # capture handlers higher in the chain can see the record.
        logging.disable(logging.NOTSET)
        chain = []
        cur = logger
        while cur:
            chain.append(
                (
                    cur,
                    cur.level,
                    list(cur.filters),
                    cur.disabled,
                    cur.propagate,
                )
            )
            cur = cur.parent
        try:
            for log_obj, _lvl, _filters, _disabled, _prop in chain:
                log_obj.disabled = False
                log_obj.filters.clear()
                log_obj.setLevel(logging.NOTSET)
                # Allow propagation up the chain so any capture handlers (e.g. pytest caplog)
                # attached on ancestors/root can observe this record even if intermediate loggers
                # normally stop propagation (configure_logging sets call logger propagate=False).
                log_obj.propagate = True
            logger.info("Update raw: %s", raw_json)
        finally:
            for log_obj, lvl, filters, disabled, prop in chain:
                log_obj.setLevel(lvl)
                log_obj.filters = filters
                log_obj.disabled = disabled
                log_obj.propagate = prop
    except Exception as e:
        logging.debug("[bot] Failed to log raw update: %s", e)
    return None


async def _tap_getupdates_response(response: httpx.Response) -> None:
    """Log raw Telegram getUpdates responses before PTB processes them."""
    try:
        req = getattr(response, "request", None)
        request_url = str(req.url) if req and getattr(req, "url", None) else ""
        if not request_url.endswith("/getUpdates"):
            return
        await response.aread()
        try:
            data = response.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            result = data.get("result")
            if isinstance(result, list) and not result:
                # Optional debug: log when getUpdates returns empty list
                if LOG_EMPTY_GETUPDATES:
                    log.info(
                        "Telegram getUpdates: received EMPTY result list (ok=%s)",
                        data.get("ok"),
                    )
                return
        raw = response.text
        if len(raw) > 5000:
            raw = f"{raw[:5000]}… [truncated]"
        log.debug("Telegram RAW getUpdates: %s", raw)
    except Exception:
        log.debug("Telegram RAW getUpdates: <unavailable>", exc_info=True)


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler to avoid unhandled exception warnings and provide context."""
    try:
        summary = (
            _summarize_update(update) if isinstance(update, Update) else "<non-Update>"
        )
    except Exception:
        summary = "<unavailable>"
    # Log the exception with traceback
    try:
        err = getattr(context, "error", None)
        if err is not None:
            is_net = isinstance(err, NetworkError) or isinstance(
                getattr(err, "__cause__", None), NetworkError
            )
            if is_net:
                log.warning(
                    "Transient network issue while processing update: %s (%s: %s)",
                    summary,
                    type(err).__name__,
                    str(err),
                )
                debug_print(
                    "[bot]", "[WARN]", f"{type(err).__name__}: {err}", "|", summary
                )
            else:
                log.error(
                    "Unhandled error while processing update: %s (%s: %s)",
                    summary,
                    type(err).__name__,
                    str(err),
                    exc_info=(type(err), err, getattr(err, "__traceback__", None)),
                )
                debug_print(
                    "[bot]", "[ERROR]", f"{type(err).__name__}: {err}", "|", summary
                )
        else:
            log.error("Unhandled error while processing update: %s (no error)", summary)
            debug_print("[bot]", "[ERROR]", "<no error>", "|", summary)
    except Exception:
        # Fallback to original behavior
        log.exception("Unhandled error while processing update: %s", summary)
    # Also notify the user minimally to avoid silent failures
    try:
        if isinstance(update, Update):
            m = Messenger(context=context, update=update)
            err = context.error
            msg = f"Error: {type(err).__name__}: {err}" if err else "Error: unknown"
            await m.reply(msg, parse_mode=None)
    except Exception:
        pass


ALLOWED_USERS: set[int] = _parse_allowed_users(_ALLOWED_USERS_RAW)


@dataclass
class Messenger:
    """Simple DI-friendly messenger wrapper."""

    context: ContextTypes.DEFAULT_TYPE
    update: Update

    async def reply(
        self, text: str, *, parse_mode: Optional[str] = ParseMode.HTML
    ) -> None:
        """Reply with sanitized text, safe truncation, retries, and fallback to plain text.

        - If parse_mode == HTML: escape everything, then allow minimal tags we control (<b>, <br>)
        - Truncate safely to avoid entity/tag cut (<= 4000 chars for safety)
        - Retry on TimedOut with exponential backoff
        - Fallback to plain text on BadRequest entity parse errors
        """
        prepared_text = text or ""
        use_parse_mode = parse_mode

        try:
            if parse_mode in (ParseMode.HTML, "HTML"):
                prepared_text, pm = telegram_prepare_html(prepared_text, 4000)
                use_parse_mode = pm
            elif parse_mode in (ParseMode.MARKDOWN, "Markdown", "MarkdownV2"):
                # Default to MarkdownV2 escaping
                prepared_text, pm = telegram_prepare_markdown(
                    prepared_text, 4000, version="v2"
                )
                use_parse_mode = pm
            else:
                # Plain text path; enforce limit
                if len(prepared_text) > 4096:
                    prepared_text = prepared_text[:4095] + "…"
                use_parse_mode = None
        except Exception:
            # If anything goes wrong, fallback to plain
            use_parse_mode = None
            prepared_text = (
                (prepared_text[:4095] + "…")
                if len(prepared_text) > 4096
                else prepared_text
            )

        async def _send(pt: str, pmode: Optional[str]):
            debug_print("[bot]", "[SEND]", f"len={len(pt)}", f"pmode={pmode}")
            if self.update.message:
                res = await self.update.message.reply_text(text=pt, parse_mode=pmode)
                debug_print("[bot]", "[SENT]", "via reply_text")
                return res
            elif self.update.effective_chat:
                res = await self.context.bot.send_message(
                    chat_id=self.update.effective_chat.id, text=pt, parse_mode=pmode
                )
                debug_print(
                    "[bot]",
                    "[SENT]",
                    f"via send_message chat_id={getattr(self.update.effective_chat, 'id', None)}",
                )
                return res

        # Retry loop for transient timeouts
        for attempt in range(3):
            try:
                await _send(prepared_text, use_parse_mode)
                return
            except BadRequest as e:
                # Fallback to plain text if Telegram can't parse entities
                msg = str(e).lower()
                if (
                    "can't parse entities" in msg
                    or "parse entities" in msg
                    or "entity" in msg
                ):
                    debug_print(
                        "[bot]",
                        "[WARN]",
                        f"BadRequest parse error: {e}; falling back to plain",
                    )
                    plain = re.sub(r"<[^>]+>", "", prepared_text)
                    if len(plain) > 4096:
                        plain = plain[:4095] + "…"
                    try:
                        await _send(plain, None)
                    except Exception as e2:
                        debug_print(
                            "[bot]",
                            "[ERROR]",
                            f"Fallback send failed: {type(e2).__name__}: {e2}",
                        )
                        raise
                    return
                raise
            except TimedOut:
                debug_print("[bot]", "[WARN]", f"TimedOut on attempt {attempt+1}/3")
                if attempt == 2:
                    break
                await asyncio.sleep(1 * (2**attempt))
            except Exception as e:
                # Last-resort fallback to plain
                debug_print(
                    "[bot]",
                    "[ERROR]",
                    f"Send failed: {type(e).__name__}: {e}; falling back to plain",
                )
                plain = re.sub(r"<[^>]+>", "", prepared_text)
                if len(plain) > 4096:
                    plain = plain[:4095] + "…"
                try:
                    await _send(plain, None)
                    return
                except Exception as e2:
                    debug_print(
                        "[bot]",
                        "[ERROR]",
                        f"Plain fallback also failed: {type(e2).__name__}: {e2}",
                    )
                    raise
        # If retries exhausted due to TimedOut, send minimal plain notification
        fallback = "Service temporarily unavailable. Please try again later."
        try:
            await _send(fallback, None)
        except Exception as e3:
            debug_print(
                "[bot]",
                "[ERROR]",
                f"Minimal fallback failed: {type(e3).__name__}: {e3}",
            )


# Authorization decorator


def _require_allowed_users(
    func: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]],
):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if ALLOWED_USERS:
            uid = update.effective_user.id if update.effective_user else None
            cid = update.effective_chat.id if update.effective_chat else None
            ok = (uid in ALLOWED_USERS) or (cid in ALLOWED_USERS)
            if not ok:
                debug_print(
                    "[bot]", "[AUTH]", f"ignored unauthorized user={uid} chat={cid}"
                )
                return
        await func(update, context)

    return wrapper


# Command parsing helpers


def _extract_after(prefix: str, text: str) -> str:
    return text[len(prefix) :].strip()


# Shared helpers (deduplicated logic)


def _strip_bot_suffix(s: str) -> str:
    s2 = s or ""
    for suf in ("Bot", "_bot", "-bot", " bot"):
        if s2.endswith(suf):
            return s2[: -len(suf)]
    return s2


def _get_bot_project(update: Update | None = None) -> str:
    """Return base project name derived from --bot-name by stripping Bot suffix (case-sensitive)."""
    if SELECTED_BOT_NAME:
        return _strip_bot_suffix(SELECTED_BOT_NAME)
    return ""


def _project_to_bot_handle(project_name: str) -> str:
    """Map canonical ProjectName to its Telegram bot handle.

    Example: AgentFab -> AgentFabBot
    """
    name = (project_name or "").strip()
    if not name:
        return ""
    return name if name.endswith("Bot") else f"{name}Bot"


def _project_to_bot_link(project_name: str) -> tuple[str, Optional[str]]:
    """Return ("@Handle", "https://t.me/Handle") for a project name.

    If project name is empty, returns ("", None).
    """
    handle = _project_to_bot_handle(project_name)
    if not handle:
        return "", None
    at = f"@{handle}"
    return at, f"https://t.me/{handle}"


_TRAILING_PUNCT_RE = re.compile(r"[\s\.,;:!\?…'\"`“”‘’\)\]\}›»]+$")
_TARGET_TOKEN_RE = re.compile(r"^@[A-Za-z0-9][A-Za-z0-9_\-./:*]*$")


def _normalize_token(tok: str) -> str:
    try:
        s = (tok or "").strip()
        # Strip leading @ prefix
        if s.startswith("@"):
            s = s[1:]
        # Strip trailing punctuation
        s = _TRAILING_PUNCT_RE.sub("", s)
        if s.endswith(".md"):
            s = s[:-3]
        return s
    except Exception:
        return (tok or "").strip()


def _looks_like_target(tok: str) -> bool:
    try:
        s = (tok or "").strip()
        if not s:
            return False
        return bool(_TARGET_TOKEN_RE.match(s))
    except Exception:
        return False


# _is_valid_target() removed - validation delegated to call_api.call_async()


def _resolve_agent_and_input(
    text: str, base_project: str, *, is_private: bool
) -> tuple[str, str, bool]:
    """Parse text into (target_name, input_text, should_handle) with conservative rules.

    ARCHITECTURE: This function does NOT validate if target exists in catalog.
    Target validation is delegated to call_api.call_async() which resolves: prompt > agent > project.
    This matches /call command behavior - parse and delegate, don't pre-validate.

    - Group chats: require an explicit @-mention; support:
        @Target <input>            -> extract Target and rest (no validation)
ло        @BotName @Target <input>   -> extract Target and rest (no validation)
        @ <input>                  -> input-only (no target)
    - Private chats: plain text behaves like '/call <input>' (no implicit target).
        @Target <input>            -> extract Target and rest (no validation)
        plain text                 -> input-only (no target)
    """
    s = (text or "").strip()
    if not s:
        return "", "", False

    # Helper: own bot handle (e.g., StratoSpaceAiBot)
    try:
        own = (SELECTED_BOT_NAME or "").strip() or _project_to_bot_handle(PROJECT_NAME)
    except Exception:
        own = ""

    if s.startswith("@"):
        body = s[1:]
        # '@' followed by nothing -> ignore
        if not body:
            return "", "", False
        # '@ <input>' (space after '@'): treat as input-only
        try:
            if body and body[0].isspace():
                return "", body.strip(), True
        except Exception:
            pass
        parts = body.lstrip().split(None, 1)
        head_raw = parts[0]
        head = head_raw.strip()
        head_norm = _normalize_token(head_raw)
        rest = parts[1] if len(parts) > 1 else ""

        # Group chats must mention the bot explicitly (@BotName ...)
        if not is_private:
            if not own:
                return "", "", False
            if head_norm != own:
                return "", "", False

        # '@BotName ...' -> address bot explicitly; next token may be target
        if own and (head == own or head_norm == own):
            if not rest.strip():
                return "", "", False
            sub = rest.strip().split(None, 1)
            cand_raw = sub[0] if sub else ""
            tail = sub[1] if len(sub) > 1 else ""
            # For natural language in groups, treat the rest as input-only by default.
            # An explicit target must be marked with '@' (e.g. '@BotName @Target ...').
            if cand_raw.startswith("@") and _looks_like_target(cand_raw):
                cand = _normalize_token(cand_raw)
                if cand:
                    return cand.lstrip("@"), tail, True
            return "", rest.strip(), True
        # '@Target ...' -> return target without validation
        # Library will resolve it as prompt > agent > project
        candidate = head if head.startswith("@") else f"@{head}" if head else ""
        if candidate and _looks_like_target(candidate):
            return candidate.lstrip("@"), rest, True
        return "", body.strip(), True

    # No leading '@'
    if is_private:
        # Private DM: treat plain text as input-only (identical to '/call <input>')
        return "", s, True
    # Group chat without mention -> ignore
    return "", "", False


@_require_allowed_users
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.debug(
        "handle_start: chat_id=%s user_id=%s",
        getattr(update.effective_chat, "id", None),
        getattr(update.effective_user, "id", None),
    )
    m = Messenger(context=context, update=update)
    debug_print(
        "[bot]",
        "[START]",
        f"entry chat_id={getattr(update.effective_chat, 'id', None)} user_id={getattr(update.effective_user, 'id', None)}",
    )
    
    # For specialized bots: show project goal + commands from metadata
    # For universal bot: show full command reference
    if PROJECT_NAME:
        goal_text = ""
        try:
            card_text = _services.call_api.read(PROJECT_NAME)
            meta = parse_metadata_and_prompt(card_text or "")
            goal = meta.get("goal") if isinstance(meta, dict) else None
            if isinstance(goal, list):
                goal_text = "\n".join(
                    str(line).strip() for line in goal if str(line).strip()
                )
            elif goal:
                goal_text = str(goal).strip()
        except Exception as e:
            debug_print("[bot]", "[START]", f"Failed to read project goal: {e}")

        if not goal_text:
            goal_text = "Проектный бот."

        command_specs = _get_project_command_specs(PROJECT_NAME)
        cmd_lines = _format_command_specs(command_specs)
        commands_block = cmd_lines or "Команды не указаны в METADATA."

        specialized_help = f"""🎯 {PROJECT_NAME}

{goal_text}

Команды:
{commands_block}

---

💬 Общайтесь со мной на естественном языке.

В приватном чате просто напишите запрос.
В группе команды работают без @упоминания.

Примеры:
- "статус проекта"
- "задачи на сегодня"
- "отчёт за неделю"
"""
        await m.reply(specialized_help.strip(), parse_mode=None)
        debug_print("[bot]", "[START]", "replied (specialized)")
        return
    
    # Universal bot: show full command reference
    base_help = """
call-bot

Commands:
- /call [--echo] @Name <input>
- /call [--echo] Name <input>  (equivalent to @Name)
- /agents [--aliases] [--q "filter"]
- /clear [@Name]  (clear conversation session for current chat/thread; all agents if name omitted)
- /reload  (rescan repositories and rebuild repo index)

Startup options:
- --bot-name Name  (token lookup: TELEGRAM_TOKEN__Name in env/.env; if --bot-name is not provided, falls back to TELEGRAM_TOKEN)

Plain text (no slash):
- In private chat: 
  - "@Name <input>" is equivalent to "/call @Name <input>"
  - "plain text" is equivalent to "/call <text>" (input-only)
- In groups: only explicit "@Name <input>" or "@BotName <input>" is handled.

Special cases:
- If this bot is AgentFabBot, default agent is AgentFab when no name is specified (e.g., "@ <input>").
    
Notes:
- /agents lists one name per line as @Name.
- With --aliases, alias lines are indented with two spaces before @ (e.g., "  @Alias").
    """.strip()
    
    await m.reply(base_help, parse_mode=None)
    debug_print("[bot]", "[START]", "replied")


@_require_allowed_users
async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Concise help with guide link as the very first line."""
    log.debug(
        "handle_help: chat_id=%s user_id=%s",
        getattr(update.effective_chat, "id", None),
        getattr(update.effective_user, "id", None),
    )
    m = Messenger(context=context, update=update)
    debug_print(
        "[bot]", "[HELP]", f"entry chat_id={getattr(update.effective_chat, 'id', None)}"
    )
    if PROJECT_NAME:
        command_specs = _get_project_command_specs(PROJECT_NAME)
        cmd_lines = _format_command_specs(command_specs)
        txt = f"""
https://github.com/strato-space/prompt/blob/main/MediaGenBlender/tg-user-guide.ru.md

Команды:
{cmd_lines if cmd_lines else "Команды не указаны в METADATA."}
        """.strip()
    else:
        txt = """
https://github.com/strato-space/call/blob/main/tg-user-guide.ru.md

Быстро:
- /call @AgentFab @31-* — обработать 31-* через AgentFab
- /call @AiNewsAggr Новости Apple — запустить агента AiNewsAggr с входом
- /prompts_ready | /prompts_draft — списки промптов (фильтры: --project, --agent, --prompt, --target, --state)
- /reload — пересканировать репозитории и обновить индекс

Подсказки:
- В личных чатах: "@Name input" эквивалентно "/call @Name input" (если Name найден в каталоге)
- Если @Name не найден - сообщение молча игнорируется (с записью в лог)
- В группах используйте @упоминание или /call
- Приоритет target: prompt > точный project > agent > шаблонный project
        """.strip()
    await m.reply(txt, parse_mode=None)
    debug_print("[bot]", "[HELP]", "replied")


@_require_allowed_users
async def handle_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.debug("handle_list: incoming text=%r", getattr(update.message, "text", None))
    m = Messenger(context=context, update=update)
    debug_print(
        "[bot]", "[AGENTS]", f"entry text={getattr(update.message, 'text', None)!r}"
    )

    # Scope by project (derive from bot); StratoSpaceAiBot lists all projects
    proj = PROJECT_NAME or None
    if (SELECTED_BOT_NAME or "").strip() == "StratoSpaceAiBot":
        proj = None
    try:
        tree = _services.call_api.list(project=proj)
        debug_print("[bot]", "[AGENTS]", f"projects={len(tree or [])}")
        if not tree:
            await m.reply("No agents found", parse_mode=None)
            return
        lines: list[str] = []
        for node in tree[:8]:
            # Project header as bold link to t.me/<ProjectName>Bot
            pname = (node.get("name") or "").strip()
            _at, url = _project_to_bot_link(pname)
            visible = f"@{pname} Bot"
            if url:
                lines.append(f'<b><a href="{url}">{py_html.escape(visible)}</a></b>')
            else:
                lines.append(f"<b>{py_html.escape(visible)}</b>")
            # Each agent as a list item (skip the first one if present)
            agents = (node.get("agents") or [])[:100]
            for idx, ag in enumerate(agents):
                if idx == 0:
                    continue
                nm = (ag.get("name") or "").strip()
                if nm:
                    lines.append(f"• @{nm}")
            lines.append("")
        payload = "\n".join(lines).strip()
        debug_print("[bot]", "[AGENTS]", f"reply_len={len(payload)}")
        await m.reply(payload, parse_mode=ParseMode.HTML)
    except Exception as e:
        debug_print("[bot]", "[AGENTS]", f"error {type(e).__name__}: {e}")
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)


# ---- Local formatting helpers for prompt listings ----


def _format_prompt_markdown_row(item: dict) -> str:
    name = str(item.get("name") or item.get("prompt_id") or "").strip()
    url = item.get("url")
    # Prefer Markdown link when URL is available; fallback to plain title
    title = f"[{name}]({url})" if (url and name) else (name or "(untitled)")
    return f"- {title}"


def _format_prompts_markdown(items: list[dict]) -> str:
    try:
        lst = list(items or [])
    except Exception:
        lst = []
    if not lst:
        return "_No prompts found_"
    rows = [_format_prompt_markdown_row(x) for x in lst]
    return "\n".join(rows)


def _parse_prompts_filters(
    text: str, *, command: str, default_project: str | None
) -> tuple[str | None, str | None, str | None, str | None]:
    return _parse_filters_mod(text, command=command, default_project=default_project)


def _parse_prompts_and_state(
    text: str, *, command: str, default_project: str | None
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    return _parse_filters_state_mod(
        text, command=command, default_project=default_project
    )


async def _send_markdown_rows_chunked(
    m: Messenger, rows: list[str], *, header: str | None = None, max_len: int = 3800
) -> None:
    """Send a list of Markdown rows split across multiple Telegram messages.

    - max_len: conservative limit below 4000 to avoid entity boundary issues.
    - header: optional header placed at the top of the first chunk only.
    """
    if not rows:
        await m.reply("_No prompts found_", parse_mode=ParseMode.MARKDOWN)
        return
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = len(header) if header else 0
    if header:
        cur.append(header)
    for r in rows:
        # +1 for newline when joined
        add_len = len(r) + (1 if cur else 0)
        if cur_len + add_len > max_len and cur:
            chunks.append("\n".join(cur))
            cur = []
            cur_len = 0
        cur.append(r)
        cur_len += add_len
    if cur:
        chunks.append("\n".join(cur))
    for ch in chunks:
        await m.reply(ch, parse_mode=ParseMode.MARKDOWN)


@_require_allowed_users
async def handle_prompts_ready(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """List ready prompts. Usage: /prompts_ready [<project>] [<agent>|@Agent]"""
    m = Messenger(context=context, update=update)
    debug_print(
        "[bot]",
        "[PROMPTS_READY]",
        f"entry text={getattr(update.message, 'text', None)!r}",
    )
    try:
        text = (update.message.text or "").strip() if update.message else ""
        proj_default = _get_bot_project(update) or None
        project, agent, prompt, target = _parse_prompts_filters(
            text, command="/prompts_ready", default_project=proj_default
        )
        items = _services.call_api.list_prompts(
            project=project, agent=agent, prompt=prompt, target=target, state="ready"
        )
        rows = [_format_prompt_markdown_row({"name": it.get("prompt")}) for it in items]
        debug_print(
            "[bot]",
            "[PROMPTS_READY]",
            f"rows={len(rows)} project={project!r} agent={agent!r}",
        )
        await _send_markdown_rows_chunked(m, rows)
    except Exception as e:
        debug_print("[bot]", "[PROMPTS_READY]", f"error {type(e).__name__}: {e}")
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)


@_require_allowed_users
async def handle_prompts_draft(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """List draft prompts. Usage: /prompts_draft [<project>] [<agent>|@Agent]"""
    m = Messenger(context=context, update=update)
    debug_print(
        "[bot]",
        "[PROMPTS_DRAFT]",
        f"entry text={getattr(update.message, 'text', None)!r}",
    )
    try:
        text = (update.message.text or "").strip() if update.message else ""
        proj_default = _get_bot_project(update) or None
        project, agent, prompt, target = _parse_prompts_filters(
            text, command="/prompts_draft", default_project=proj_default
        )
        items = _services.call_api.list_prompts(
            project=project, agent=agent, prompt=prompt, target=target, state="draft"
        )
        rows = [_format_prompt_markdown_row({"name": it.get("prompt")}) for it in items]
        await _send_markdown_rows_chunked(m, rows)
    except Exception as e:
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)


@_require_allowed_users
async def handle_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Rescan repositories and rebuild the SQLite repo index."""
    m = Messenger(context=context, update=update)
    try:
        res = _services.call_api.reload()
        scanned = int(res.get("scanned", 0)) if isinstance(res, dict) else 0
        await m.reply(
            f"Reload complete. Scanned: <b>{scanned}</b>", parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await m.reply(f"Reload failed: {type(e).__name__}: {e}", parse_mode=None)


@_require_allowed_users
async def handle_prompts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unified prompts lister. Usage: /prompts [--state ready|draft] [filters]

    Filters support --project/--agent/--prompt/--target and key=value forms; @Agent shorthand.
    """
    m = Messenger(context=context, update=update)
    try:
        text = (update.message.text or "").strip() if update.message else ""
        proj_default = _get_bot_project(update) or None
        project, agent, prompt, target, state = _parse_prompts_and_state(
            text, command="/prompts", default_project=proj_default
        )
        items = _services.call_api.list_prompts(
            project=project, agent=agent, prompt=prompt, target=target, state=state
        )
        rows = [_format_prompt_markdown_row({"name": it.get("prompt")}) for it in items]
        header = None
        if state:
            header = f"State: {state}"
        await _send_markdown_rows_chunked(m, rows, header=header)
    except Exception as e:
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)


async def _call_task(
    m: Messenger,
    name: str,
    input_text: str,
    *,
    echo: bool = False,
    chat_id: int | None = None,
    thread_id: int | None = None,
) -> None:
    typing_task: asyncio.Task | None = None
    typing_stop: asyncio.Event | None = None
    try:
        # Ensure the final result is sent as a reply to the triggering message.
        # Use task-local ContextVar to avoid cross-talk between concurrent requests.
        token_reply = None
        try:
            reply_id = getattr(getattr(m.update, "effective_message", None), "message_id", None)
            try:
                token_reply = app_call.reply_to_message_id_var.set(reply_id)
            except Exception:
                token_reply = None
        except Exception:
            token_reply = None

        log.info(
            "_call_task: start name=%s input_len=%d echo=%s chat_id=%s thread_id=%s",
            name,
            len(input_text or ""),
            echo,
            chat_id,
            thread_id,
        )
        debug_print(
            "[bot]",
            "[CALL_TASK]",
            f"start name={name} len={len(input_text or '')} echo={echo} chat_id={chat_id} thread_id={thread_id}",
        )

        # Show typing status to indicate command is being processed
        try:
            typing_chat_id = chat_id
            typing_thread_id = thread_id
            if typing_chat_id is None and m.update and m.update.effective_chat:
                typing_chat_id = m.update.effective_chat.id
            msg_effective = getattr(m.update, "effective_message", None)
            if typing_thread_id is None and msg_effective is not None:
                typing_thread_id = getattr(msg_effective, "message_thread_id", None)
            if typing_chat_id is not None:
                typing_stop = asyncio.Event()
                typing_task = asyncio.create_task(
                    _typing_loop(
                        m.context.bot,
                        chat_id=typing_chat_id,
                        thread_id=typing_thread_id,
                        stop_event=typing_stop,
                    )
                )
                debug_print(
                    "[bot]",
                    "[CALL_TASK]",
                    f"typing loop chat_id={typing_chat_id} thread_id={typing_thread_id}",
                )
        except Exception:
            pass

        # Delegate to lib; it will publish to Telegram via its own utilities
        proj_baseline = (
            None
            if (SELECTED_BOT_NAME or "").strip() == "StratoSpaceAiBot"
            else (PROJECT_NAME or None)
        )
        # For specialized bots: if no explicit target, use project name as target (runs project.md)
        # For StratoSpaceAiBot: keep existing behavior (blank agent)
        if (name or "").strip():
            # Explicit target provided by user
            proj = proj_baseline
            target_name = name
        else:
            # No target: for specialized bot, use project as target; for universal bot, use None
            if proj_baseline:
                proj = proj_baseline
                target_name = proj_baseline  # Use project name as target to invoke project.md
            else:
                proj = None
                target_name = None
        res = await _services.call_api.call_async(
            project=proj,
            agent=None,
            prompt=None,
            target=target_name,  # delegate target interpretation (prompt>agent>project) to the library
            input=input_text,
            echo=echo,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        # ... (rest of the code remains the same)
        try:
            ok = bool(res.get("ok")) if isinstance(res, dict) else None
            debug_print("[bot]", "[CALL_TASK]", f"result ok={ok}")
        except Exception:
            pass
        # Avoid double responses: the app pipeline publishes welcome/digest directly to Telegram.
        # On success: do not send an extra bot reply. On error: reply with a concise error.
        try:
            if not (isinstance(res, dict) and res.get("ok")):
                code_raw = res.get("code") if isinstance(res, dict) else None
                code = str(code_raw or "ERROR")
                if code.upper() == "NO_DATA_FOUND":
                    debug_print(
                        "[bot]", "[CALL_TASK]", "suppressing NO_DATA_FOUND response"
                    )
                    return
                status = (res.get("error_code") if isinstance(res, dict) else None) or 500
                desc = (
                    res.get("description") if isinstance(res, dict) else None
                ) or "Unknown error"
                # If the app pipeline already published the error to Telegram (common path:
                # Runner.run error -> build_and_run_agent sends a Telegram error notification,
                # then lib/api converts "Error:" output to an envelope with status=502),
                # suppress bot replies to avoid duplicates in the origin chat.
                should_suppress = int(status) == 502 and code.upper() in {"PIPELINE_ERROR", "UPSTREAM_CONNECT_ERROR"}
                # Never suppress moderation/errors that the user needs to see
                if should_suppress:
                    desc_lower = desc.lower()
                    if "moderation" in desc_lower or "blocked" in desc_lower:
                        should_suppress = False
                if should_suppress:
                    debug_print(
                        "[bot]",
                        "[CALL_TASK]",
                        f"suppressing duplicate pipeline error reply code={code} status={status}",
                    )
                    return
                await m.reply(f"Error: {code} ({status}): {desc}", parse_mode=None)
                debug_print("[bot]", "[CALL_TASK]", f"replied error code={code} status={status}")
            else:
                debug_print(
                    "[bot]",
                    "[CALL_TASK]",
                    "ok=true; no extra reply to avoid duplicates (pipeline published)",
                )
        except Exception:
            # Never let reply errors crash the task
            pass
        log.info("_call_task: done name=%s", name)
    except Exception as e:
        log.exception("_call_task: error name=%s", name)
        # Send error as plain text to avoid HTML entity parsing issues
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)
    finally:
        if typing_stop is not None:
            typing_stop.set()
        if typing_task is not None:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task
        if "token_reply" in locals() and token_reply is not None:
            try:
                app_call.reply_to_message_id_var.reset(token_reply)
            except Exception:
                pass


_BOT_INFO_CACHE: dict | None = None


def _build_telegram_file_url(path: str) -> str | None:
    try:
        url = (path or "").strip()
        if not url:
            return None
        if url.startswith("http://") or url.startswith("https://"):
            return url
        token = (
            os.environ.get("CALL_TELEGRAM_TOKEN")
            or os.environ.get("TELEGRAM_TOKEN")
            or ""
        )
        if not token:
            return None
        return f"https://api.telegram.org/file/bot{token}/{url}"
    except Exception:
        log.debug("Failed to build Telegram file URL", exc_info=True)
        return None


async def _build_resource_link_item(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
    name: str | None,
    mime_type: str | None,
    description: str,
    chat_id: int | None,
    message_id: int | None,
    direction: str,
) -> dict | None:
    try:
        debug_print(
            "[bot]",
            "[GET_FILE]",
            f"request file_id={file_id!r} chat_id={chat_id} message_id={message_id} direction={direction!r}",
        )
        f = await context.bot.get_file(file_id)
        file_path = getattr(f, "file_path", "") or ""
        uri = _build_telegram_file_url(file_path)
        debug_print(
            "[bot]",
            "[GET_FILE]",
            f"resolved file_id={file_id!r} file_path={file_path!r} uri={(uri or '')!r}",
        )
        if not uri:
            return None
        item: dict = {
            "type": "resource_link",
            "uri": uri,
            "url": uri,  # some consumers/tests expect `url`
            "name": (name or "attachment"),
            "description": description,
            "source": {
                "type": "telegram",
                "chat_id": chat_id,
                "message_id": message_id,
                "direction": direction,
            },
        }
        if mime_type:
            item["mimeType"] = mime_type
        return item
    except Exception:
        log.debug("Failed to resolve Telegram file to resource_link", exc_info=True)
        return None


def _describe_telegram_attachment(
    kind: str,
    *,
    width: int | None = None,
    height: int | None = None,
    file_size: int | None = None,
    mime_type: str | None = None,
    file_name: str | None = None,
) -> str:
    """Build a compact, human-readable description string for Telegram attachments."""
    base_kind = (kind or "attachment").strip().lower()
    base = f"Telegram {base_kind}"
    details: list[str] = []
    if isinstance(file_name, str) and file_name.strip():
        details.append(file_name.strip())
    parts: list[str] = []
    if isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0:
        parts.append(f"{width}x{height}")
    if isinstance(file_size, int) and file_size > 0:
        parts.append(f"size={file_size} bytes")
    if isinstance(mime_type, str) and mime_type.strip():
        parts.append(f"mime={mime_type.strip()}")
    if parts:
        details.append(", ".join(parts))
    if details:
        return base + " (" + "; ".join(details) + ")"
    return base


async def _collect_telegram_attachments(
    message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    direction: str,
) -> list[dict]:
    items: list[dict] = []
    if not message:
        return items
    try:
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
        message_id = getattr(message, "message_id", None)
        group_messages = _get_media_group_messages(message)

        for msg in group_messages:
            photos = getattr(msg, "photo", None) or []
            if not photos:
                continue
            best = photos[0]
            try:
                if TELEGRAM_PHOTO_VARIANT in {"smallest", "min"}:
                    best = min(
                        photos,
                        key=lambda p: (getattr(p, "width", 0) or 0)
                        * (getattr(p, "height", 0) or 0),
                    )
                elif TELEGRAM_PHOTO_VARIANT in {"largest", "max"}:
                    best = max(
                        photos,
                        key=lambda p: (getattr(p, "width", 0) or 0)
                        * (getattr(p, "height", 0) or 0),
                    )
                elif TELEGRAM_PHOTO_VARIANT == "last":
                    best = photos[-1]
                else:
                    best = photos[0]
            except Exception:
                best = photos[0]
            name = f"photo_{getattr(best, 'file_id', 'unknown')}.jpg"
            local_chat = getattr(msg, "chat", None)
            local_chat_id = getattr(local_chat, "id", chat_id)
            local_message_id = getattr(msg, "message_id", message_id)
            desc = _describe_telegram_attachment(
                "photo",
                width=getattr(best, "width", None),
                height=getattr(best, "height", None),
                file_size=getattr(best, "file_size", None),
                mime_type="image/jpeg",
            )
            photo_item = await _build_resource_link_item(
                context=context,
                file_id=getattr(best, "file_id", ""),
                name=name,
                mime_type="image/jpeg",
                description=desc,
                chat_id=local_chat_id,
                message_id=local_message_id,
                direction=direction,
            )
            if photo_item:
                items.append(photo_item)
        doc = getattr(message, "document", None)
        if doc and getattr(doc, "file_id", None):
            doc_name = getattr(doc, "file_name", None)
            doc_mime = getattr(doc, "mime_type", None)
            desc = _describe_telegram_attachment(
                "document",
                file_size=getattr(doc, "file_size", None),
                mime_type=str(doc_mime) if doc_mime else None,
                file_name=str(doc_name) if doc_name else None,
            )
            doc_item = await _build_resource_link_item(
                context=context,
                file_id=getattr(doc, "file_id", ""),
                name=str(doc_name) if doc_name else None,
                mime_type=str(doc_mime) if doc_mime else None,
                description=desc,
                chat_id=chat_id,
                message_id=message_id,
                direction=direction,
            )
            if doc_item:
                items.append(doc_item)
        video = getattr(message, "video", None)
        if video and getattr(video, "file_id", None):
            vid_name = getattr(video, "file_name", None)
            vid_mime = getattr(video, "mime_type", None)
            desc = _describe_telegram_attachment(
                "video",
                width=getattr(video, "width", None),
                height=getattr(video, "height", None),
                file_size=getattr(video, "file_size", None),
                mime_type=str(vid_mime) if vid_mime else None,
                file_name=str(vid_name) if vid_name else None,
            )
            video_item = await _build_resource_link_item(
                context=context,
                file_id=getattr(video, "file_id", ""),
                name=str(vid_name) if vid_name else None,
                mime_type=str(vid_mime) if vid_mime else None,
                description=desc,
                chat_id=chat_id,
                message_id=message_id,
                direction=direction,
            )
            if video_item:
                items.append(video_item)
        voice = getattr(message, "voice", None)
        if voice and getattr(voice, "file_id", None):
            voice_mime = getattr(voice, "mime_type", None)
            desc = _describe_telegram_attachment(
                "voice",
                file_size=getattr(voice, "file_size", None),
                mime_type=str(voice_mime) if voice_mime else None,
            )
            voice_item = await _build_resource_link_item(
                context=context,
                file_id=getattr(voice, "file_id", ""),
                name="voice_message",
                mime_type=str(voice_mime) if voice_mime else None,
                description=desc,
                chat_id=chat_id,
                message_id=message_id,
                direction=direction,
            )
            if voice_item:
                items.append(voice_item)
        audio = getattr(message, "audio", None)
        if audio and getattr(audio, "file_id", None):
            aud_name = getattr(audio, "file_name", None)
            aud_mime = getattr(audio, "mime_type", None)
            desc = _describe_telegram_attachment(
                "audio",
                file_size=getattr(audio, "file_size", None),
                mime_type=str(aud_mime) if aud_mime else None,
                file_name=str(aud_name) if aud_name else None,
            )
            audio_item = await _build_resource_link_item(
                context=context,
                file_id=getattr(audio, "file_id", ""),
                name=str(aud_name) if aud_name else None,
                mime_type=str(aud_mime) if aud_mime else None,
                description=desc,
                chat_id=chat_id,
                message_id=message_id,
                direction=direction,
            )
            if audio_item:
                items.append(audio_item)
    except Exception:
        log.debug("Failed to collect Telegram attachments", exc_info=True)
    return items


async def _handle_plain_text_with_media_group(
    m: Messenger,
    name: str | None,
    main_text: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int | None,
    thread_id: int | None,
) -> None:
    """Helper for handle_plain_text when the message is part of a media group.

    We wait briefly so that all messages in the same media_group_id are observed by
    _log_update and cached in _MEDIA_GROUP_CACHE, then build the input payload once
    using the aggregated attachments.
    """
    try:
        # Small delay to allow subsequent media_group messages to arrive and be
        # cached by _log_update before we build the payload.
        await asyncio.sleep(0.4)

        input_arg, _ = await build_input_payload_from_reply(
            name or None,
            main_text or "",
            update,
            context,
        )

        await _call_task(
            m,
            name or None,
            input_arg,
            echo=False,
            chat_id=chat_id,
            thread_id=thread_id,
        )
    except Exception:
        log.exception("handle_plain_text media_group task failed")


async def _get_bot_info(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    global _BOT_INFO_CACHE
    if isinstance(_BOT_INFO_CACHE, dict) and _BOT_INFO_CACHE:
        return _BOT_INFO_CACHE
    try:
        me = await context.bot.get_me()
        if hasattr(me, "to_dict"):
            data = me.to_dict()
        else:
            data = {
                "id": getattr(me, "id", None),
                "is_bot": getattr(me, "is_bot", None),
                "first_name": getattr(me, "first_name", None),
                "username": getattr(me, "username", None),
            }
        _BOT_INFO_CACHE = data
        return data
    except Exception:
        log.debug("Failed to fetch Telegram bot info via getMe", exc_info=True)
        return None


async def build_input_payload_from_reply(
    name: str | None, main_text: str, update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[str, dict | None]:
    """Build JSON payload from reply context if present and return (input_arg, payload_dict_or_None).

    - Includes target when provided
    - Adds context items for reply text and replied document (resolved to Telegram file URL)
    - Adds 'replay' field (string or array) for convenience
    - Sets 'input' to main_text; if empty, falls back to reply text
    - Returns (JSON-string, payload) when any field is present; otherwise (plain_text, None)
    """
    # Collect reply-derived context (files) and reply text; delegate JSON build to call_api
    ctx_items: list = []
    reply_text: str = ""
    attachments: list[dict] = []
    try:
        msg = getattr(update, "effective_message", None)
        if msg is None:
            msg = getattr(update, "message", None) or getattr(update, "edited_message", None)
        r = getattr(msg, "reply_to_message", None) if msg is not None else None
        if r is not None:
            r_text = (
                (getattr(r, "text", None) or getattr(r, "caption", None) or "")
                .strip()
            )
            if r_text:
                reply_text = r_text
            try:
                attachments.extend(
                    await _collect_telegram_attachments(
                        r,
                        context,
                        direction="replay",
                    )
                )
            except Exception:
                log.debug(
                    "Failed to collect Telegram attachments from reply_to_message",
                    exc_info=True,
                )
        if msg is not None:
            try:
                attachments.extend(
                    await _collect_telegram_attachments(
                        msg,
                        context,
                        direction="input",
                    )
                )
            except Exception:
                log.debug(
                    "Failed to collect Telegram attachments from current message",
                    exc_info=True,
                )
    except Exception:
        log.debug(
            "Failed to inspect Telegram message while building input payload",
            exc_info=True,
        )
    try:
        if attachments:
            seen_keys: set[tuple[str, str]] = set()
            for it in attachments:
                if not isinstance(it, dict):
                    continue
                uri = str(it.get("uri") or "")
                item_name = str(it.get("name") or "")
                key = (uri, item_name)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                ctx_items.append(it)
    except Exception:
        log.debug("Failed to deduplicate Telegram attachments", exc_info=True)
    if INCLUDE_TELEGRAM_MESSAGE_CONTEXT:
        try:
            if msg is not None and hasattr(msg, "to_dict"):
                ctx_items.append(
                    {
                        "type": "telegram_message",
                        "message": msg.to_dict(),
                    }
                )
            r = getattr(msg, "reply_to_message", None) if msg is not None else None
            if r is not None and hasattr(r, "to_dict"):
                ctx_items.append(
                    {
                        "type": "telegram_message",
                        "message": r.to_dict(),
                    }
                )
        except Exception:
            log.debug("Failed to append telegram_message context items", exc_info=True)
    if INCLUDE_TELEGRAM_BOT_CONTEXT:
        try:
            bot_info = await _get_bot_info(context)
            if isinstance(bot_info, dict) and bot_info:
                ctx_items.append({"type": "telegram_bot", "bot": bot_info})
        except Exception:
            log.debug("Failed to append telegram_bot context item", exc_info=True)
    # Delegate to library for predictable, shared behavior (no FS fallback; ordered keys)
    input_arg, payload = _services.call_api.build_input_payload(
        target=(name or None),
        main_text=(main_text or ""),
        extra_context=ctx_items or None,
        reply_text=(reply_text or None),
    )
    try:
        import json as _json

        # Pretty-print payload with indentation; cap length to ~2000 chars to avoid noisy logs
        txt = _json.dumps(payload or {}, ensure_ascii=False, indent=2)
        if len(txt) > 2000:
            txt = txt[:1997] + "..."
        debug_print("[bot]", "[PAYLOAD]", txt)
    except Exception:
        pass
    return (input_arg, payload)


@_require_allowed_users
async def handle_call(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = Messenger(context=context, update=update)
    text = (update.message.text or "") if update.message else ""
    log.debug("handle_call: incoming text=%r", text)
    debug_print("[bot]", "[CALL]", f"entry text={text!r}")

    # Parse optional --echo flag and extract Name/@Name + input (both forms are accepted)
    echo_flag = False
    try:
        t = text.strip()
        # If the command explicitly mentions another bot, ignore
        try:
            if t.lower().startswith("/call@"):  # explicit target bot mention
                end = t.find(" ")
                cmd_token = t if end == -1 else t[:end]
                mentioned = cmd_token[len("/call@") :].strip()
                own = (SELECTED_BOT_NAME or "").strip() or _project_to_bot_handle(
                    PROJECT_NAME
                )
                if mentioned and own and mentioned != own:
                    debug_print(
                        "[bot]", "[CALL]", f"ignoring command addressed to @{mentioned}"
                    )
                    return
        except Exception:
            pass
        if t.startswith("/call"):
            t = _extract_after("/call", t)
        elif t.lower().startswith("call"):
            t = _extract_after("call", t)
        # Drop optional @<own-bot-name> immediately following the command (e.g., "/call@StratoSpaceAiBot @Vasil3")
        try:
            own = (SELECTED_BOT_NAME or "").strip() or _project_to_bot_handle(
                PROJECT_NAME
            )
            own_at = ("@" + own) if own else ""
            # Quick path: if remainder starts with our @bot token, strip it
            if own_at and t.lstrip().startswith(own_at):
                t = t.lstrip()[len(own_at) :].lstrip()
        except Exception:
            pass
        # Remove --echo/—echo flags without collapsing whitespace
        _echo_pattern = re.compile(r"(?i)(?<!\S)(?:--|—|–)?echo(?!\S)")
        t_no_flags = _echo_pattern.sub("", t)
        if t_no_flags != t:
            echo_flag = True
        # Remove leading own @Bot token if present again after flags (preserving spacing)
        try:
            own = (SELECTED_BOT_NAME or "").strip() or _project_to_bot_handle(
                PROJECT_NAME
            )
            own_at = ("@" + own) if own else ""
            if own_at:
                leading_pat = re.compile(rf"^\s*{re.escape(own_at)}(?=\s|$)")
                t_no_flags = leading_pat.sub("", t_no_flags)
        except Exception:
            pass

        # Extract first token and remainder, preserving newlines
        t2 = t_no_flags.lstrip()
        if not t2:
            raise ValueError("Usage: /call [--echo] @Name <input>")
        ws_match = re.search(r"\s", t2)
        name_tok = t2[: ws_match.start()] if ws_match else t2
        remainder = t2[ws_match.start() :] if ws_match else ""
        # Parse target if first token starts with '@'
        if name_tok.startswith("@"):
            name = _normalize_token(name_tok)
            main_text = remainder.lstrip(" \t")
        else:
            name = ""
            main_text = t2
        debug_print("[bot]", "[CALL]", f"parsed name={name!r} echo={echo_flag}")
    except ValueError as ve:
        await m.reply(str(ve), parse_mode=None)
        return

    # No special-case for AgentFab; project is derived from bot name

    # Kick off a background task and pass the exact chat/thread where the command was received.
    # These values should take precedence over any Agent card defaults or env variables downstream.
    cid = update.effective_chat.id if update and update.effective_chat else None
    tid = update.message.message_thread_id if update and update.message else None
    # Provide original message id for reply threading in the app pipeline.
    # Use a task-local ContextVar to avoid cross-talk between concurrent requests.
    reply_id = None
    try:
        reply_id = update.message.message_id if update and update.message else None
    except Exception:
        reply_id = None
    input_arg, _ = await build_input_payload_from_reply(
        name or None, main_text or "", update, context
    )

    coro = _call_task(
        m,
        name or None,
        input_arg,
        echo=echo_flag,
        chat_id=cid,
        thread_id=tid,
    )
    try:
        ctx = contextvars.copy_context()
        try:
            ctx.run(app_call.reply_to_message_id_var.set, reply_id)
        except Exception:
            pass
        # Python 3.11+: create_task supports explicit context
        asyncio.create_task(coro, context=ctx)
    except TypeError:
        # Fallback for older runtimes: keep legacy global (best-effort)
        try:
            app_call.reply_to_message_id = reply_id
        except Exception:
            pass
        asyncio.create_task(coro)
    log.info("handle_call: scheduled task for name=%s", name)
    debug_print("[bot]", "[CALL]", f"scheduled name={name!r}")


@_require_allowed_users
async def handle_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List projects (StratoSpaceAiBot only)."""
    m = Messenger(context=context, update=update)
    if (SELECTED_BOT_NAME or "").strip() != "StratoSpaceAiBot":
        await m.reply(
            "/projects is available only for StratoSpaceAiBot", parse_mode=None
        )
        return
    try:
        debug_print("[bot]", "[PROJECTS]", "entry")
        tree = call_api.list(project=None)
        projects = [n.get("name") for n in (tree or [])]
        if not projects:
            await m.reply("No projects", parse_mode=None)
            return
        lines: list[str] = []
        for pname in projects:
            _at, url = _project_to_bot_link(pname)
            visible = f"@{(pname or '').strip()} Bot"
            if url:
                lines.append(f'<b><a href="{url}">{py_html.escape(visible)}</a></b>')
            else:
                lines.append(f"<b>{py_html.escape(visible)}</b>")
        payload = "\n".join(lines)
        debug_print("[bot]", "[PROJECTS]", f"reply_len={len(payload)}")
        await m.reply(payload, parse_mode=ParseMode.HTML)
    except Exception as e:
        debug_print("[bot]", "[PROJECTS]", f"error {type(e).__name__}: {e}")
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)


@_require_allowed_users
async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the SQLite conversation session(s) for this chat/thread."""
    m = Messenger(context=context, update=update)
    text = (update.message.text or "").strip() if update.message else ""
    debug_print("[bot]", "[CLEAR]", f"entry text={text!r}")
    try:
        # Parse optional agent name after /clear, allow @Name or Name
        parts = text.split(None, 1)
        arg = parts[1].strip() if len(parts) > 1 else ""
        if arg.startswith("/clear"):
            arg = arg[len("/clear") :].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        agent_name = arg or ""

        cid = update.effective_chat.id if update and update.effective_chat else None
        tid = update.message.message_thread_id if update and update.message else None

        res = await _services.call_api.clear_session(
            agent_name or None, chat_id=cid, thread_id=tid
        )
        if not isinstance(res, dict) or not res.get("ok"):
            await m.reply(
                f"Clear failed: {res.get('description', 'unknown error')}",
                parse_mode=None,
            )
            return

        cleared = res.get("cleared") or []
        head = (
            f"Cleared session for @{agent_name}"
            if agent_name
            else "Cleared sessions for all agents"
        )
        body = (
            "<code>" + "\n".join(cleared) + "</code>"
            if cleared
            else "(nothing to clear)"
        )
        await m.reply(f"{head}\n\n{body}", parse_mode=ParseMode.HTML)
        debug_print("[bot]", "[CLEAR]", f"cleared_count={len(cleared)}")
    except Exception as e:
        debug_print("[bot]", "[CLEAR]", f"error {type(e).__name__}: {e}")
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)


@_require_allowed_users
async def handle_plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text or caption messages (non-command).

    Behavior:
    - Private chats: "@Name <input>" treated as /call @Name <input>, "text" as /call text
    - Group chats: Only "@Name <input>" handled (ignore other messages to avoid noise)
    - Supports both text messages and media messages with a caption.
    - Target resolution delegated to call_api.call_async() without pre-validation
    """
    msg = getattr(update, "effective_message", None)
    if msg is None:
        msg = getattr(update, "message", None) or getattr(update, "edited_message", None)
    text = ((msg.text or msg.caption or "").strip()) if msg else ""
    has_media = False
    try:
        if msg:
            has_media = any(
                [
                    getattr(msg, "photo", None),
                    getattr(msg, "document", None),
                    getattr(msg, "video", None),
                    getattr(msg, "audio", None),
                    getattr(msg, "voice", None),
                ]
            )
    except Exception:
        has_media = False
    log.debug("handle_plain_text: text=%r has_media=%s", text, has_media)
    if not text and not has_media:
        return
    # Determine chat type early (affects whether we strip own @Bot mention)
    try:
        is_private = bool(
            getattr(getattr(update, "effective_chat", None), "type", "") == "private"
        )
    except Exception:
        is_private = False
    # In private DMs only: strip leading own @Bot mention to allow natural text parsing
    if is_private:
        try:
            own = (SELECTED_BOT_NAME or "").strip() or _project_to_bot_handle(
                PROJECT_NAME
            )
            own_at = ("@" + own) if own else ""
            if own_at and text.startswith(own_at):
                text = text[len(own_at) :].lstrip()
        except Exception:
            pass
    # Resolve agent and input according to chat type using shared helper
    try:
        base = _get_bot_project(update)
    except Exception:
        base = ""
    # If message has media but no text/caption:
    # - Private chats: handle it (invoke default project / target) so voice/photos "just work".
    # - Group chats: ignore it to avoid noise; require an explicit mention/target in text/caption.
    if has_media and not text:
        name = base or PROJECT_NAME or ""
        main_text = ""
        should_handle = bool(is_private)
    else:
        try:
            name, main_text, should_handle = _resolve_agent_and_input(
                text, base, is_private=is_private
            )
        except Exception as e:
            # Conservative fallback: do not handle to avoid scheduling unwanted tasks
            log.warning(
                "handle_plain_text: _resolve_agent_and_input failed: %s: %s",
                type(e).__name__, e
            )
            name, main_text, should_handle = "", "", False
    if not should_handle:
        debug_print("[bot]", "[PLAIN]", "ignored (should_handle=false)")
        return
    cid = update.effective_chat.id if update and update.effective_chat else None
    tid = msg.message_thread_id if msg else None
    media_group_id = getattr(msg, "media_group_id", None) if msg is not None else None
    messenger = Messenger(context=context, update=update)

    if media_group_id:
        # For albums, defer payload construction slightly so that all messages
        # in the media group are cached and attachments from the whole group
        # can be included.
        asyncio.create_task(
            _handle_plain_text_with_media_group(
                messenger,
                name or None,
                main_text or "",
                update,
                context,
                chat_id=cid,
                thread_id=tid,
            )
        )
        debug_print("[bot]", "[PLAIN]", f"scheduled (media_group) name={name!r}")
        return

    input_arg, _ = await build_input_payload_from_reply(
        name or None, main_text or "", update, context
    )
    asyncio.create_task(
        _call_task(
            messenger,
            name or None,
            input_arg,
            echo=False,
            chat_id=cid,
            thread_id=tid,
        )
    )
    debug_print("[bot]", "[PLAIN]", f"scheduled name={name!r}")


def main() -> None:
    # Configure logging once per bot process (DEBUG if CALL_DEBUG=1, else INFO)
    call_logging()
    # Parse CLI args
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.echo:
        cfg = _current_config_dict()
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return

    # Resolve project name from optional bot handle by stripping common suffixes
    global SELECTED_BOT_NAME, PROJECT_NAME
    SELECTED_BOT_NAME = (args.bot_name or args.bot_name_pos or "").strip()
    PROJECT_NAME = _strip_bot_suffix(SELECTED_BOT_NAME) if SELECTED_BOT_NAME else ""
    debug_print(
        "[bot]", "[MAIN]", f"bot={SELECTED_BOT_NAME!r} project={PROJECT_NAME!r}"
    )

    # KISS: require project name to be provided; call layer will use it to fetch the token
    if not PROJECT_NAME:
        print("Error: --bot-name (project name) is required", file=sys.stderr)
        sys.exit(1)

    # Configure HTTPX with sane timeouts to reduce startup/long-poll issues
    # Also bypass system proxies for Telegram domains (common cause of timeouts)
    os.environ.setdefault("NO_PROXY", "api.telegram.org,*.telegram.org")
    os.environ.setdefault("no_proxy", "api.telegram.org,*.telegram.org")
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=120.0,
        write_timeout=60.0,
        pool_timeout=30.0,
        httpx_kwargs={
            "event_hooks": {"response": [_tap_getupdates_response]},
        },
    )

    # Ensure repo index is loaded before polling to avoid NO_DATA_FOUND on first calls
    try:
        r = _services.call_api.reload()
        debug_print(
            "[bot]",
            "[MAIN]",
            f"reload scanned={getattr(r, 'get', lambda k, d=None: d)('scanned', None) if isinstance(r, dict) else r}",
        )
    except Exception:
        pass

    # Use the single source of truth to get the token for polling
    polling_token = get_project_token(PROJECT_NAME)
    # Ensure downstream app pipeline prefers this bot token
    try:
        os.environ["CALL_TELEGRAM_TOKEN"] = polling_token
    except Exception:
        pass
    
    # MCP initialization - use shared helper from call.py
    post_init, post_shutdown = create_mcp_lifespan_callbacks("bot")
    
    app = (
        ApplicationBuilder()
        .token(polling_token)
        .request(request)
        .get_updates_request(request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_help))
    # Preferred: /agents; keep /list as a temporary alias
    app.add_handler(CommandHandler("agents", handle_list))
    app.add_handler(CommandHandler("list", handle_list))
    app.add_handler(CommandHandler("projects", handle_projects))
    app.add_handler(CommandHandler("prompts_ready", handle_prompts_ready))
    app.add_handler(CommandHandler("prompts_draft", handle_prompts_draft))
    app.add_handler(CommandHandler("prompts", handle_prompts))
    app.add_handler(CommandHandler("reload", handle_reload))
    app.add_handler(CommandHandler("call", handle_call))
    app.add_handler(CommandHandler("clear", handle_clear))

    # Project-defined command aliases (from project.md METADATA in repo.db)
    try:
        if PROJECT_NAME:
            project_specs = _get_project_command_specs(PROJECT_NAME)
        else:
            project_specs = []
    except Exception:
        project_specs = []
    project_names = [spec.name for spec in project_specs]
    for cmd in _filter_custom_commands(project_names):
        app.add_handler(CommandHandler(cmd, handle_project_command))

    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION | filters.ATTACHMENT) & (~filters.COMMAND),
            handle_plain_text,
        )
    )
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION | filters.ATTACHMENT)
            & filters.UpdateType.EDITED_MESSAGE
            & (~filters.COMMAND),
            handle_plain_text,
        )
    )
    # Log all incoming updates at the end so it doesn't interfere with other handlers
    app.add_handler(TypeHandler(Update, _log_update), group=100)
    # Global error handler
    app.add_error_handler(_error_handler)

    # Run the application (blocking)
    log.info("Starting polling...")
    debug_print("[bot]", "[MAIN]", "run_polling")
    try:
        app.run_polling(drop_pending_updates=_DROP_PENDING_UPDATES)
    except Exception:
        log.exception("run_polling terminated with error")
        raise


if __name__ == "__main__":
    main()
