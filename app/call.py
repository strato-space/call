import os
import asyncio
import logging
from typing import Optional, Dict, Any, List, Callable, Awaitable, Type, Union
import base64
from contextlib import asynccontextmanager, ExitStack
from dataclasses import dataclass, field
import urllib.parse
from pathlib import Path
import json
import yaml
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

# Import HTML/Telegram/Telegraph utilities from utils with script fallback
try:
    from .utils.html_sanitizer import clean_html_for_telegram, clean_html_for_telegraph, minify_html_func
    from .utils.telegram_text import (
        telegram_truncate_html_safe,
        telegram_truncate_markdown_safe,
        telegram_prepare_html,
        telegram_prepare_markdown,
    )
    from .utils.telegraph_utils import publish_results, create_telegrath_account
except ImportError:
    # Fallback for when running as a plain script
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'utils'))
    from html_sanitizer import clean_html_for_telegram, clean_html_for_telegraph, minify_html_func
    from telegram_text import (
        telegram_truncate_html_safe,
        telegram_truncate_markdown_safe,
        telegram_prepare_html,
        telegram_prepare_markdown,
    )
    from telegraph_utils import publish_results, create_telegrath_account


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

from agents import Agent, Runner, WebSearchTool
from agents.tool import FileSearchTool, FunctionTool
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

load_dotenv(dotenv_path=str(_env_file), override=True)

# Module-level debug flag (can be overridden by main(debug=True))
DEBUG = str(os.getenv("DEBUG", "")).lower() in ("1", "true", "yes", "on")

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

# Global variable to store the last message object
telegram_last_message: Optional[Message] = None
selected_chat_id: Optional[int] = None
selected_thread_id: Optional[int] = None

def get_telegram_chat_id(env_var: str, default: str = None) -> int:
    """Safely get and convert Telegram chat ID from environment."""
    try:
        chat_id = ensure_env(env_var, default)
        # Remove any non-numeric characters except optional leading minus
        if chat_id:
            chat_id = ''.join(c for c in chat_id if c.isdigit() or c == '-')
            if chat_id and chat_id != '-':  # Check if we have a valid number
                return int(chat_id)
        if default is not None:
            return int(default)
        raise ValueError(f"Invalid chat ID in {env_var}")
    except (ValueError, TypeError) as e:
        raise ValueError(f"Failed to parse Telegram chat ID from {env_var}: {e}")

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
# Global OpenAI client - will be configured with proxy in create_openai_client()
openai_client: OpenAI = None

def create_openai_client():
    """Create and configure OpenAI client with proper proxy configuration for agents SDK."""
    from openai import AsyncOpenAI
    import agents
    
    # Get proxy configuration from environment
    proxy_url = os.environ.get("OPENAI_PROXY_URL") or os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
    
    if proxy_url:
        # For agents SDK, we need to configure the underlying httpx client properly
        import httpx
        
        # Create httpx client with proxy and generous timeouts
        # Separate connect/read/write timeouts to better handle slow model responses
        http_client = httpx.AsyncClient(
            proxy=proxy_url,
            timeout=httpx.Timeout(connect=45.0, read=600.0, write=240.0, pool=60.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            verify=True,
            follow_redirects=True,
        )
        
        # Create AsyncOpenAI client with proxied httpx client and small retry budget
        client = AsyncOpenAI(
            api_key=OPENAI_API_KEY,
            http_client=http_client,
            max_retries=2,
            timeout=600.0,
        )
    else:
        # Direct connection
        client = AsyncOpenAI(api_key=OPENAI_API_KEY, max_retries=2, timeout=600.0)
    
    # Set as default client for agents SDK
    agents.set_default_openai_client(client)
    return client

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

async def init_bot():
    global bot
    # Configure PTB to use HTTPX with tuned timeouts and connection pool
    # Bypass system proxies for Telegram and disable trust_env to reduce connection issues
    import os as _os
    _os.environ.setdefault("NO_PROXY", "api.telegram.org,*.telegram.org")
    _os.environ.setdefault("no_proxy", "api.telegram.org,*.telegram.org")
    request = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=120.0,
        write_timeout=60.0,
    )
    bot = Bot(token=telegram_token, request=request)
    return bot

async def init_openai_client():
    """Initialize the global OpenAI client with proper proxy configuration."""
    global openai_client
    if openai_client is None:
        openai_client = create_openai_client()
    return openai_client

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
        print(f"Message sent. ID: {message.message_id}, Chat ID: {message.chat_id}, Thread ID: {message.message_thread_id}")
        return message
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        raise e


