"""
Telegram bot for the call subsystem.

Commands:
- /agents [--aliases] [--q "filter"]
- /call @Name <input>

The bot only interacts with the call library API and does not directly use OpenAI or Telegraph.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
import logging
from typing import Callable, Awaitable, Optional
import re
import html as py_html
import argparse
import json
import sys

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Telegram bot entrypoint for Call")
    p.add_argument("bot_name_pos", nargs="?", help="Bot handle, e.g. StratoSpaceAiBot")
    p.add_argument("--bot-name", dest="bot_name", default=None, help="Bot handle (same as positional)")
    p.add_argument("--echo", action="store_true", help="Print effective config and exit")
    return p

from dotenv import load_dotenv
from pathlib import Path
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut
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
from call.lib.logging import debug_print, configure_logging, get_logger
from call.app.utils.telegram_text import (
    telegram_truncate_html_safe,
    telegram_prepare_html,
    telegram_prepare_markdown,
)
from call.app.call import get_project_token
from call.lib.discovery import prompts as _lib_prompts


# Load environment from call/.env first (module-relative), then allow process env to override
_CALL_DIR = Path(__file__).resolve().parent.parent  # .../call/
_CALL_ENV = _CALL_DIR / ".env"
if _CALL_ENV.exists():
    load_dotenv(dotenv_path=str(_CALL_ENV), override=True)
# Load default .env (cwd) and OS env; allow overriding too
load_dotenv(override=True)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
# Optional selected bot name from CLI (set later in main) or env
SELECTED_BOT_NAME: str = os.environ.get("BOT_NAME", "").strip()
# Derived project name is computed in main after parsing args
PROJECT_NAME: str = ""
_ALLOWED_USERS_RAW = os.environ.get("ALLOWED_USERS", "").strip()
DROP_PENDING_UPDATES_RAW = os.environ.get("DROP_PENDING_UPDATES", "").strip()

# Module logger (emits once configure_logging() is called by the entrypoint)
log = get_logger("bot")

log.info("Loaded env: call/.env exists=%s", _CALL_ENV.exists())
masked = TELEGRAM_TOKEN[:6] + "..." if TELEGRAM_TOKEN else "<empty>"
log.info("TELEGRAM_TOKEN(prefix)=%s, ALLOWED_USERS_raw_len=%d", masked, len(_ALLOWED_USERS_RAW))


def _parse_allowed_users(raw: str) -> set[int]:
    out: set[int] = set()
    if not raw:
        return out
    for part in raw.split(','):
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
    if v in ("1", "true", "yes", "on"): return True
    if v in ("0", "false", "no", "off"): return False
    return default

# Materialize parsed envs
_ALLOWED_USERS = _parse_allowed_users(_ALLOWED_USERS_RAW)
_DROP_PENDING_UPDATES = _env_to_bool(DROP_PENDING_UPDATES_RAW, default=False)


def _current_config_dict() -> dict:
    """Collect key startup parameters for echo/debug output."""
    try:
        allowed = sorted(list(_ALLOWED_USERS)) if '_ALLOWED_USERS' in globals() else []
    except Exception:
        allowed = []
    return {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "ALLOWED_USERS": allowed,
        "DEBUG": os.environ.get("DEBUG", ""),
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
        kind = None
        data = None
        if update.message:
            kind = "message"
            data = (update.message.text or update.message.caption or "").strip()
        elif update.edited_message:
            kind = "edited_message"
            data = (update.edited_message.text or update.edited_message.caption or "").strip()
        elif update.callback_query:
            kind = "callback_query"
            data = (getattr(update.callback_query, "data", "") or "").strip()
        elif update.channel_post:
            kind = "channel_post"
            data = (update.channel_post.text or update.channel_post.caption or "").strip()
        else:
            kind = "other"
            data = ""
        data = (data or "")[:120].replace("\n", " ")
        return f"uid={uid} chat={chat_id} user={user_id} kind={kind} data={data!r}"
    except Exception:
        return "<unavailable>"


async def _log_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """TypeHandler callback to log every incoming update (uses CALL_DEBUG via debug_print)."""
    summary = _summarize_update(update)
    # Structured app log remains at INFO
    log.info("Update: %s", summary)
    # Console debug output is gated by CALL_DEBUG through debug_print
    # Module prefix first, optional tags second
    debug_print("[bot]", "[UPDATE]", summary)
    return None


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler to avoid unhandled exception warnings and provide context."""
    try:
        summary = _summarize_update(update) if isinstance(update, Update) else "<non-Update>"
    except Exception:
        summary = "<unavailable>"
    # Log the exception with traceback
    try:
        err = getattr(context, "error", None)
        if err is not None:
            # Provide traceback tuple explicitly for structured logs
            log.error("Unhandled error while processing update: %s (%s: %s)", summary, type(err).__name__, str(err), exc_info=(type(err), err, getattr(err, "__traceback__", None)))
            debug_print("[bot]", "[ERROR]", f"{type(err).__name__}: {err}", "|", summary)
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

    async def reply(self, text: str, *, parse_mode: Optional[str] = ParseMode.HTML) -> None:
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
                prepared_text, pm = telegram_prepare_markdown(prepared_text, 4000, version="v2")
                use_parse_mode = pm
            else:
                # Plain text path; enforce limit
                if len(prepared_text) > 4096:
                    prepared_text = prepared_text[:4095] + "…"
                use_parse_mode = None
        except Exception:
            # If anything goes wrong, fallback to plain
            use_parse_mode = None
            prepared_text = (prepared_text[:4095] + "…") if len(prepared_text) > 4096 else prepared_text

        async def _send(pt: str, pmode: Optional[str]):
            debug_print("[bot]", "[SEND]", f"len={len(pt)}", f"pmode={pmode}")
            if self.update.message:
                res = await self.update.message.reply_text(text=pt, parse_mode=pmode)
                debug_print("[bot]", "[SENT]", "via reply_text")
                return res
            elif self.update.effective_chat:
                res = await self.context.bot.send_message(chat_id=self.update.effective_chat.id, text=pt, parse_mode=pmode)
                debug_print("[bot]", "[SENT]", f"via send_message chat_id={getattr(self.update.effective_chat, 'id', None)}")
                return res

        # Retry loop for transient timeouts
        for attempt in range(3):
            try:
                await _send(prepared_text, use_parse_mode)
                return
            except BadRequest as e:
                # Fallback to plain text if Telegram can't parse entities
                msg = str(e).lower()
                if "can't parse entities" in msg or "parse entities" in msg or "entity" in msg:
                    debug_print("[bot]", "[WARN]", f"BadRequest parse error: {e}; falling back to plain")
                    plain = re.sub(r"<[^>]+>", "", prepared_text)
                    if len(plain) > 4096:
                        plain = plain[:4095] + "…"
                    try:
                        await _send(plain, None)
                    except Exception as e2:
                        debug_print("[bot]", "[ERROR]", f"Fallback send failed: {type(e2).__name__}: {e2}")
                        raise
                    return
                raise
            except TimedOut:
                debug_print("[bot]", "[WARN]", f"TimedOut on attempt {attempt+1}/3")
                if attempt == 2:
                    break
                await asyncio.sleep(1 * (2 ** attempt))
            except Exception as e:
                # Last-resort fallback to plain
                debug_print("[bot]", "[ERROR]", f"Send failed: {type(e).__name__}: {e}; falling back to plain")
                plain = re.sub(r"<[^>]+>", "", prepared_text)
                if len(plain) > 4096:
                    plain = plain[:4095] + "…"
                try:
                    await _send(plain, None)
                    return
                except Exception as e2:
                    debug_print("[bot]", "[ERROR]", f"Plain fallback also failed: {type(e2).__name__}: {e2}")
                    raise
        # If retries exhausted due to TimedOut, send minimal plain notification
        fallback = "Service temporarily unavailable. Please try again later."
        try:
            await _send(fallback, None)
        except Exception as e3:
            debug_print("[bot]", "[ERROR]", f"Minimal fallback failed: {type(e3).__name__}: {e3}")


