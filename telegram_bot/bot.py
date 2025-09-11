"""
Telegram bot for the call subsystem.

Commands:
- /list [--aliases] [--q "filter"]
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
from call.lib.discovery import to_pascal_case
from call.app.utils.telegram_text import (
    telegram_truncate_html_safe,
    telegram_prepare_html,
    telegram_prepare_markdown,
)


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
_ALLOWED_USERS_RAW = os.environ.get("ALLOWED_USERS", "").strip()
DROP_PENDING_UPDATES_RAW = os.environ.get("DROP_PENDING_UPDATES", "").strip()

# Configure logging early
_debug_env = os.environ.get("DEBUG", "").strip().lower()
_level = logging.DEBUG if _debug_env in ("1", "true", "yes", "on") else logging.INFO
logging.basicConfig(level=_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("call.telegram_bot")
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
    """TypeHandler callback to log every incoming update."""
    summary = _summarize_update(update)
    log.info("Update: %s", summary)
    # Also print to stdout for easy grepping when logs are redirected
    try:
        print(f"[UPDATE] {summary}")
    except Exception:
        pass
    return None


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler to avoid unhandled exception warnings and provide context."""
    try:
        summary = _summarize_update(update) if isinstance(update, Update) else "<non-Update>"
    except Exception:
        summary = "<unavailable>"
    # Log the exception with traceback
    log.exception("Unhandled error while processing update: %s", summary, exc_info=context.error)


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
        # Small semaphore to avoid exhausting HTTPX connection pool under bursts
        if not hasattr(self.context.application, "_send_semaphore"):
            self.context.application._send_semaphore = asyncio.Semaphore(5)

        async with self.context.application._send_semaphore:
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
                if self.update.message:
                    return await self.update.message.reply_text(text=pt, parse_mode=pmode)
                elif self.update.effective_chat:
                    return await self.context.bot.send_message(chat_id=self.update.effective_chat.id, text=pt, parse_mode=pmode)

            # Retry loop for transient timeouts
            for attempt in range(3):
                try:
                    await _send(prepared_text, use_parse_mode)
                    return
                except BadRequest as e:
                    # Fallback to plain text if Telegram can't parse entities
                    msg = str(e).lower()
                    if "can't parse entities" in msg or "parse entities" in msg or "entity" in msg:
                        plain = re.sub(r"<[^>]+>", "", prepared_text)
                        if len(plain) > 4096:
                            plain = plain[:4095] + "…"
                        await _send(plain, None)
                        return
                    raise
                except TimedOut:
                    if attempt == 2:
                        break
                    await asyncio.sleep(1 * (2 ** attempt))
                except Exception:
                    # Last-resort fallback to plain
                    plain = re.sub(r"<[^>]+>", "", prepared_text)
                    if len(plain) > 4096:
                        plain = plain[:4095] + "…"
                    await _send(plain, None)
                    return
            # If retries exhausted due to TimedOut, send minimal plain notification
            fallback = "Service temporarily unavailable. Please try again later."
            await _send(fallback, None)


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


def _get_bot_base(update: Update | None = None) -> str:
    """Return normalized base name of this bot (e.g., 'AgentFab' for 'AgentFabBot') without calling Telegram API.

    Preference:
    1) Use SELECTED_BOT_NAME if set (e.g., provided via --bot-name)
    2) Otherwise, cannot reliably infer bot username from Update; return empty string.
    """
    if SELECTED_BOT_NAME:
        return to_pascal_case(_strip_bot_suffix(SELECTED_BOT_NAME))
    return ""


def _exists_agent(name: str) -> bool:
    try:
        items = call_api.list(query=None, include_aliases=False, grouped=False)
        return any((it.get("name") or "") == name for it in (items or []))
    except Exception:
        return False


