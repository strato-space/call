import os
import asyncio
import logging
from typing import Optional, Dict, Any
import urllib.parse
from pathlib import Path
import json
import httpx
from openai import OpenAI

# Import agent utilities (internal copy)
from utils.agent_utils import extract_agent_attributes, get_agent_instructions
from pathlib import Path
import shutil
from bs4 import BeautifulSoup      # use stdlib 'html.parser' to avoid extra deps


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
from agents.run_context import RunContextWrapper
from agents.mcp import MCPServerStdio
from agents.model_settings import ModelSettings

from telegraph import Telegraph

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram import Bot, Message
from telegram.constants import ParseMode, ChatAction
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(_env_file))

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
# Optional outbound proxy for OpenAI via httpx
PROXY_URL = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or None

# Global OpenAI client configured once
openai_client: OpenAI
if PROXY_URL:
    _transport = httpx.HTTPTransport(proxy=PROXY_URL)
    openai_client = OpenAI(api_key=OPENAI_API_KEY, http_client=httpx.Client(transport=_transport))
else:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Initialize bot at module level
global bot
bot: Bot

async def init_bot():
    global bot
    # Avoid picking up system proxy vars that break outbound connections on server
    for key in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        if os.environ.get(key):
            os.environ.pop(key, None)
    # Ensure direct access to common hosts
    no_proxy_hosts = [
        "localhost", "127.0.0.1", "::1",
        "api.telegram.org", "api.telegra.ph", "telegra.ph", "github.com",
    ]
    existing = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    merged = set(filter(None, [s.strip() for s in existing.split(",")]))
    merged.update(no_proxy_hosts)
    if merged:
        os.environ["NO_PROXY"] = ",".join(sorted(merged))
    bot = Bot(token=telegram_token)
    return bot

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
        message = await bot.send_message(
            chat_id=chat_id or telegram_last_message.chat_id,
            text=safe_text,
            parse_mode=parse_mode,
            message_thread_id=message_thread_id or telegram_last_message.message_thread_id or None,
        )
        
        telegram_last_message = message
        print(f"Message sent. ID: {message.message_id}, Chat ID: {message.chat_id}, Thread ID: {message.message_thread_id}")
        return message
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        raise e


async def send_digest_notification(
    url: str,
    themes_url: str | None = None,
    prompt_url: str | None = None,
    *,
    text: str = None,
    message_thread_id: int = None,
    agent_name: str | None = None,
    agent_path: str | Path | None = None,
    input_text: str | None = None,
) -> Optional[Message]:
    # Send digest to a specific Telegram chat.
    if text is None:
        text = f"📰 {url}"
        if input_text:
            try:
                safe_input = (input_text or "")[:3800]
                text = text + f"\n<code>input: {safe_input}</code>"
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
                        link = link.replace("{{digest_url}}", url)
                        if themes_url:
                            link = link.replace("{{themes_url}}", themes_url)
                        if prompt_url:
                            link = link.replace("{{prompt_url}}", prompt_url)
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
        message_obj = await telegram_send_message(
            text=text,
            reply_markup=reply_markup,
            message_thread_id=message_thread_id)

        print(f"Digest notification sent. ID: {message_obj.message_id}, Chat ID: {message_obj.chat_id}")
        return message_obj
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
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

        import asyncio
        from asyncio.subprocess import PIPE

        async def _run_git(cmd: list[str]) -> int:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(prompt_repo),
                stdout=PIPE,
                stderr=PIPE,
            )
            out, err = await proc.communicate()
            print(f"[git] {' '.join(cmd)}\nexit={proc.returncode}\nstdout={out.decode(errors='ignore')}\nstderr={err.decode(errors='ignore')}")
            return proc.returncode

        await _run_git(["git", "add", "-A", "."])
        rc_commit = await _run_git(["git", "commit", "-m", commit_msg])
        if rc_commit == 0:
            await _run_git(["git", "push"])
        else:
            print("[git] No changes to commit; skipping push")
    except Exception as e:
        print(f"[git] Post-run push failed: {e}")


async def telegram_send_message(chat_id: int = None, text: str = None, message_thread_id: int = None, reply_markup: InlineKeyboardMarkup = None):

    safe_text = clean_html_for_telegram(text or "")
    message = await bot.send_message(
        chat_id=chat_id or telegram_last_message.chat_id,
        message_thread_id = message_thread_id or (
        telegram_last_message.message_thread_id if telegram_last_message else None),
        text=safe_text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup
    )
    return message


logging.getLogger("openai").setLevel(logging.DEBUG)

default_samples_dir = str(Path(__file__).resolve().parents[2])

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