# Authorization decorator

def _require_allowed_users(func: Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if ALLOWED_USERS:
            uid = update.effective_user.id if update.effective_user else None
            cid = update.effective_chat.id if update.effective_chat else None
            ok = (uid in ALLOWED_USERS) or (cid in ALLOWED_USERS)
            if not ok:
                # Minimal feedback without leaking info
                if update.message:
                    await update.message.reply_text("Unauthorized")
                return
        await func(update, context)
    return wrapper


# Command parsing helpers

def _extract_after(prefix: str, text: str) -> str:
    return text[len(prefix):].strip()


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


def _resolve_agent_and_input(text: str, base_project: str, *, is_private: bool) -> tuple[str, str, bool]:
    """Parse plain text into (agent_name, input_text, should_handle).

    Rules:
    - In groups (is_private=False), only handle messages starting with '@Name'.
    - In private chats, 'Name <input>' and '@Name <input>' are both accepted.
    - Minimal special-case: when text starts with '@' and no name is provided, do not handle.
      (Earlier behavior mentioned AgentFab default, but we keep this conservative here.)
    """
    s = (text or "").strip()
    if not s:
        return "", "", False
    # Groups must mention explicitly
    if not is_private and not s.startswith("@"):
        return "", "", False
    if s.startswith("@"):
        body = s[1:].lstrip()
        if not body:
            # '@' alone — ignore
            return "", "", False
        parts = body.split(None, 1)
        name = parts[0].lstrip("@")
        rest = parts[1] if len(parts) > 1 else ""
        return name, rest, True
    # Private chat: allow 'Name <input>' without '@'
    parts = s.split(None, 1)
    if not parts:
        return "", "", False
    name = parts[0].lstrip("@")
    rest = parts[1] if len(parts) > 1 else ""
    return name, rest, True


@_require_allowed_users
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.debug("handle_start: chat_id=%s user_id=%s", getattr(update.effective_chat, 'id', None), getattr(update.effective_user, 'id', None))
    m = Messenger(context=context, update=update)
    debug_print("[bot]", "[START]", f"entry chat_id={getattr(update.effective_chat, 'id', None)} user_id={getattr(update.effective_user, 'id', None)}")
    await m.reply(
        """
call-bot

Commands:
- /call [--echo] @Name <input>
- /call [--echo] Name <input>  (equivalent to @Name)
- /agents [--aliases] [--q "filter"]
- /clear [@Name]  (clear conversation session for current chat/thread; all agents if name omitted)

Startup options:
- --bot-name Name  (token lookup: TELEGRAM_TOKEN.Name in env/.env; if --bot-name is not provided, falls back to TELEGRAM_TOKEN)

Plain text (no slash):
- In private chat: "@Name <input>" and "Name <input>" are equivalent.
- In groups: only explicit "@Name <input>" is handled to avoid reacting to every message.

Special cases:
- If this bot is AgentFabBot, default agent is AgentFab when no name is specified (e.g., "@ <input>").
    
Notes:
- /agents lists one name per line as @Name.
- With --aliases, alias lines are indented with two spaces before @ (e.g., "  @Alias").
        """.strip(),
        parse_mode=None,
    )
    debug_print("[bot]", "[START]", "replied")


@_require_allowed_users
async def handle_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.debug("handle_list: incoming text=%r", getattr(update.message, 'text', None))
    m = Messenger(context=context, update=update)
    debug_print("[bot]", "[AGENTS]", f"entry text={getattr(update.message, 'text', None)!r}")

    # Scope by project (derive from bot); StratoSpaceAiBot lists all projects
    proj = PROJECT_NAME or None
    if (SELECTED_BOT_NAME or "").strip() == "StratoSpaceAiBot":
        proj = None
    try:
        tree = call_api.list(project=proj)
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
                lines.append(f"<b><a href=\"{url}\">{py_html.escape(visible)}</a></b>")
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
    name = str(item.get('name') or item.get('prompt_id') or '').strip()
    url = item.get('url')
    # Prefer Markdown link when URL is available; fallback to plain title
    title = f"[{name}]({url})" if (url and name) else (name or '(untitled)')
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


async def _send_markdown_rows_chunked(m: Messenger, rows: list[str], *, header: str | None = None, max_len: int = 3800) -> None:
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
async def handle_prompts_ready(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List ready prompts. Usage: /prompts_ready [<project>] [<agent>|@Agent]"""
    m = Messenger(context=context, update=update)
    debug_print("[bot]", "[PROMPTS_READY]", f"entry text={getattr(update.message, 'text', None)!r}")
    try:
        text = (update.message.text or "").strip() if update.message else ""
        tokens = text.split()
        # Drop the leading command token even if it has a @Bot suffix
        if tokens and tokens[0].startswith("/prompts_ready"):
            tokens = tokens[1:]
        args = tokens
        # Derive defaults: if a specific bot is used, default project from bot name
        proj_default = _get_bot_project(update) or None
        project = None
        agent = None
        for tok in args:
            t = tok.strip()
            if not t:
                continue
            if t.startswith("@"):
                agent = t[1:]
            elif project is None:
                project = t
            else:
                # If project already set, treat the next token as agent if not prefixed
                agent = t
        # Use default project if none provided
        project = project or proj_default
        items = _lib_prompts(project=project, agent=agent, state='ready')
        if not items and proj_default and not (project and project != proj_default):
            items = _lib_prompts(project=None, agent=agent, state='ready')
        rows = [_format_prompt_markdown_row(x) for x in items]
        debug_print("[bot]", "[PROMPTS_READY]", f"rows={len(rows)} project={project!r} agent={agent!r}")
        await _send_markdown_rows_chunked(m, rows)
    except Exception as e:
        debug_print("[bot]", "[PROMPTS_READY]", f"error {type(e).__name__}: {e}")
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)


@_require_allowed_users
async def handle_prompts_draft(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List draft prompts. Usage: /prompts_draft [<project>] [<agent>|@Agent]"""
    m = Messenger(context=context, update=update)
    debug_print("[bot]", "[PROMPTS_DRAFT]", f"entry text={getattr(update.message, 'text', None)!r}")
    try:
        text = (update.message.text or "").strip() if update.message else ""
        tokens = text.split()
        # Drop the leading command token even if it has a @Bot suffix
        if tokens and tokens[0].startswith("/prompts_draft"):
            tokens = tokens[1:]
        args = tokens
        proj_default = _get_bot_project(update) or None
        project = None
        agent = None
        for tok in args:
            t = tok.strip()
            if not t:
                continue
            if t.startswith("@"):
                agent = t[1:]
            elif project is None:
                project = t
            else:
                agent = t
        project = project or proj_default
        items = _lib_prompts(project=project, agent=agent, state='draft')
        if not items and proj_default and not (project and project != proj_default):
            items = _lib_prompts(project=None, agent=agent, state='draft')
        rows = [_format_prompt_markdown_row(x) for x in items]
        await _send_markdown_rows_chunked(m, rows)
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
    try:
        log.info(
            "_call_task: start name=%s input_len=%d echo=%s chat_id=%s thread_id=%s",
            name,
            len(input_text or ''),
            echo,
            chat_id,
            thread_id,
        )
        debug_print("[bot]", "[CALL_TASK]", f"start name={name} len={len(input_text or '')} echo={echo} chat_id={chat_id} thread_id={thread_id}")
        # Delegate to lib; it will publish to Telegram via its own utilities
        proj = None if (SELECTED_BOT_NAME or "").strip() == "StratoSpaceAiBot" else (PROJECT_NAME or None)
        res = await call_api.call_async(
            project=proj,
            agent=None,
            prompt=None,
            target=name,  # delegate target interpretation (prompt>agent>project) to the library
            input=input_text,
            echo=echo,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        try:
            ok = bool(res.get("ok")) if isinstance(res, dict) else None
            debug_print("[bot]", "[CALL_TASK]", f"result ok={ok}")
        except Exception:
            pass
        # Always send a minimal acknowledgement to the user to avoid "silent" behavior
        try:
            if isinstance(res, dict) and res.get("ok"):
                out = res.get("final_output")
                text = out if isinstance(out, str) and out.strip() else "Done."
                await m.reply(text, parse_mode=None)
                debug_print("[bot]", "[CALL_TASK]", f"replied ok len={len(text)}")
            else:
                code = (res.get("code") if isinstance(res, dict) else None) or "ERROR"
                status = (res.get("error_code") if isinstance(res, dict) else None) or 500
                desc = (res.get("description") if isinstance(res, dict) else None) or "Unknown error"
                await m.reply(f"Error: {code} ({status}): {desc}", parse_mode=None)
                debug_print("[bot]", "[CALL_TASK]", f"replied error code={code} status={status}")
        except Exception:
            # Never let reply errors crash the task
            pass
        log.info("_call_task: done name=%s", name)
    except Exception as e:
        log.exception("_call_task: error name=%s", name)
        # Send error as plain text to avoid HTML entity parsing issues
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)


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
        if t.startswith("/call"):
            t = _extract_after("/call", t)
        elif t.lower().startswith("call"):
            t = _extract_after("call", t)
        # Tokenize and remove --echo occurrences (support ASCII and Unicode dashes) before parsing @Name
        parts = t.split()
        filtered: list[str] = []
        for p in parts:
            token = p.strip()
            # Normalize leading dashes: '-', '--', '–', '—' (en/em dash). We only check for 'echo' flag.
            normalized = token.lstrip("-–—")
            if normalized == "echo" and token != "echo":
                echo_flag = True
                continue
            if token == "--echo":
                echo_flag = True
                continue
            filtered.append(p)
        t2 = " ".join(filtered).lstrip()
        if not t2:
            raise ValueError("Usage: /call [--echo] @Name <input>")
        name_tok, rest = (t2.split(maxsplit=1) + [""])[:2]
        name = name_tok[1:] if name_tok.startswith("@") else name_tok
        debug_print("[bot]", "[CALL]", f"parsed name={name!r} echo={echo_flag}")
    except ValueError as ve:
        await m.reply(str(ve), parse_mode=None)
        return

    # No special-case for AgentFab; project is derived from bot name

    # Kick off a background task and pass the exact chat/thread where the command was received.
    # These values should take precedence over any Agent card defaults or env variables downstream.
    cid = update.effective_chat.id if update and update.effective_chat else None
    tid = update.message.message_thread_id if update and update.message else None
    asyncio.create_task(
        _call_task(
            m,
            name,
            rest,
            echo=echo_flag,
            chat_id=cid,
            thread_id=tid,
        )
    )
    log.info("handle_call: scheduled task for name=%s", name)
    debug_print("[bot]", "[CALL]", f"scheduled name={name!r}")


@_require_allowed_users
async def handle_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List projects (StratoSpaceAiBot only)."""
    m = Messenger(context=context, update=update)
    if (SELECTED_BOT_NAME or "").strip() != "StratoSpaceAiBot":
        await m.reply("/projects is available only for StratoSpaceAiBot", parse_mode=None)
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
            arg = arg[len("/clear"):].strip()
        if arg.startswith("@"):
            arg = arg[1:]
        agent_name = arg or ""

        cid = update.effective_chat.id if update and update.effective_chat else None
        tid = update.message.message_thread_id if update and update.message else None

        res = await call_api.clear_session(agent_name or None, chat_id=cid, thread_id=tid)
        if not isinstance(res, dict) or not res.get("ok"):
            await m.reply(f"Clear failed: {res.get('description', 'unknown error')}", parse_mode=None)
            return

        cleared = res.get("cleared") or []
        head = f"Cleared session for @{agent_name}" if agent_name else "Cleared sessions for all agents"
        body = "<code>" + "\n".join(cleared) + "</code>" if cleared else "(nothing to clear)"
        await m.reply(f"{head}\n\n{body}", parse_mode=ParseMode.HTML)
        debug_print("[bot]", "[CLEAR]", f"cleared_count={len(cleared)}")
    except Exception as e:
        debug_print("[bot]", "[CLEAR]", f"error {type(e).__name__}: {e}")
        await m.reply(f"Error: {type(e).__name__}: {str(e)}", parse_mode=None)

@_require_allowed_users
async def handle_plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip() if update.message else ""
    log.debug("handle_plain_text: text=%r", text)
    if not text:
        return
    base = _get_bot_project(update)
    chat_type = getattr(getattr(update, "effective_chat", None), "type", "") or ""
    is_private = (chat_type == "private")
    name, inp, should_handle = _resolve_agent_and_input(text, base, is_private=is_private)
    if not should_handle:
        debug_print("[bot]", "[PLAIN]", f"ignored text={text!r}")
        return
    cid = update.effective_chat.id if update and update.effective_chat else None
    tid = update.message.message_thread_id if update and update.message else None
    asyncio.create_task(
        _call_task(
            Messenger(context=context, update=update),
            name,
            inp,
            echo=False,
            chat_id=cid,
            thread_id=tid,
        )
    )
    debug_print("[bot]", "[PLAIN]", f"scheduled name={name!r}")


def main() -> None:
    # CLIasync def main():
    # Configure logging once per bot process (DEBUG if CALL_DEBUG=1, else INFO)
    try:
        configure_logging()
    except Exception:
        pass
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
    debug_print("[bot]", "[MAIN]", f"bot={SELECTED_BOT_NAME!r} project={PROJECT_NAME!r}")

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
    )

    # Use the single source of truth to get the token for polling
    polling_token = get_project_token(PROJECT_NAME)
    app = (
        ApplicationBuilder()
        .token(polling_token)
        .request(request)
        .get_updates_request(request)
        .build()
    )

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_start))
    # Preferred: /agents; keep /list as a temporary alias
    app.add_handler(CommandHandler("agents", handle_list))
    app.add_handler(CommandHandler("list", handle_list))
    app.add_handler(CommandHandler("projects", handle_projects))
    app.add_handler(CommandHandler("prompts_ready", handle_prompts_ready))
    app.add_handler(CommandHandler("prompts_draft", handle_prompts_draft))
    app.add_handler(CommandHandler("call", handle_call))
    app.add_handler(CommandHandler("clear", handle_clear))

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_plain_text))
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