def _resolve_agent_and_input(text: str, base: str, *, is_private: bool) -> tuple[str, str, bool]:
    """Resolve (agent_name, input_text, should_handle) from a free-text message.

    Rules:
    - Private chats: "@Name ..." and "Name ..." are equivalent. If no explicit name,
      fall back to empty agent; but if base == AgentFab, default to AgentFab.
      Special case: leading '@' with no name (e.g., "@ text") → empty agent or AgentFab.
    - Group/supergroup: only explicit "@Name ..." is handled; otherwise do not handle.
    """
    t = (text or "").strip()
    default_base = "AgentFab" if base == "AgentFab" else ""

    if is_private:
        if not t:
            return (default_base, t, True)
        if t.startswith("@"):
            parts = t.split(maxsplit=1)
            tok = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            candidate = tok[1:]
            if not candidate:
                # '@ <text>' → empty agent, or AgentFab for AgentFabBot
                return (default_base, rest or "", True)
            cand_norm = to_pascal_case(candidate)
            if cand_norm and _exists_agent(cand_norm):
                return (cand_norm, rest, True)
            # Unknown agent → treat as no explicit agent, pass full text
            return (default_base, t, True)
        else:
            parts = t.split(maxsplit=1)
            candidate = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            cand_norm = to_pascal_case(candidate)
            if cand_norm and _exists_agent(cand_norm):
                return (cand_norm, rest, True)
            return (default_base, t, True)

    # Group/supergroup: act only on explicit '@Name ...'
    if not t or not t.startswith("@"):
        return ("", t, False)
    parts = t.split(maxsplit=1)
    candidate = parts[0][1:]
    rest = parts[1] if len(parts) > 1 else ""
    if not candidate:
        return (default_base, rest, True)
    cand_norm = to_pascal_case(candidate)
    if cand_norm and _exists_agent(cand_norm):
        return (cand_norm, rest, True)
    # Do not handle unknown names in group to reduce noise
    return ("", t, False)


def _parse_call_text(text: str) -> tuple[str, str]:
    """
    Parse forms like:
    - /call @Name some input
    - call @Name some input
    Returns (name_without_at, input_text)
    Raises ValueError if not parsable.
    """
    t = text.strip()
    if t.startswith("/call"):
        t = _extract_after("/call", t)
    elif t.lower().startswith("call"):
        t = _extract_after("call", t)
    # Expect @Name at the start
    if not t or not t.lstrip().startswith("@"):
        raise ValueError("Usage: /call @Name <input>")
    t = t.lstrip()
    # Split first token as @Name
    parts = t.split(maxsplit=1)
    name_tok = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    name = name_tok[1:]  # strip '@'
    return name, rest


# Handlers

@_require_allowed_users
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.debug("handle_start: chat_id=%s user_id=%s", getattr(update.effective_chat, 'id', None), getattr(update.effective_user, 'id', None))
    m = Messenger(context=context, update=update)
    await m.reply(
        """
call-bot

Commands:
- /call [--echo] @Name <input>
- /call [--echo] Name <input>  (equivalent to @Name)
- /list [--aliases] [--q "filter"]

Startup options:
- --bot-name Name  (token lookup: TELEGRAM_TOKEN.Name in env/.env; if --bot-name is not provided, falls back to TELEGRAM_TOKEN)

Plain text (no slash):
- In private chat: "@Name <input>" and "Name <input>" are equivalent.
- In groups: only explicit "@Name <input>" is handled to avoid reacting to every message.

Special cases:
- If this bot is AgentFabBot, default agent is AgentFab when no name is specified (e.g., "@ <input>").

Notes:
- /list prints one name per line as @Name.
- With --aliases, alias lines are indented with two spaces before @ (e.g., "  @Alias").
        """.strip(),
        parse_mode=None,
    )