def clean_html_for_telegraph(html_content: str) -> str:
    # Parse with stdlib html.parser and sanitize to Telegraph-allowed subset
    # Note: Telegraph rejects many tags (e.g., h1/h2, hr, html/body/head, small, etc.)
    soup = BeautifulSoup(html_content, "html.parser")

    # 1) Drop document-level wrappers early
    for tag in soup.find_all(["html", "head", "body"]):
        tag.unwrap()

    # 2) Normalize headings (h1–h6) to <p><strong>…</strong></p>
    for level in ("h1", "h2", "h3", "h4", "h5", "h6"):
        for h in list(soup.find_all(level)):
            new_p = soup.new_tag("p")
            strong = soup.new_tag("strong")
            strong.string = h.get_text(strip=False)
            new_p.append(strong)
            h.replace_with(new_p)

    # 3) Replace <hr> with line breaks
    for hr in list(soup.find_all("hr")):
        br1 = soup.new_tag("br")
        br2 = soup.new_tag("br")
        hr.replace_with(br1)
        br1.insert_after(br2)

    # 4) Unwrap tags we explicitly don't want to keep as elements
    for t in list(soup.find_all(["small", "div", "span", "section", "article", "header", "footer", "nav"])):
        t.unwrap()

    # 5) Whitelist allowed tags; unwrap everything else while preserving text
    allowed_tags = {
        "p", "a", "em", "strong", "ul", "ol", "li",
        "br", "img", "figure", "figcaption", "pre", "code", "blockquote"
    }
    for tag in list(soup.find_all(True)):
        if tag.name not in allowed_tags:
            tag.unwrap()

    # 6) Strip disallowed attributes; keep only minimal safe attrs
    for tag in soup.find_all(True):
        # remove style/class/id and any other attrs by default
        allowed_attrs = {}
        if tag.name == "a":
            # Telegraph allows href (absolute URLs preferred)
            if tag.has_attr("href"):
                allowed_attrs["href"] = tag["href"]
        elif tag.name == "img":
            if tag.has_attr("src"):
                allowed_attrs["src"] = tag["src"]
            if tag.has_attr("alt"):
                allowed_attrs["alt"] = tag["alt"]
        # assign filtered attrs
        tag.attrs = allowed_attrs

    # 7) Final string without newlines; ensure non-empty content
    cleaned = str(soup).replace('\n', '')
    if not soup.get_text(strip=True):
        raise ValueError("Cleaned HTML has no text content!")
    return cleaned.strip()

def clean_html_for_telegram(html_content: str) -> str:
    """Sanitize HTML for Telegram parse_mode=HTML.

    Telegram supports a limited subset of tags. This function:
    - Converts <ul>/<ol>/<li> into plain-text bullet or numbered lines.
    - Unwraps unsupported tags while preserving text.
    - Strips disallowed attributes, keeping only href on <a>.
    """
    from bs4 import BeautifulSoup

    if not isinstance(html_content, str):
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    # Convert lists to plain text lines
    for ul in list(soup.find_all("ul")):
        lines = []
        for li in ul.find_all("li", recursive=False):
            txt = li.get_text(" ", strip=True)
            if txt:
                lines.append(f"• {txt}")
        new_p = soup.new_tag("p")
        new_p.string = "\n".join(lines) if lines else ""
        ul.replace_with(new_p)

    for ol in list(soup.find_all("ol")):
        lines = []
        idx = 1
        for li in ol.find_all("li", recursive=False):
            txt = li.get_text(" ", strip=True)
            if txt:
                lines.append(f"{idx}. {txt}")
                idx += 1
        new_p = soup.new_tag("p")
        new_p.string = "\n".join(lines) if lines else ""
        ol.replace_with(new_p)

    # Allowed tags for Telegram HTML
    allowed_tags = {"a", "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote", "br"}

    # Unwrap unsupported tags
    for tag in list(soup.find_all(True)):
        if tag.name not in allowed_tags:
            tag.unwrap()

    # Strip attributes, keep only href for <a>
    for tag in soup.find_all(True):
        allowed_attrs = {}
        if tag.name == "a" and tag.has_attr("href"):
            allowed_attrs["href"] = tag["href"]
        tag.attrs = allowed_attrs

    cleaned = str(soup)
    # Telegram is sensitive to stray newlines before/after code/pre; basic trim
    return cleaned.strip()

def minify_html_func(html_string: str) -> str:
    """Sanitize and lightly minify HTML without external minifiers.

    Steps:
    - Whitelist sanitizer via clean_html_for_telegraph()
    - Strip HTML comments
    - Collapse inter-tag whitespace (">   <" -> "><")
    - Trim leading/trailing whitespace
    """
    import re

    cleaned = clean_html_for_telegraph(html_string)
    # Remove HTML comments
    s = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    # Collapse inter-tag whitespace only (preserves spacing inside text nodes)
    s = re.sub(r">\s+<", "><", s)
    return s.strip()