async def send_digest_notification(
    url: str | None = None,
    *,
    text: str = None,
    message_thread_id: int = None,
    agent_name: str | None = None,
    agent_path: str | Path | None = None,
    input_text: str | None = None,
    image_path: str | Path | None = None,
) -> Optional[Message]:
    # If content is too long for Telegram, publish and use resulting URL
    try:
        if (text is not None) and isinstance(text, str) and len(text) >= 4000:
            pub_title = (agent_name or "Agent")
            url = await publish_results(title=pub_title, content=text)
            text = None  # switch to link mode
    except Exception:
        # On failure to publish, fall back to sending as-is (may get truncated by Telegram)
        pass

    # Prepare final text
    if text is None:
        text = f"📰 {url}" if url else "📰"
        if input_text:
            try:
                safe_input = (input_text or "")[:3800]
                text = text + f"\n<code>{safe_input}</code>"
            except Exception:
                pass

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
            agent_name_norm = to_pascal_case(agent_name or "") if agent_name else None
            if agent_name_norm:
                try:
                    resolved_yaml = discover_agent_yaml(agent_name_norm)
                except Exception:
                    resolved_yaml = None
        if resolved_yaml:
            agent_cfg = load_yaml(resolved_yaml) or {}
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
                        safe_url = url or ""
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
        if image_path:
            message_obj = await telegram_send_photo(
                image_path=image_path,
                caption=text,
                message_thread_id=message_thread_id,
                reply_markup=reply_markup,
            )
        else:
            # KISS: rely on globally selected_* targets updated once in main()
            message_obj = await telegram_send_message(
                text=text,
                reply_markup=reply_markup,
                message_thread_id=message_thread_id)

        print(f"Digest notification sent. ID: {message_obj.message_id}, Chat ID: {message_obj.chat_id}")
        return message_obj
    except Exception as e:
        print(f"Error sending Telegram message/photo: {e}")
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

    # Choose parse mode, then prepare safely using centralized helpers
    is_md = _looks_like_markdown(text or "")
    try:
        if is_md:
            safe_text, chosen_mode = telegram_prepare_markdown(text or "", 4000, version="v2")
            chosen_parse_mode = ParseMode.MARKDOWN_V2 if chosen_mode == "MarkdownV2" else ParseMode.MARKDOWN
        else:
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

    async def _op():
        return await bot.send_message(
            chat_id=eff_chat_id,
            message_thread_id=eff_thread_id,
            text=safe_text,
            parse_mode=chosen_parse_mode,
            reply_markup=reply_markup
        )
    try:
        if DEBUG:
            print(f"[TG] send_message parse_mode={chosen_parse_mode}")
        message = await async_retry(_op, retries=2, base_delay=1.0, jitter=0.2, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
    except BadRequest as e:
        # Fallback to plain text if Telegram can't parse entities
        emsg = str(e).lower()
        if "can't parse entities" in emsg or "parse entities" in emsg or "entity" in emsg:
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
            if DEBUG:
                print("[TG] BadRequest parse error, retrying as plain text")
            message = await async_retry(_op_plain, retries=1, base_delay=0.7, jitter=0.1, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
        else:
            raise
    return message


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
        # Prepare caption via centralized helpers
        try:
            cap_is_md = "```" in caption or any(m in (caption or "") for m in ("**", "__", "# ", "1. "))
            if cap_is_md:
                safe_caption, cmode = telegram_prepare_markdown(caption or "", 1024, version="v2")
                parse_mode = ParseMode.MARKDOWN_V2 if cmode == "MarkdownV2" else ParseMode.MARKDOWN
            else:
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

    async def _op():
        with open(image_path, 'rb') as f:
            return await bot.send_photo(
                chat_id=eff_chat_id,
                message_thread_id=eff_thread_id,
                photo=f,
                caption=(safe_caption or None),
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )

    message = await async_retry(_op, retries=2, base_delay=1.0, jitter=0.2, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
    return message


logging.getLogger("openai").setLevel(logging.DEBUG)

default_samples_dir = str(Path(__file__).resolve().parents[2])

# --- Shared helpers (Responses-only mode) ---
def _is_url(s: str) -> bool:
    try:
        return isinstance(s, str) and s.strip().lower().startswith(("http://", "https://"))
    except Exception:
        return False

def _as_input_image(src: Union[str, Path, bytes], stack: ExitStack):
    """
    Build a Responses `input_image` content part:
      - URL -> {"type":"input_image","image_url": "..."}
      - Local path/bytes -> {"type":"input_image","image_data": "<b64>", "mime_type":"image/png"}
    """
    if src is None:
        return None
    if isinstance(src, (str, Path)):
        p = str(src)
        if _is_url(p):
            return {"type": "input_image", "image_url": p}
        data = Path(p).read_bytes()
        return {"type": "input_image", "image_data": base64.b64encode(data).decode("ascii"), "mime_type": "image/png"}
    if isinstance(src, (bytes, bytearray)):
        return {"type": "input_image", "image_data": base64.b64encode(bytes(src)).decode("ascii"), "mime_type": "image/png"}
    return None

async def _responses_image_one_out(
    *,
    prompt_text: str,
    output_path: Path,
    base_image: Optional[Union[str, Path, bytes]] = None,
    ref_images: Optional[List[Union[str, Path, bytes]]] = None,
    mask: Optional[Union[str, Path, bytes]] = None,
    size: str = "1024x1024",
    model: str = "gpt-4o",
):
    """
    Pure Responses API call using the built-in `image_generation` tool.
    Saves exactly one image to output_path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        content: List[Dict[str, Any]] = [{"type": "input_text", "text": prompt_text + f"\n\n[SIZE={size}]"}]
        if base_image:
            part = _as_input_image(base_image, stack)
            if part:
                content.append(part)
        for r in (ref_images or []):
            p = _as_input_image(r, stack)
            if p:
                content.append(p)
        if mask:
            m = _as_input_image(mask, stack)
            if m:
                content.append({"type": "input_text", "text": "[MASK BELOW – transparent = editable]"})
                content.append(m)

        system = (
            "You are a precise image editor.\n"
            "- Use the `image_generation` tool exactly once.\n"
            "- If an input image is included, treat it as the BASE; others are STYLE/POSE refs.\n"
            "- Produce exactly ONE final image at the requested size.\n"
            "- Return only the image (no prose).\n"
        )
        tools: List[Dict[str, Any]] = [{"type": "image_generation"}]
        mcp_fs_url = os.getenv("MCP_FS_URL")
        if mcp_fs_url:
            tools.append({
                "type": "mcp",
                "server_url": mcp_fs_url,
                "server_label": "fs",
                "allowed_tools": ["read_resource", "list_directory"],
                "require_approval": "never",
            })

        async def _call():
            return await openai_client.responses.create(
                model=model,
                instructions=system,
                tools=tools,
                input=[{"role": "user", "content": content}],
            )

        resp = await async_retry(_call, retries=2, base_delay=1.0, jitter=0.2, retry_on=(httpx.TimeoutException, OSError))

        # Extract a single image from Responses output
        def _extract_image_b64(r):
            for msg in getattr(r, "output", []) or []:
                for part in getattr(msg, "content", []) or []:
                    t = getattr(part, "type", None) or getattr(part, "kind", None)
                    if t in ("image", "output_image"):
                        b64 = getattr(part, "image_base64", None) or getattr(part, "b64_json", None)
                        url = getattr(part, "image_url", None) or getattr(part, "url", None)
                        return (b64, url)
            return (None, None)

        b64, url = _extract_image_b64(resp)
        if not b64 and url:
            async with httpx.AsyncClient(follow_redirects=True, timeout=300.0) as client:
                rr = await client.get(url)
                rr.raise_for_status()
                output_path.write_bytes(rr.content)
                return output_path
        if not b64:
            raise RuntimeError("Responses image not found (no b64/url).")
        output_path.write_bytes(base64.b64decode(b64))
        return output_path

def _parse_image_job_from_llm(text: str) -> Optional[dict]:
    """
    Expected JSON edit job if the agent doesn't call a tool:
      {"prompt": "...", "base_image": "<url-or-path>", "ref_images": ["..."], "size": "1024x1024", "mask": "<optional>"}
    """
    try:
        if not text or not isinstance(text, str):
            return None
        s = text.strip()
        # Try to find a JSON object in the text
        start = s.find('{')
        end = s.rfind('}')
        if start == -1 or end == -1 or end <= start:
            return None
        obj = json.loads(s[start:end + 1])
        if not isinstance(obj, dict):
            return None
        if "prompt" in obj and ("base_image" in obj or "ref_images" in obj):
            # normalize fields
            if obj.get("ref_images") is None:
                obj["ref_images"] = []
            return obj
        return None
    except Exception:
        return None

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
        cleaned = clean_html_for_telegram(safe_text)
        if len(cleaned) > 3800:
            cleaned = cleaned[:3797] + "..."
        msg = await telegram_send_message(
            chat_id=selected_chat_id,
            message_thread_id=selected_thread_id,
            text=cleaned,
            reply_markup=None,
        )
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
        print(f"[MCP Hook] Calling tool: {tool_name}")
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
        print("[MCP Hook] Arguments (YAML):\n" + yaml_args)

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
                    await async_retry(_op, retries=1, base_delay=0.5, jitter=0.1, retry_on=(TimedOut, NetworkError, httpx.TimeoutException))
            except Exception:
                pass

            try:
                async def _call():
                    return await parent_call_tool(tool_name, arguments)
                result = await async_retry(_call, retries=1, base_delay=1.0, jitter=0.2, retry_on=(httpx.TimeoutException, OSError))
                print(f"[MCP Hook] Tool {tool_name} completed successfully")
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
def to_pascal_case(name: str) -> str:
    """Normalize agent name to PascalCase as per process-agents.md (case-insensitive input)."""
    if not name:
        return ""
    # strip leading '@' and split by non-alnum and separators like ':' and '/'
    raw = name.strip().lstrip('@')
    # only take the AgentName part before ':' if provided
    raw = raw.split(':', 1)[0]
    parts = []
    token = ''
    for ch in raw:
        if ch.isalnum():
            token += ch
        else:
            if token:
                parts.append(token)
                token = ''
    if token:
        parts.append(token)
    # Preserve existing internal capitalization within tokens.
    # Only uppercase the first character of each token; do not lowercase the remainder.
    def _cap_preserve(t: str) -> str:
        if not t:
            return ''
        return t[:1].upper() + t[1:]
    return ''.join(_cap_preserve(p) for p in parts)


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
                name_pc = to_pascal_case(str(name))
                # resolve to actual directory casing if present
                agent_dir = _resolve_dir_case(base_dir, name_pc)
                path = (agent_dir / 'agent.yaml')
                if path.exists():
                    mapping[name_pc] = path
                # bind aliases
                if isinstance(aliases_map, dict):
                    for alias in (aliases_map.get(name) or aliases_map.get(name_pc) or []):
                        alias_pc = to_pascal_case(str(alias))
                        if alias_pc and path.exists():
                            mapping[alias_pc] = path
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
                name = to_pascal_case(str(y.get('id') or y.get('name') or child.name))
                aliases = []
                raw_aliases = y.get('aliases') or []
                if isinstance(raw_aliases, list):
                    aliases = [to_pascal_case(str(a)) for a in raw_aliases if str(a).strip()]
                result[name] = (ay, aliases)
            except Exception:
                result[child.name] = (ay, [])
    return result


def _ensure_indices(rep: Path) -> None:
    """Create minimal indices agents.yaml in AgentFab/ and agents/ if missing.

    Structure:
      name: <string>
      agents: { AgentName: <short description or empty> }
      aliases: { AgentName: [Alias1, Alias2] }
    """
    import yaml
    for sub in ('AgentFab', 'agents'):
        base = rep / sub
        index = base / 'agents.yaml'
        if index.exists():
            continue
        scanned = _scan_agents_dir(base)
        agents_map = {name: '' for name in scanned.keys()}
        aliases_map = {name: aliases for name, (_, aliases) in scanned.items() if aliases}
        if not agents_map and sub == 'AgentFab':
            # Try to derive from AgentFab root agent.yaml 'agents' section
            af = rep / 'AgentFab' / 'agent.yaml'
            if af.exists():
                try:
                    data = load_yaml(af) or {}
                    section = data.get('agents') or {}
                    if isinstance(section, dict):
                        for group_val in section.values():
                            if isinstance(group_val, dict):
                                for nm, desc in group_val.items():
                                    agents_map[to_pascal_case(str(nm))] = str(desc or '')
                except Exception:
                    pass
        content = {
            'name': f'{sub} Agents Index',
            'agents': agents_map,
        }
        if aliases_map:
            content['aliases'] = aliases_map
        try:
            with open(index, 'w', encoding='utf-8') as f:
                yaml.safe_dump(content, f, allow_unicode=True, sort_keys=False)
        except Exception:
            # best-effort; ignore failures
            pass


def discover_agent_yaml(agent_name: str) -> Path | None:
    """Discover agent YAML with index-first strategy and fallbacks.

    Priority:
    0) Special-case AgentFab -> prompt/AgentFab/agent.yaml
    1) Index lookup in AgentFab/agents.yaml (by name or alias)
    2) Index lookup in agents/agents.yaml (by name or alias)
    3) Directory scan in AgentFab/<AgentName>/agent.yaml
    4) Directory scan in agents/<AgentName>/agent.yaml
    """
    if not agent_name:
        return None
    repo = discover_prompt_repo()
    query_raw = str(agent_name).strip().lstrip('@')
    query_norm = to_pascal_case(query_raw)

    # 0) Special-case: AgentFab root card
    if query_norm.lower() == 'agentfab':
        root_yaml = repo / 'AgentFab' / 'agent.yaml'
        return root_yaml if root_yaml.exists() else None

    # Ensure indices exist (best-effort)
    _ensure_indices(repo)

    # 1) Index lookup AgentFab
    af_index_map = _load_agents_index(repo / 'AgentFab' / 'agents.yaml', repo / 'AgentFab')
    if query_norm in af_index_map:
        return af_index_map[query_norm]

    # 2) Index lookup agents
    agents_index_map = _load_agents_index(repo / 'agents' / 'agents.yaml', repo / 'agents')
    if query_norm in agents_index_map:
        return agents_index_map[query_norm]

    # 3–4) Fallback directory scan with case-insensitive match
    def find_in_dir(base: Path) -> Path | None:
        if not base.exists():
            return None
        # Try exact
        direct = base / query_norm / 'agent.yaml'
        if direct.exists():
            return direct
        # Case-insensitive directory match
        for child in base.iterdir():
            if child.is_dir() and child.name.lower() == query_norm.lower():
                cand = child / 'agent.yaml'
                if cand.exists():
                    return cand
        return None

    agentfab_path = find_in_dir(repo / 'AgentFab')
    if agentfab_path:
        return agentfab_path
    return find_in_dir(repo / 'agents')


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


def make_responses_image_generation_tool() -> FunctionTool:
    """
    Factory: returns a FunctionTool that calls Responses API `image_generation`
    to produce exactly one image.
    """
    async def on_invoke_tool(params: Dict[str, Any]) -> Dict[str, Any]:
        prompt: str = params.get("prompt", "")
        images: List[str] = params.get("images", []) or []
        size: str = params.get("size", "1024x1024")
        mask: Optional[str] = params.get("mask")
        output_path: Optional[str] = params.get("output_path")

        base = images[0] if images else None
        refs = images[1:] if len(images) > 1 else []

        if output_path:
            out = Path(output_path)
            await _responses_image_one_out(
                prompt_text=prompt, base_image=base, ref_images=refs, mask=mask, size=size, output_path=out
            )
            b64 = base64.b64encode(out.read_bytes()).decode("ascii")
            return {"b64_png": b64, "saved_path": str(out), "size": size}
        else:
            with ExitStack() as stack:
                tmp = stack.enter_context(tempfile.NamedTemporaryFile(suffix=".png", delete=False))
                tmp_path = Path(tmp.name)
                stack.callback(lambda: tmp_path.exists() and tmp_path.unlink(missing_ok=True))
                await _responses_image_one_out(
                    prompt_text=prompt, base_image=base, ref_images=refs, mask=mask, size=size, output_path=tmp_path
                )
                b64 = base64.b64encode(tmp_path.read_bytes()).decode("ascii")
                return {"b64_png": b64, "saved_path": None, "size": size}

    params_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Text prompt for image generation/editing"},
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of image paths/URLs. images[0]=base image; images[1..]=reference images"
            },
            "size": {"type": "string", "default": "1024x1024"},
            "mask": {"type": ["string", "null"], "description": "Optional mask path/URL"},
            "output_path": {"type": ["string", "null"], "description": "Where to save the result PNG"}
        },
        "required": ["prompt", "images"],
        "additionalProperties": False
    }

    return FunctionTool(
        name="image_generation_one_out",
        description="Render a single image using Responses API image_generation. Base + refs supported.",
        params_json_schema=params_schema,
        on_invoke_tool=on_invoke_tool,
    )


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


@dataclass
class AgentConfig:
    name: str
    instructions: str
    model: str
    model_settings: ModelSettings | None = None
    vs_list: List[str] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)
    agent_yaml_path: Path | None = None
    base_dir: Path | None = None


def _normalize_vs_list(vs_val: Any) -> List[str]:
    if vs_val is None:
        return []
    if isinstance(vs_val, (list, tuple, set)):
        return [str(x) for x in vs_val if x is not None]
    return [str(vs_val)]


async def build_agent_config(agent_name: str | None = None) -> AgentConfig:
    """Build AgentConfig by discovering/loading YAML via normalized agent name."""
    norm = to_pascal_case(agent_name or "") if agent_name else ""
    path_obj: Path | None = discover_agent_yaml(norm) if norm else None

    dto: AgentDTO | None = None
    if path_obj and Path(path_obj).exists():
        dto = AgentDTO(load_yaml(path_obj), base_dir=Path(path_obj).parent)

    # Defaults
    default_model = os.environ.get("LLM_MODEL", "gpt-4.5")
    name = (dto.name if dto and dto.name else (agent_name or "Agent")).strip()
    model = (dto.model if dto and dto.model else default_model)
    model_settings = (dto.model_settings if dto else None)
    attributes: Dict[str, Any] = dict(getattr(dto, 'attributes', {}) or {})
    instructions = ""
    if dto:
        try:
            instr, attrs = await dto.getInstructions()
            instructions = instr or ""
            if isinstance(attrs, dict):
                # Merge, but strip aliases from runtime attrs
                attrs = {k: v for k, v in attrs.items() if k not in {"alias", "aliases"}}
                attributes |= attrs
        except Exception:
            instructions = instructions or ""

    # Vector stores from attributes (agent or prompt)
    vs_list = _normalize_vs_list(attributes.get('vs'))

    # Sanitize numeric token fields in model_settings (if provided)
    try:
        import re as _re
        if model_settings:
            if hasattr(model_settings, 'max_tokens'):
                v = getattr(model_settings, 'max_tokens')
                m = _re.search(r"\d+", str(v)) if isinstance(v, str) else None
                setattr(model_settings, 'max_tokens', (int(m.group(0)) if m else v) if not isinstance(v, int) else v)
            if hasattr(model_settings, 'max_output_tokens'):
                v = getattr(model_settings, 'max_output_tokens')
                m = _re.search(r"\d+", str(v)) if isinstance(v, str) else None
                setattr(model_settings, 'max_output_tokens', (int(m.group(0)) if m else v) if not isinstance(v, int) else v)
    except Exception:
        pass

    return AgentConfig(
        name=name,
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        vs_list=vs_list,
        attributes=attributes,
        agent_yaml_path=path_obj,
        base_dir=(path_obj.parent if path_obj else None),
    )


@asynccontextmanager
async def build_agent_by_name(agent_name: str, samples_dir: str):
    """Async context manager that creates MCP servers and builds an Agent by name.

    Usage:
        async with build_agent_by_name(name, samples_dir) as (agent, cfg):
            ...
    """
    server_gsheets = None
    async with MCPServerStdioHook(
            params={
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", samples_dir]
            },
            name="fs",
            client_session_timeout_seconds=60
    ) as server_fs, MCPServerStdioHook(
            params={"command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]},
            name="seq",
            client_session_timeout_seconds=180
    ) as server_seq:
        print("[MCP] Voice server disabled (package '@modelcontextprotocol/server-realtime-api-voice' not available)")
        # Optionally enable Google Sheets server (kept disabled by default)
        # async with MCPServerStdioHook(
        #     params={
        #         "command": "uvx",
        #         "args": ["--env-file", samples_dir + "/server/mcp/.env", "mcp-google-sheets@latest"]
        #     },
        #     client_session_timeout_seconds=60
        # ) as server_gsheets:

        cfg = await build_agent_config(agent_name)
        tools = [WebSearchTool(), make_responses_image_generation_tool()]
        if cfg.vs_list:
            try:
                tools.append(FileSearchTool(vector_store_ids=cfg.vs_list))
            except Exception:
                pass
        agent = Agent(
            name=f"{cfg.name} [agent]",
            instructions=cfg.instructions,
            tools=tools,
            mcp_servers=[server_fs, server_seq] + ([server_gsheets] if server_gsheets else []),
            model=cfg.model,
            model_settings=(cfg.model_settings or ModelSettings()),
        )

        # Expose tools from servers (side-effect logging retained)
        run_context = RunContextWrapper(context=None)
        try:
            _ = await server_fs.list_tools(run_context, agent)
            _ = await server_seq.list_tools(run_context, agent)
            if server_gsheets:
                tools_gsheets = await server_gsheets.list_tools(run_context, agent)
                print("Google Sheets tools:", tools_gsheets)
        except Exception:
            pass

        try:
            yield agent, cfg
        finally:
            # Context managers will close servers automatically
            pass


async def run_digest_pipeline(samples_dir: str, agent_path: str = None, user_input: str = "", debug: bool = False, cli_agent_name: str = "", initial_history: List[Dict[str, Any]] | None = None):

    async with build_agent_by_name(cli_agent_name, samples_dir) as (agent, cfg):
        # Выполняем запрос: передаём user_input как элемент истории в формате 
        # {"role": "user", "content": user_input}
        # Seed history: optional initial history + current user_input
        history = list(initial_history) if initial_history else []
        history.append({"role": "user", "content": user_input or "go"})
        # Simple loop with max safety counter
        max_cycles = 100
        cycles = 0
        # Retry guards to prevent infinite loops on repeated MCP failures
        mcp_retry_main_done = False
        mcp_retry_sr_done = False
        # Ensure step1_output is always defined even if we break on first error
        step1_output = ""
        while True:
            cycles += 1
            if cycles > max_cycles:
                print("Max cycles reached; exiting loop")
                break
            try:
                result1 = await Runner.run(
                    agent,
                    history,
                    max_turns=150,
                )
                history = result1.to_input_list()
                step1_output = result1.final_output
            except Exception as e:
                # Log and continue by adding error and a 'go' to history
                err_text = format_exception_text(e)
                print("Error during main agent run:\n" + err_text)
                history.append({"role": "assistant", "content": f"Error: {err_text}"})
                is_mcp = "Error invoking MCP tool" in str(e)
                if is_mcp and not mcp_retry_main_done:
                    mcp_retry_main_done = True
                    history.append({"role": "user", "content": "go"})
                    continue
                # Do not auto-retry more than once; break the loop and return
                break

            print("Step 1 output:")
            print(step1_output)
            
            # Detect whether an image output is requested and generate it via Responses API
            img_path_for_notify: Path | None = None
            try:
                # Identify agent folder and output filename from files_contract.outputs
                resolved_yaml_path: Path | None = (cfg.agent_yaml_path if cfg.agent_yaml_path else (Path(agent_path) if agent_path else None))
                agent_yaml_data = load_yaml(resolved_yaml_path) if resolved_yaml_path and resolved_yaml_path.exists() else {}
                outputs_section = ((agent_yaml_data.get('files_contract') or {}).get('outputs') or [])
                out_name = None
                if isinstance(outputs_section, list) and outputs_section:
                    # Take the first file as the primary image output
                    out_name = str(outputs_section[0]).strip()
                # Heuristic: only attempt generation if output filename looks like an image
                should_generate = bool(out_name and out_name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')))
                if should_generate and step1_output:
                    img_path_for_notify = _resolve_output_file_path(resolved_yaml_path, out_name)
                    print(f"[Responses] Output file planned: {img_path_for_notify}")
                    try:
                        # Prefer JSON job (base + refs); else prompt-only generation fallback
                        job = _parse_image_job_from_llm(step1_output)
                        if job:
                            print("[Responses] Detected JSON image-edit job (base + refs).")
                            await _responses_image_one_out(
                                prompt_text=job["prompt"],
                                base_image=job.get("base_image"),
                                ref_images=job.get("ref_images") or [],
                                mask=job.get("mask"),
                                size=job.get("size") or "1024x1024",
                                output_path=img_path_for_notify,
                            )
                        else:
                            print("[Responses] No JSON job; using prompt-only generation.")
                            await _responses_image_one_out(
                                prompt_text=step1_output,
                                output_path=img_path_for_notify,
                            )
                    except Exception as e:
                        # Log and continue without image
                        print(f"[Responses] Error creating image: {e}")
                        img_path_for_notify = None
            except Exception as e:
                print(f"[Responses] Skipping image generation due to error: {e}")

            # Send Telegram digest notification only outside of debug mode
            if not debug:
                await send_digest_notification(
                    agent_name=cfg.name,
                    agent_path=(str(cfg.agent_yaml_path) if cfg.agent_yaml_path else agent_path),
                    input_text=user_input,
                    text=step1_output,
                    image_path=(str(img_path_for_notify) if img_path_for_notify and img_path_for_notify.exists() else None),
                )

            # Post-run: commit and push changes only outside of debug mode
            if not debug:
                await post_run_git_push(agent_name=cfg.name, user_input=user_input)

            # Only run SelfReflection when the original agent is AgentFab
            # Consider both the resolved config name and the CLI-invoked name (may include leading '@')
            invoked_name = (cli_agent_name or "").strip().lstrip("@").lower()
            cfg_name_norm = (cfg.name or "").strip().lstrip("@").lower() if isinstance(cfg.name, str) else ""
            is_agentfab = (cfg_name_norm == "agentfab") or (invoked_name == "agentfab")
            if not is_agentfab:
                # For non-AgentFab agents, finish after the first main run
                break

            # --- Run SelfReflection agent and react to its return code ---
            async with build_agent_by_name("SelfReflection", samples_dir) as (sr_agent, sr_cfg):
                print(f"[SR] Running SelfReflection (cycles={cycles})")
                try:
                    sr_result = await Runner.run(
                        sr_agent,
                        history,
                        max_turns=100,
                    )
                except Exception as e:
                    err_text = format_exception_text(e)
                    print("Error during SelfReflection run:\n" + err_text)
                    history.append({"role": "assistant", "content": f"SelfReflection error: {err_text}"})
                    is_mcp = "Error invoking MCP tool" in str(e)
                    if is_mcp and not mcp_retry_sr_done:
                        mcp_retry_sr_done = True
                        history.append({"role": "user", "content": "go"})
                        # Skip to next outer loop iteration
                        continue
                    # Give up on SR this cycle; proceed to loop control (will likely break below)
                    sr_result = None
                # Prefer structured return code if available; else parse text
                sr_code = getattr(sr_result, "return_code", None) if sr_result else None
                sr_output = getattr(sr_result, "final_output", None) if sr_result else None
                if not sr_code and isinstance(sr_output, str):
                    s = sr_output.strip().upper()
                    if s.startswith("PREV"):
                        sr_code = "PREV"
                    elif s.startswith("CONTINUE"):
                        sr_code = "CONTINUE"
                print(f"[SR] Return code: {sr_code}")
                # If SelfReflection produced any text, echo to console and Telegram
                if isinstance(sr_output, str) and sr_output.strip():
                    print("[SR] Output:\n" + sr_output)
                    try:
                        await telegram_send_message(text=f"<b>SelfReflection</b>\n{sr_output}")
                    except Exception:
                        pass

                # Handle PREV/CONTINUE loop control
                if sr_code == "PREV":
                    # Do NOT add SR result to history; just push a 'go' from user and restart loop
                    print("[SR] PREV received: appending 'go' and restarting outer loop")
                    history.append({"role": "user", "content": "go"})
                    continue
                elif sr_code == "CONTINUE":
                    # Keep running SelfReflection repeatedly, skipping the main agent
                    while sr_code == "CONTINUE":
                        print(f"[SR] CONTINUE loop iteration (cycles={cycles})")
                        cycles += 1
                        if cycles > max_cycles:
                            print("Max cycles reached in SelfReflection; exiting loop")
                            sr_code = "MAX"
                            break
                        try:
                            sr_result = await Runner.run(
                                sr_agent,
                                history,
                                max_turns=100,
                            )
                        except Exception as e:
                            err_text = format_exception_text(e)
                            print("Error during SelfReflection CONTINUE iteration:\n" + err_text)
                            history.append({"role": "assistant", "content": f"SelfReflection error: {err_text}"})
                            is_mcp = "Error invoking MCP tool" in str(e)
                            if is_mcp and not mcp_retry_sr_done:
                                mcp_retry_sr_done = True
                                history.append({"role": "user", "content": "go"})
                                continue
                            break
                        # Update history fully to SR's view
                        try:
                            history = sr_result.to_input_list()
                        except Exception:
                            out2 = getattr(sr_result, "final_output", None)
                            if isinstance(out2, str) and out2:
                                history.append({"role": "assistant", "content": out2})
                        # Re-evaluate SR code
                        sr_code = getattr(sr_result, "return_code", None)
                        if not sr_code and isinstance(getattr(sr_result, "final_output", None), str):
                            s2 = sr_result.final_output.strip().upper()
                            if s2.startswith("PREV"):
                                sr_code = "PREV"
                            elif s2.startswith("CONTINUE"):
                                sr_code = "CONTINUE"
                        # Echo SR output for CONTINUE iterations as well
                        out_text = getattr(sr_result, "final_output", None)
                        if isinstance(out_text, str) and out_text.strip():
                            print("[SR] Output:\n" + out_text)
                            try:
                                await telegram_send_message(text=f"<b>SelfReflection</b>\n{out_text}")
                            except Exception:
                                pass
                        print(f"[SR] Return code after CONTINUE iteration: {sr_code}")
                        if sr_code == "PREV":
                            print("[SR] PREV received inside CONTINUE loop: appending 'go' and breaking to outer loop")
                            history.append({"role": "user", "content": "go"})
                            break

            # Loop control based on SelfReflection return code
            if sr_code not in ("PREV", "CONTINUE"):
                # At the end of the cycle, ask user for the next message only in interactive shells
                import sys as _sys
                try:
                    is_tty = hasattr(_sys, "stdin") and _sys.stdin and _sys.stdin.isatty()
                except Exception:
                    is_tty = False
                if is_tty:
                    try:
                        loop = asyncio.get_running_loop()
                        prompt_text = "Enter next user message (or 'exit' to finish, empty => 'go'): "
                        user_next = await loop.run_in_executor(None, lambda: input(prompt_text))
                    except Exception:
                        user_next = ""

                    if isinstance(user_next, str) and user_next.strip().lower() == "exit":
                        break
                    # Append user's message (default to 'go') and continue the outer loop
                    history.append({"role": "user", "content": (user_next or "go")})
                    continue
                else:
                    print("[INFO] Non-interactive shell detected; skipping CLI prompt and finishing.")
                    break

        # qa_prompt = await load_qa_prompt()
        # noinspection PyTypeChecker
        # history.append({"role": "user", "content": qa_prompt})

        # 2 шаг — получить содержимое prompt/prompt.md
        # result2 = await Runner.run(agent, history, max_turns=50)

        # step2_output = result2.final_output

        # print("Step 2 output:")
        # print(step2_output)

        return agent, history, step1_output

async def main(agent_path: str = None, user_input: str = "", debug: bool = False, agent_name: str = ""):
    # Initialize OpenAI client with proper proxy configuration
    await init_openai_client()
    # Allow caller to force debug prints
    global DEBUG
    if debug:
        DEBUG = True
    
    # When debugging, avoid external side effects like Telegram messages
    if not debug:
        await init_bot()
        
    # Load agent profile if specified
    agent_attrs = {}
    if agent_path and os.path.exists(agent_path):
        agent_attrs = extract_agent_attributes(agent_path)
    
    # Prepare the welcome message: show agent name and input
    display_name = (agent_attrs.get("name") if agent_attrs else None) or (agent_name or "Agent")
    # Use <pre><code> for multi-line; <code> for single-line
    msg_input = user_input or ""
    if "\n" in msg_input:
        code_block = f"<pre>{msg_input[:3600]}</pre>"
    else:
        code_block = f"<code>{msg_input[:3800]}</code>"
    welcome_text = f"<b>🔌 {display_name}</b>\n{code_block}"

    
    # KISS: Single-pass selection — merge possible outputs and extract once
    try:
        merged_output = _merge_outputs(
            (load_yaml(Path(agent_path)).get("output") if agent_path else None),
            (agent_attrs.get('output') if isinstance(agent_attrs, dict) else None),
        )
        m_chat, m_thread = _extract_tg_targets(merged_output)
        prompt_chat_id = m_chat
        prompt_thread_id = m_thread
    except Exception:
        pass

    # Save globally for all subsequent messages (welcome, MCP hook, digest)
    global selected_chat_id, selected_thread_id
    selected_chat_id = prompt_chat_id or TELEGRAM_CHAT_ID
    selected_thread_id = prompt_thread_id or (TELEGRAM_THREAD_ID or None)

    if not debug:
        print(f"[INFO] Agent path: {agent_path}")
        print(f"[INFO] Welcome target: chat_id={selected_chat_id or '(env default)'}, thread_id={selected_thread_id or '(auto/None)'}")

    if not debug:
        await send_telegram_welcome_message(
            welcome_text[:4000],
            chat_id=selected_chat_id,
            message_thread_id=selected_thread_id,
        )
    
    # Compute samples directory: agent directory + '/memory' if agent_path provided
    if agent_path:
        samples_dir = os.path.join(os.path.dirname(agent_path), 'memory')
    else:
        samples_dir = default_samples_dir

    samples_dir = default_samples_dir
    # Run the digest pipeline with the agent profile
    # Backward compatibility: some deployments may have older run_digest_pipeline signature
    try:
        agent, history, step1_output = await run_digest_pipeline(
            samples_dir,
            agent_path=agent_path,
            user_input=user_input,
            debug=debug,
            cli_agent_name=agent_name,
        )
    except TypeError:
        # Fallback: call without cli_agent_name for older servers
        agent, history, step1_output = await run_digest_pipeline(
            samples_dir,
            agent_path=agent_path,
            user_input=user_input,
            debug=debug,
        )


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
    # Send clean welcome banner without any progress bar
    telegram_last_message = await telegram_send_message(
        chat_id=chat_id,
        text=text,
        message_thread_id=(message_thread_id if message_thread_id is not None else (selected_thread_id or TELEGRAM_THREAD_ID or None))
    )
    print(
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
    # Accept legacy invocation: python -m call.app.call <AgentName> [<input>]
    agent_name = ""
    user_input = ""
    if args:
        agent_name = args[0]
        user_input = " ".join(args[1:]) if len(args) > 1 else ""

    _asyncio.run(main(agent_name=agent_name, user_input=user_input))
