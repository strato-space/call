import os
import sys
import asyncio
import pytest
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv


def _find_repo_root(start: Path) -> Path:
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return start.parent


# Ensure src root is on sys.path so 'call' package is importable
_repo_root = _find_repo_root(Path(__file__).resolve())
_src_root = _repo_root / "src"
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))

if "CALL_REPO_ROOT" not in os.environ:
    os.environ["CALL_REPO_ROOT"] = str(_repo_root)
if "CALL_WORKSPACE_ROOT" not in os.environ:
    os.environ["CALL_WORKSPACE_ROOT"] = str(_repo_root.parent)

from telegram import Bot
from telegram.constants import ParseMode
from telegram.request import HTTPXRequest

from call.app.utils.telegram_text import (
    telegram_prepare_markdown,
    telegram_prepare_html,
)

# --- Live-send guards ---------------------------------------------------------
# Set TELEGRAM_LIVE=1 to enable these integration tests.
_LIVE = os.getenv("TELEGRAM_LIVE") == "1"
_LIVE_KIND = (
    (os.getenv("TELEGRAM_LIVE_KIND") or "").strip().lower()
)  # 'md' or 'html' optional

pytestmark = [
    pytest.mark.skipif(
        not _LIVE,
        reason="Set TELEGRAM_LIVE=1 to run live Telegram send tests",
    )
]

# --- Load .env from repo to populate TELEGRAM_* if not already set ---
def _load_env_from_dotenv() -> None:
    """Best-effort .env loader for integration tests.

    Attempts to load key=value pairs from repo .env into os.environ if not set.
    This avoids adding a python-dotenv dependency just for tests.
    """
    try:
        dotenv = _repo_root / ".env"
        if not dotenv.exists():
            return
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" not in s:
                continue
            k, v = s.split("=", 1)
            key = k.strip()
            val = v.strip().strip('"')
            if key and key not in os.environ:
                os.environ[key] = val
        # Normalize common key variants after loading
        # Prefer TELEGRAM_BOT_TOKEN; if missing, map from TELEGRAM_TOKEN
        if not os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_TOKEN"):
            os.environ["TELEGRAM_BOT_TOKEN"] = os.environ["TELEGRAM_TOKEN"]
        # Normalize chat id fallbacks
        if not os.getenv("TELEGRAM_DEBUG_CHAT_ID"):
            for alt in ("TELEGRAM_CHAT", "TG_CHAT_ID", "CHAT_ID"):
                if os.getenv(alt):
                    os.environ["TELEGRAM_DEBUG_CHAT_ID"] = os.environ[alt]
                    break
    except Exception:
        # best-effort; ignore errors
        pass


_load_env_from_dotenv()

# Map TELEGRAM_TOKEN -> TELEGRAM_BOT_TOKEN if only the former exists
if not os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_TOKEN"):
    os.environ["TELEGRAM_BOT_TOKEN"] = os.environ["TELEGRAM_TOKEN"]

# Integration tests that actually send messages to Telegram.
# These tests are skipped unless the following environment variables are set:
#   TELEGRAM_BOT_TOKEN   - bot token
#   TELEGRAM_DEBUG_CHAT_ID     - chat id to send messages to (int or str)
# Optional:
#   TELEGRAM_DEBUG_THREAD_ID   - topic thread id in supergroup (int)
#
# Run with: pytest -q call/app/tests/test_telegram_send.py -k send --maxfail=1
# Ensure your venv is used: .venv\Scripts\python.exe -m pytest ...


def _env_token_chat_thread() -> tuple[str, str, Optional[int]]:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_DEBUG_CHAT_ID")
    thread = os.getenv("TELEGRAM_DEBUG_THREAD_ID")
    thread_id = int(thread) if thread and thread.strip() else None
    return token or "", chat_id or "", thread_id


# Load .env before evaluating skip markers
_env_candidates = [
    _repo_root / ".env",
    _repo_root.parent / ".env",
]
for _cand in _env_candidates:
    if _cand.exists():
        load_dotenv(dotenv_path=str(_cand), override=False)
        break

pytestmark = [
    *pytestmark,  # keep live guard
    pytest.mark.skipif(
        _LIVE_KIND == "skip",
        reason="TELEGRAM_LIVE_KIND=skip disables Telegram integration tests",
    ),
    pytest.mark.skipif(
        not os.getenv("TELEGRAM_BOT_TOKEN") or not os.getenv("TELEGRAM_DEBUG_CHAT_ID"),
        reason="Integration test requires TELEGRAM_BOT_TOKEN and TELEGRAM_DEBUG_CHAT_ID in .env or environment",
    ),
]


def test_send_markdown_v2_message() -> None:
    if _LIVE_KIND and _LIVE_KIND != "md":
        pytest.skip("Skipping Markdown test due to TELEGRAM_LIVE_KIND filter")
    token, chat_id, thread_id = _env_token_chat_thread()

    # Ensure proxies do not interfere with Telegram connectivity in test envs
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "PROXY", "PROXY_URL"):
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "api.telegram.org,*.telegram.org")

    # Prepare a MarkdownV2 sample (escaped automatically by helper)
    md_text = (
        "In City, ...\n\n"
        "**[MegaFon](https://www.google.com/maps/search/MegaFon,+City)**\n"
        "_City, Russia_\n"
        "Address: 1 Sample Street\n"
    )
    safe_text, mode = telegram_prepare_markdown(md_text, 4000, version="v2")
    assert mode == "MarkdownV2"

    request = HTTPXRequest(connect_timeout=20.0, read_timeout=120.0, pool_timeout=5.0)
    bot = Bot(token=token, request=request)

    async def _run():
        try:
            msg = await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=safe_text,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            # If thread id is invalid/missing on target chat, retry without topic
            from telegram.error import BadRequest

            if isinstance(e, BadRequest) and "thread not found" in str(e).lower():
                msg = await bot.send_message(
                    chat_id=chat_id,
                    text=safe_text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                raise
        assert msg is not None
        assert msg.message_id > 0

    asyncio.run(_run())


def test_send_html_message() -> None:
    if _LIVE_KIND and _LIVE_KIND != "html":
        pytest.skip("Skipping HTML test due to TELEGRAM_LIVE_KIND filter")
    token, chat_id, thread_id = _env_token_chat_thread()

    # Ensure proxies do not interfere with Telegram connectivity in test envs
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "PROXY", "PROXY_URL"):
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "api.telegram.org,*.telegram.org")

    # Prepare an HTML sample (sanitized + truncated by helper)
    html_text = (
        "<b>SelfReflection</b>\n"
        "Check HTML mode <i>italic</i> and <b>bold</b> and the link "
        '<a href="https://example.com?q=1&x=2">Example</a>.'
    )
    safe_text, mode = telegram_prepare_html(html_text, 4000)
    assert mode == "HTML"

    request = HTTPXRequest(connect_timeout=20.0, read_timeout=120.0, pool_timeout=5.0)
    bot = Bot(token=token, request=request)

    async def _run():
        try:
            msg = await bot.send_message(
                chat_id=chat_id,
                message_thread_id=thread_id,
                text=safe_text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            from telegram.error import BadRequest

            if isinstance(e, BadRequest) and "thread not found" in str(e).lower():
                msg = await bot.send_message(
                    chat_id=chat_id,
                    text=safe_text,
                    parse_mode=ParseMode.HTML,
                )
            else:
                raise
        assert msg is not None
        assert msg.message_id > 0

    asyncio.run(_run())