async def load_prompt(directory="prompt", variables: dict = None, agent_path: str | None = None) -> dict:
    """
    Return the best available AI News Aggregator prompt and its GitHub URL.
    
    Args:
        directory: Directory (under prompt repo) to search for legacy prompt files
        variables: Optional dict of variables for template substitution.
                  Keys should match {{varname}} placeholders in the template.
        agent_path: Optional path to the agent.yaml; if provided, we will
                    load instructions from the agent's prompt_file (prompt.yaml)
                  
    Returns:
        dict: {
            'instructions': str, # The prompt text with variables substituted
            'url': str, # GitHub URL to edit the prompt
            'themes_url': str # URL to the file with themes
        }
    """
    # Base repo path (GitHub edit URL kept for compatibility)
    repo_base_url = "https://github.com/strato-space/ai/edit/main/"
    
    # 1) Prefer loading from the agent profile directory if provided
    if agent_path and os.path.exists(agent_path):
        try:
            # Use existing helper to extract instructions from agent.yaml
            instructions = get_agent_instructions(agent_path)
            agent_dir = Path(agent_path).parent
            # Compute URLs to prompt.yaml and themes.md in the same folder
            prompt_path = (agent_dir / "prompt.yaml")
            themes_path = (agent_dir / "themes.md")
            url = repo_base_url + urllib.parse.quote(str(prompt_path).replace('\\', '/'), safe="/")
            themes_url = repo_base_url + urllib.parse.quote(str(themes_path).replace('\\', '/'), safe="/")
            # Variable substitution
            if variables is None:
                variables = {}
            for var_name, var_value in variables.items():
                placeholder = f"{{{{{var_name}}}}}"
                instructions = instructions.replace(placeholder, str(var_value))
            return {
                "instructions": instructions,
                "url": url,
                "themes_url": themes_url,
            }
        except Exception:
            # Fallback to legacy search below
            pass

    # 2) Legacy search: look for prompt/ai-news-aggr/{v1,v2,v3}.md under discovered prompt repo
    try:
        prompt_repo = discover_prompt_repo()
    except Exception:
        prompt_repo = None
    base_dir = (prompt_repo / directory) if prompt_repo else Path(directory)
    candidates = []
    for folder in ("ai-news-aggr", "AiNewsAggr"):
        for name in ("v1.md", "v2.md", "v3.md"):
            candidates.append(base_dir / folder / name)
    
    # Default empty dict if no variables provided
    if variables is None:
        variables = {}

    instructions = None
    for path in candidates:
        path_str = path.as_posix()
        if os.path.exists(path_str):
            with open(path_str, "r", encoding="utf-8") as f:
                instructions = f.read()
                
            # Perform variable substitution
            for var_name, var_value in variables.items():
                placeholder = f"{{{{{var_name}}}}}"  # Double braces for f-string, then format adds another set
                instructions = instructions.replace(placeholder, str(var_value))
            
            quoted_path = urllib.parse.quote(path_str, safe="/")
            url = repo_base_url + quoted_path
            # Try both kebab and Pascal case for themes path in repo URL
            themes_rel_candidates = [
                "prompt/ai-news-aggr/themes.md",
                "prompt/AiNewsAggr/themes.md",
                "prompt/agents/ai-news-aggr/themes.md",
                "prompt/agents/AiNewsAggr/themes.md",
            ]
            themes_url = None
            for rel in themes_rel_candidates:
                themes_url = repo_base_url + rel
                break
            return {
                "instructions": instructions,
                "url": url,
                "themes_url": themes_url,
            }
    
    raise FileNotFoundError("No prompt file found")

async def create_telegrath_account():

    telegraph = Telegraph(TELEGRAPH_TOKEN)

    acc = telegraph.create_account(short_name='strato.space', author_name='AI News Aggregator Agent @ strato.space',
                                   author_url='https://linkedin.com/in/iqdoctor')
    token = acc.get('access_token')
    print(f"Telegraph access_token: {token}")


async def publish_results(title: str = "AgentName Results", content: str = None) -> str:
    """Publish aggregation results on Telegra.ph."""

    telegraph = Telegraph(TELEGRAPH_TOKEN)

    clear_context = minify_html_func(content)
    # Save clear_context to call/logs/y.html
    # todo: use [MCP Hook] Parameters: {'path': '/home/strato-space/prompt/agents/AiNewsAggr/memory/ai-news-aggr'}
    # with open('call/logs/y.html', 'w', encoding='utf-8') as f:
    #    f.write(clear_context)
    # Build dynamic title: if caller passed an agent name, append ' Results' unless already present
    page_title = (f"{title} Results" if title and "Results" not in str(title) else (title or "AgentName Results"))
    response = telegraph.create_page(
        title=page_title,
        html_content=clear_context,
    )

    url = f"https://telegra.ph/{response['path']}"
    print("Results published to:", url)
    return url

def telegram_progress_bar(thoughtNumber, totalThoughts, bar_length=10):
    filled = int(bar_length * thoughtNumber / totalThoughts)
    bar = "█" * filled + "░" * (bar_length - filled)
    percent = int(100 * thoughtNumber / totalThoughts)
    return f"{bar} {thoughtNumber}/{totalThoughts}"

    # Example usage:
    # msg = telegram_progress_bar(7, 10)
    # Output: "███████░░░ 7/10 (70%)"