@_require_allowed_users
async def handle_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.debug("handle_list: incoming text=%r", getattr(update.message, 'text', None))
    m = Messenger(context=context, update=update)

    # Parse flags from arguments (supports both /list and plain 'list ...')
    text = (update.message.text or "") if update.message else ""
    args = text.split()[1:] if text.startswith("/list") else text.split()[1:] if text.lower().startswith("list") else []
    include_aliases = False
    query = None
    # very light parser for --aliases and --q VALUE
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--aliases" or a == "--all":
            include_aliases = True
            i += 1
        elif a == "--q" and i + 1 < len(args):
            query = args[i + 1]
            i += 2
        else:
            i += 1

    # Run list via library API (no direct OpenAI calls here)
    try:
        log.debug("handle_list: query=%r include_aliases=%s", query, include_aliases)
        flat = call_api.list(query=query, include_aliases=include_aliases, grouped=False)
        if not isinstance(flat, list) or not flat:
            await m.reply("No agents found")
            return
        lines: list[str] = []
        seen: set[str] = set()
        for it in flat[:400]:
            name = (it.get("name") or "").strip()
            if name and name not in seen:
                lines.append(f"@{name}")
                seen.add(name)
            if include_aliases:
                for al in (it.get("aliases") or []):
                    al = (al or "").strip()
                    if al and al not in seen:
                        lines.append(f"  @{al}")
                        seen.add(al)
        await m.reply("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await m.reply(f"Error: {type(e).__name__}: {str(e)}")


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
        # Delegate to lib; it will publish to Telegram via its own utilities
        res = await call_api.call_async(
            name=name,
            input_text=input_text,
            echo=echo,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        # Optionally echo a short confirmation
        agent = res.get("agent")
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
    except ValueError as ve:
        await m.reply(str(ve), parse_mode=None)
        return

    base = _get_bot_base(update)
    if base == "AgentFab":
        if name != "AgentFab":
            log.info("handle_call: overriding requested agent %s -> AgentFab due to bot base=%r", name, base)
        name = "AgentFab"

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


@_require_allowed_users
async def handle_plain_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip() if update.message else ""
    log.debug("handle_plain_text: text=%r", text)
    if not text:
        return
    base = _get_bot_base(update)
    chat_type = getattr(getattr(update, "effective_chat", None), "type", "") or ""
    is_private = (chat_type == "private")
    name, inp, should_handle = _resolve_agent_and_input(text, base, is_private=is_private)
    if not should_handle:
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


def main() -> None:
    # CLI flags
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--echo", action="store_true", help="Print startup parameters and exit 0")
    parser.add_argument("--bot-name", dest="bot_name", default="", help="Bot name handle to select TELEGRAM_TOKEN.Name from env/.env (overrides positional)")
    # Positional bot name: `bot.py StratoSpaceAiBot` equivalent to `--bot-name StratoSpaceAiBot`
    parser.add_argument("bot_name_pos", nargs="?", default="", help="Positional bot name; equivalent to --bot-name")
    args, _ = parser.parse_known_args()

    if args.echo:
        cfg = _current_config_dict()
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return

    # Resolve token based on optional bot name
    global SELECTED_BOT_NAME
    # Prefer flag over positional if both provided
    SELECTED_BOT_NAME = (args.bot_name or args.bot_name_pos or "").strip()

    selected_token = TELEGRAM_TOKEN
    if SELECTED_BOT_NAME:
        key = f"TELEGRAM_TOKEN.{SELECTED_BOT_NAME}"
        cand = os.environ.get(key, "").strip()
        if cand:
            selected_token = cand
        else:
            print(
                (
                    "Error: bot token not set in environment/.env. "
                    f"Expected {key}."
                ),
                file=sys.stderr,
            )
            sys.exit(1)

    if not selected_token:
        raise RuntimeError("TELEGRAM_TOKEN is not set")

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

    app = (
        ApplicationBuilder()
        .token(selected_token)
        .request(request)
        .get_updates_request(request)
        .build()
    )

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("help", handle_start))
    app.add_handler(CommandHandler("list", handle_list))
    app.add_handler(CommandHandler("call", handle_call))

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_plain_text))
    # Log all incoming updates at the end so it doesn't interfere with other handlers
    app.add_handler(TypeHandler(Update, _log_update), group=100)
    # Global error handler
    app.add_error_handler(_error_handler)

    # Run the application (blocking)
    log.info("Starting polling...")
    try:
        app.run_polling(drop_pending_updates=_DROP_PENDING_UPDATES)
    except Exception:
        log.exception("run_polling terminated with error")
        raise


if __name__ == "__main__":
    main()