async def edit_message_text(text):
    await bot.edit_message_text(
        chat_id=telegram_last_message.chat_id,
        message_id=telegram_last_message.message_id,
        text=text,
        parse_mode="HTML")

class MCPServerStdioHook(MCPServerStdio):
    """Wrapper for MCPServerStdio that write logs tool calls to stdout and tg bot."""
    from typing import Any
    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None) -> CallToolResult:
        print(f"[MCP Hook] Calling tool: {tool_name}")
        print(f"[MCP Hook] Parameters: {arguments}")
        parameters = arguments

        if tool_name != 'sequentialthinking':
             return await super().call_tool(tool_name, parameters)
        try:
            thought = parameters['thought']
            if telegram_last_message:
                bar = telegram_progress_bar(parameters['thoughtNumber'], parameters['totalThoughts'])
                text = f"<b>💭Thinking: {bar}</b>\n\n{thought}\n\n<b>💭Thinking: {bar}</b>"
                await edit_message_text(text)
                # Send typing action
                await bot.send_chat_action(chat_id=telegram_last_message.chat_id,
                                           message_thread_id=telegram_last_message.message_thread_id,
                                           action=ChatAction.TYPING)
                await bot.send_chat_action(chat_id=telegram_last_message.chat_id, action=ChatAction.TYPING)

            result = await super().call_tool(tool_name, parameters)
            print(f"[MCP Hook] Tool {tool_name} completed successfully")

            return result
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
    return ''.join(p.capitalize() for p in parts)


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


def discover_agent_yaml(agent_name: str) -> Path | None:
    """Discover agent definition YAML with creator->execution precedence.

    Search order (separates creator system from execution system):
    1) Creator:   prompt/AgentFab/<AgentName>.yaml
                  prompt/AgentFab/<AgentName>/agent.yaml
       (case-insensitive matching on <AgentName>)
    2) Execution: prompt/agents/<AgentName>/agent.yaml
       (existing behavior; case-insensitive dir match)

    Args:
        agent_name: PascalCase AgentName (use to_pascal_case() on inputs).

    Returns:
        Path to YAML file or None if not found.
    """
    repo = discover_prompt_repo()

    # 1) Creator system lookup under prompt/AgentFab
    creator_dir = repo / 'AgentFab'
    if creator_dir.exists():
        # 1.a direct file: AgentFab/<AgentName>.yaml
        direct_file = creator_dir / f"{agent_name}.yaml"
        if direct_file.exists():
            return direct_file
        # 1.b nested folder: AgentFab/<AgentName>/agent.yaml (case-insensitive)
        for child in creator_dir.iterdir():
            if child.is_dir() and child.name.lower() == agent_name.lower():
                cand = child / 'agent.yaml'
                if cand.exists():
                    return cand

    # 2) Execution system lookup under prompt/agents (existing behavior)
    agents_dir = repo / 'agents'
    if not agents_dir.exists():
        return None
    # direct match
    direct = agents_dir / agent_name / 'agent.yaml'
    if direct.exists():
        return direct
    # case-insensitive scan
    for child in agents_dir.iterdir():
        if child.is_dir() and child.name.lower() == agent_name.lower():
            cand = child / 'agent.yaml'
            if cand.exists():
                return cand
    return None


def load_yaml(path: Path) -> dict:
    import yaml  # lazy import
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


class AgentDTO:
    """DTO for strato.Agent loaded from YAML.

    Variant A prompt handling:
    - Supports multiple prompt files.
    - Loads only prompts marked as ON by markers: '🟢', '+', 'v', 'on' (case-insensitive).
    - First loaded prompt is the default.
    - Enriches prompt with agent attributes if keys are missing (prompt overrides agent).
    - Exposes getDefaultPrompt(), getPrompt(name), getPromptNames().
    """
    def __init__(self, raw: dict, base_dir: Path | None = None):
        self.raw = raw or {}
        self.base_dir: Path | None = base_dir
        self.id: str | None = self.raw.get('id')
        self.name: str | None = self.raw.get('name')
        self.model: str | None = self.raw.get('model') or self.raw.get('llm')
        self.instructions: str | None = self.raw.get('instructions') or self.raw.get('prompt')
        self.prompts = self.raw.get('prompts') or []
        self.prompt_file: str | None = self.raw.get('prompt_file')
        self.attributes: dict[str, Any] = {}
        # Extract model settings from possible sections
        self.model_settings = self._extract_model_settings()
        # Everything not used in model settings goes to attributes map
        used_keys = set(['id', 'name', 'model', 'llm', 'instructions', 'prompt', 'prompts', 'prompt_file', 'model_settings', 'modelSettings'])
        for k, v in self.raw.items():
            if k not in used_keys:
                self.attributes[k] = v

        # Internal prompt registry: name -> enriched prompt dict
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

        Priority:
        1) If default prompt loaded and contains 'instructions', use it.
        2) Else if top-level 'instructions' provided, use it.
        3) Else fallback to await load_prompt().
        """
        default_prompt = self.getDefaultPrompt()
        if isinstance(default_prompt, dict) and default_prompt.get('instructions'):
            return default_prompt.get('instructions'), self.attributes
        if self.instructions:
            return self.instructions, self.attributes
        prompt = await load_prompt()
        return prompt["instructions"], self.attributes

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
        """Load prompts according to Variant A rules, supporting both list and mapping forms.

        Supported shapes:
        - list of strings: ["on|path/to/prompt.yaml", "off|Other.yaml"]
        - mapping: { Name: "instructions text" | {instructions: ..., ...} }
        """
        ordered_candidates: list[tuple[str, bool]] = []  # (file_or_name, is_on)
        # Do NOT auto-include prompt_file; default = first ON in prompts per user's preference
        # Parse entries in self.prompts
        if isinstance(self.prompts, list):
            for entry in self.prompts:
                if not isinstance(entry, str):
                    continue
                parts = [p.strip() for p in entry.split('|')]
                if len(parts) >= 2:
                    marker, target = parts[0], parts[1]
                    is_on = self._is_on_marker(marker)
                else:
                    # entry is a plain path without marker
                    target = parts[0]
                    is_on = False
                ordered_candidates.append((target, is_on))
            # Load prompts: resolve targets to files (with or without extension).
            # The FIRST entry in the list is considered the default, regardless of marker.
            first = True
            for target, is_on in ordered_candidates:
                if not isinstance(target, str):
                    continue
                resolved = self._resolve_prompt_target(target)
                if resolved is None:
                    continue
                data = None
                suffix = resolved.suffix.lower()
                if suffix in ('.yaml', '.yml'):
                    data = self._load_prompt_file(str(resolved))
                elif suffix == '.md':
                    try:
                        with open(resolved, 'r', encoding='utf-8') as f:
                            md_text = f.read()
                        data = {"instructions": md_text}
                    except Exception:
                        data = None
                if data is None:
                    continue
                name = Path(target).stem if Path(target).suffix else Path(resolved).stem
                # Set default on the very first successfully loaded entry
                self._register_prompt(name, data, is_default_candidate=first or is_on)
                first = False
        elif isinstance(self.prompts, dict):
            # Mapping form: name -> instructions string or prompt object
            first = True
            for name, value in self.prompts.items():
                prompt_obj = None
                if isinstance(value, str):
                    prompt_obj = {"instructions": value}
                elif isinstance(value, dict):
                    prompt_obj = dict(value)
                if prompt_obj:
                    self._register_prompt(str(name), prompt_obj, is_default_candidate=first)
                    first = False

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


async def run_digest_pipeline(samples_dir: str, agent_path: str = None, user_input: str = "", debug: bool = False, cli_agent_name: str = ""):
    # Запускаем два MCP-сервера параллельно (filesystem и sequential-thinking)
    server_gsheets = None
    async with MCPServerStdioHook(
            params={
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", samples_dir]
            },
            client_session_timeout_seconds=30
    ) as server_fs, MCPServerStdioHook(
        params={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]
        },
        client_session_timeout_seconds=30
    ) as server_seq, MCPServerStdioHook(
        params={
            "command": "uvx",
            # "args": ["--env-file", samples_dir + "/server/mcp/.env", "mcp-google-sheets@latest"]
            "args": ["--env-file", samples_dir + "/call/.env", "--from", samples_dir + "/mcp-google-sheets/", "mcp-google-sheets"]
        },
        client_session_timeout_seconds=30
    ) as server_gsheets, MCPServerStdioHook(
            params={
                "command": "uvx",
                "args": ["--env-file", samples_dir + "/server/mcp/.env", "--from", samples_dir + "/voice", "mcp-voicebot"]
            },
        client_session_timeout_seconds=30
    ) as server_voice:

        # Получаем инструменты от обоих серверов
        run_context = RunContextWrapper(context=None)
        agent = Agent(name="test", instructions="test")
        tools_fs = await server_fs.list_tools(run_context, agent)
        tools_seq = await server_seq.list_tools(run_context, agent)
        if server_gsheets:
          tools_gsheets = await server_gsheets.list_tools(run_context, agent)
          print("Google Sheets tools:", tools_gsheets)

        # Load agent profile if specified
        agent_attrs = {}
        if agent_path and os.path.exists(agent_path):
            agent_attrs = extract_agent_attributes(agent_path)
            
        # Set up template variables with agent attributes
        template_vars = {
            "agent": agent_path or "default",
            "name": agent_attrs.get("name", "AI News Aggregator"),
            "role": agent_attrs.get("role", "an AI news aggregation assistant"),
            "goal": agent_attrs.get("goal", "gather and summarize the latest news")
        }
        
        # Load prompt with template variables and agent profile location
        prompt_data = await load_prompt(variables=template_vars, agent_path=agent_path)
        
        # Determine agent yaml path: prefer explicit agent_path, otherwise discover
        agent_yaml_path = agent_path if agent_path else None
        # agent_path may come as YAML/MD path; keep existing attrs, and try to enrich from prompt repo
        if not agent_yaml_path and agent_attrs.get("name"):
            norm = to_pascal_case(agent_attrs.get("name"))
            agent_yaml_path = discover_agent_yaml(norm)
        if debug:
            print(f"[DEBUG] Discovered agent YAML path: {agent_yaml_path}")
        # Get model from environment or default; may be overridden by yaml
        model_name = os.environ.get("LLM_MODEL", "gpt-4.5")
        model_settings_obj = None
        if agent_yaml_path:
            # Normalize to Path in case a string was provided
            path_obj = agent_yaml_path
            try:
                from os import PathLike as _PathLike  # type: ignore
            except Exception:
                _PathLike = tuple()
            if isinstance(agent_yaml_path, (str, _PathLike)):
                path_obj = Path(str(agent_yaml_path))
            dto = AgentDTO(load_yaml(path_obj), base_dir=path_obj.parent)
            if debug:
                import pprint, sys, yaml
                print(f"[DEBUG] AgentDTO loaded from: {path_obj}")
                payload = {
                    "id": dto.id,
                    "name": dto.name,
                    "model": dto.model,
                    "instructions_preview": (dto.instructions[:200] + '...') if isinstance(dto.instructions, str) and len(dto.instructions or '') > 200 else dto.instructions,
                    "prompts": dto.prompts,
                    "prompt_names": dto.getPromptNames(),
                    "default_prompt_name": getattr(dto, "_default_prompt_name", None),
                    "attributes": dto.attributes,
                    "model_settings": getattr(dto.model_settings, "__dict__", dto.model_settings),
                }
                pprint.PrettyPrinter(width=120).pprint(payload)
                # Dump enriched default prompt as YAML
                default_prompt = dto.getDefaultPrompt() or {}
                print("[DEBUG] Enriched default prompt (YAML):")
                try:
                    print(yaml.safe_dump(default_prompt, allow_unicode=True, sort_keys=False))
                except Exception:
                    pprint.PrettyPrinter(width=120).pprint(default_prompt)

                # Print instructions in readable form with line breaks
                instr = default_prompt.get('instructions') if isinstance(default_prompt, dict) else None
                if instr is not None:
                    print("[DEBUG] Instructions (readable):")
                    if isinstance(instr, str):
                        print(instr)
                    elif isinstance(instr, list):
                        print("\n".join(str(x) for x in instr))
                    else:
                        # Fallback to YAML dump of just instructions
                        try:
                            print(yaml.safe_dump({'instructions': instr}, allow_unicode=True, sort_keys=False))
                        except Exception:
                            print(str(instr))

                # Also show augmented instructions that will be sent to LLM (instructions + extra attrs as YAML, excluding model settings and aliases)
                try:
                    dto_instr, extra_attrs = dto.getInstructions()
                    if dto_instr:
                        augmented = dto_instr
                        if extra_attrs:
                            # Filter out model-setting-like keys
                            ms_keys = {"temperature", "top_p", "max_tokens", "stop", "presence_penalty", "frequency_penalty", "n", "best_of", "alias", "aliases"}
                            try:
                                if dto.model_settings:
                                    ms_keys |= {k for k in vars(dto.model_settings).keys()}
                            except Exception:
                                pass
                            filtered = {k: v for k, v in extra_attrs.items() if k not in ms_keys}
                            try:
                                ctx_yaml = yaml.safe_dump(filtered, allow_unicode=True, sort_keys=False)
                            except Exception:
                                ctx_yaml = str(filtered)
                            augmented = f"{dto_instr}\n\n# Context\n{ctx_yaml}"
                        print("[DEBUG] Augmented instructions (to LLM):")
                        print(augmented)
                except Exception:
                    pass
                print("[DEBUG] Stopping after AgentDTO dump (--debug).")
                sys.exit(0)
        # Merge attrs from DTO for normal (non-debug) run
        agent_attrs = {**dto.attributes, **agent_attrs}
        if dto.model:
            model_name = dto.model
        if dto.model_settings:
            model_settings_obj = dto.model_settings
        # use instructions from DTO; augment with non-model settings attrs for LLM context
        dto_instructions, extra_attrs = await dto.getInstructions()
        if dto_instructions:
            try:
                ms_keys = {"temperature", "top_p", "max_tokens", "stop", "presence_penalty", "frequency_penalty", "n", "best_of", "alias", "aliases"}
                if dto.model_settings:
                    ms_keys |= {k for k in vars(dto.model_settings).keys()}
                filtered = {k: v for k, v in extra_attrs.items() if k not in ms_keys}
                import yaml as _yaml
                ctx_yaml = _yaml.safe_dump(filtered, allow_unicode=True, sort_keys=False) if filtered else ""
                agent_instructions = dto_instructions if not ctx_yaml else f"{dto_instructions}\n\n# Context\n{ctx_yaml}"
            except Exception:
                agent_instructions = dto_instructions
            # Do not let aliases seep into agent runtime attrs either
            if isinstance(extra_attrs, dict):
                extra_attrs = {k: v for k, v in extra_attrs.items() if k not in {"alias", "aliases"}}
            agent_attrs |= extra_attrs
        # Create agent with attributes from profile or defaults
        # Prefer CLI-provided name if available, fallback to profile or default
        agent_name = (locals().get('cli_agent_name') or agent_attrs.get("name", "AI News Aggregator"))
        # Ensure we use DTO-derived instructions (default prompt) and never fall back to legacy loader
        agent_instructions = locals().get('agent_instructions', None) or (await dto.getInstructions())[0] or ""
        
        # Note: temperature handling removed per request. Keep dto.model_settings as-is, but sanitize max token fields.
        # Some providers require numeric types for max tokens; drop or coerce invalid string values like ">= 30000".
        try:
            import re as _re
            def _to_int_or_none(v):
                if isinstance(v, int):
                    return v
                if isinstance(v, str):
                    m = _re.search(r"\d+", v)
                    return int(m.group(0)) if m else None
                return None
            if model_settings_obj:
                if hasattr(model_settings_obj, 'max_tokens'):
                    v = getattr(model_settings_obj, 'max_tokens')
                    setattr(model_settings_obj, 'max_tokens', _to_int_or_none(v))
                if hasattr(model_settings_obj, 'max_output_tokens'):
                    v = getattr(model_settings_obj, 'max_output_tokens')
                    setattr(model_settings_obj, 'max_output_tokens', _to_int_or_none(v))
        except Exception:
            pass
        agent = Agent(
            name=f"{agent_name} [agent]",
            instructions=agent_instructions,
            # prompt=prompt_data["prompt"],
            tools=[WebSearchTool()],
            mcp_servers=[server_fs, server_seq],
            model=model_name,
            model_settings=(model_settings_obj or ModelSettings())
        )

        # Выполняем запрос: передаём user_input как элемент истории в формате {"role": "user", "content": user_input}
        _seed_history = []
        try:
            if user_input:
                _seed_history.append({"role": "user", "content": user_input})
        except Exception:
            pass
        result1 = await Runner.run(
            agent,
            _seed_history,
            max_turns=50
        )

        history = result1.to_input_list()
        step1_output = result1.final_output

        print("Step 1 output:")
        print(step1_output)
        title = "📰 AI News Aggregator"

        # Prefer CLI-provided agent name for publication title
        title_name = (cli_agent_name or agent_name or "Agent")
        if isinstance(step1_output, str) and len(step1_output) < 4000:
            # Send digest directly to Telegram as HTML (cleaning handled in telegram_send_message)
            await send_digest_notification(
                "",
                prompt_data["themes_url"],
                prompt_data["url"],
                agent_name=agent_name,
                agent_path=agent_path,
                input_text=user_input,
                text=step1_output,
            )
        else:
            url = await publish_results(title=title_name, content=step1_output)
            await send_digest_notification(
                url,
                prompt_data["themes_url"],
                prompt_data["url"],
                agent_name=agent_name,
                agent_path=agent_path,
                input_text=user_input,
            )

        # Post-run: commit and push changes using normalized agent_name and raw user_input
        await post_run_git_push(agent_name=agent_name, user_input=user_input)

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
    # When debugging, avoid external side effects like Telegram messages
    if not debug:
        await init_bot()
        
    # Load agent profile if specified
    agent_attrs = {}
    if agent_path and os.path.exists(agent_path):
        agent_attrs = extract_agent_attributes(agent_path)
    
    # Prepare the welcome message: show agent name and input
    display_name = (agent_attrs.get("name") if agent_attrs else None) or (agent_name or "Agent")
    welcome_text = (
        f"<b>🔌 {display_name}</b>\n"
        f"<code>input: {user_input[:3800]}</code>"
    )

    
    # Prefer chat/thread from agent YAML/prompt (including output.tg.*), then attributes, then env
    prompt_chat_id = None
    prompt_thread_id = None
    try:
        # 0) If agent_path provided, read output directly from YAML first
        if agent_path:
            try:
                _raw_yaml = load_yaml(Path(agent_path))
                _raw_out = _raw_yaml.get("output") if isinstance(_raw_yaml, dict) else {}
                c_id, t_id = _extract_tg_targets(_raw_out)
                prompt_chat_id = c_id or prompt_chat_id
                prompt_thread_id = t_id or prompt_thread_id
            except Exception:
                pass
        # Prefer explicit agent_path passed to main()
        dto = None
        apath_obj = None
        if agent_path:
            apath_obj = Path(agent_path)
            dto = AgentDTO(load_yaml(apath_obj), base_dir=apath_obj.parent)
        else:
            # Fallback: attempt discovery by name
            norm = to_pascal_case(agent_attrs.get("name", "")) if agent_attrs else ""
            apath_obj = discover_agent_yaml(norm) if norm else None
            dto = AgentDTO(load_yaml(apath_obj), base_dir=apath_obj.parent) if apath_obj else None
        if dto:
            default_prompt = dto.getDefaultPrompt() or {}
            # Read output from default prompt; if absent, fallback to dto.attributes
            output_raw = default_prompt.get("output") if isinstance(default_prompt, dict) else None
            if not output_raw and isinstance(dto.attributes, dict):
                output_raw = dto.attributes.get("output")
            c_id2, t_id2 = _extract_tg_targets(output_raw)
            prompt_chat_id = c_id2 or prompt_chat_id
            prompt_thread_id = t_id2 or prompt_thread_id
    except Exception:
        pass

    # Fallback: read from agent_attrs if still missing
    if (prompt_chat_id is None or prompt_thread_id is None) and isinstance(agent_attrs, dict):
        c_id3, t_id3 = _extract_tg_targets(agent_attrs.get("output"))
        if prompt_chat_id is None:
            prompt_chat_id = c_id3
        if prompt_thread_id is None:
            prompt_thread_id = t_id3

    if not debug:
        print(f"[INFO] Agent path: {agent_path}")
        print(f"[INFO] Welcome target: chat_id={prompt_chat_id or '(env default)'}, thread_id={prompt_thread_id or '(auto/None)'}")

    if not debug:
        await send_telegram_welcome_message(
            welcome_text[:4000],
            chat_id=prompt_chat_id,
            message_thread_id=prompt_thread_id,
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
    # Choose chat: prefer explicit override from prompt; else fall back to env (secondary on Windows)
    if chat_id is None:
        chat_id = TELEGRAM_SECOND_CHAT_ID if os.name == "nt" else TELEGRAM_CHAT_ID
    thinking_message = f'💭Thinking: {telegram_progress_bar(0,10)}'

    telegram_last_message = await telegram_send_message(
        chat_id=chat_id,
        text=thinking_message + '\n\n' + text + '\n\n' + thinking_message,
        message_thread_id=(message_thread_id if message_thread_id is not None else (
            TELEGRAM_THREAD_ID if chat_id == TELEGRAM_CHAT_ID else None
        ))
    )
    print(
        f"Last message set. ID: {telegram_last_message.message_id}, Chat ID: {telegram_last_message.chat_id}, Thread ID: {telegram_last_message.message_thread_id}")


if __name__ == "__main__":
    import argparse
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description='call — Call subsystem CLI: invoke agent by name with input',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples:
  pwsh -c "python app/call.py DiscoveryAgent 'Summarize today news about Apple'"
  python app/call.py UxManager "Analyze this dialog: <text>"
  '''
    )

    parser.add_argument('agent', type=str, help='AgentName (case-insensitive, normalized to PascalCase)')
    parser.add_argument('input', type=str, nargs='?', default='', help='Input payload text')
    parser.add_argument('--agent-profile', type=str, help='Path to agent profile (optional)', default=os.getenv('AI_AGENT_PROFILE'))
    parser.add_argument('--debug', action='store_true', help='Enable debug prints and stop after AgentDTO load')
    parser.add_argument('--echo', action='store_true', help='Echo agent and input as JSON, then exit')

    args = parser.parse_args()

    # Preserve user-provided case; only strip leading '@' and whitespace
    raw_agent = (args.agent or "").strip()
    if raw_agent.startswith('@'):
        raw_agent = raw_agent[1:]
    agent_name = raw_agent
    user_input = args.input or ''

    

    # Try discover agent YAML by normalized name
    yaml_path = discover_agent_yaml(agent_name)
    agent_path = args.agent_profile
    if not agent_path and yaml_path:
        agent_path = str(yaml_path)

    # Echo mode: print structured agent/input and exit without running pipeline
    if args.echo:
        try:
            print(json.dumps({"module": "call.app.call", "name": agent_name, "input": user_input, "echo": True, "agent_path": agent_path, "debug": args.debug}, ensure_ascii=False))
            sys.stdout.write('\n')
            sys.stdout.flush()
        except Exception:
            print(f"{agent_name}\t{user_input}")
        sys.exit(0)

    try:
        # Pass discovered/explicit agent profile into pipeline
        asyncio.run(main(agent_path=agent_path, user_input=user_input, debug=args.debug, agent_name=agent_name))
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        import traceback
        print("\nError:", file=sys.stderr)
        traceback.print_exc()
        print("\nFull error details:", file=sys.stderr)
        print(f"Type: {type(e).__name__}", file=sys.stderr)
        print(f"Message: {str(e)}", file=sys.stderr)
        sys.exit(1)
