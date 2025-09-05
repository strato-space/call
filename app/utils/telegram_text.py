from __future__ import annotations

import re

__all__ = [
    "telegram_truncate_html_safe",
    "telegram_truncate_markdown_safe",
    "telegram_prepare_html",
    "telegram_prepare_markdown",
]


def telegram_truncate_html_safe(html: str, max_len: int) -> str:
    """Truncate a sanitized HTML string to <= max_len characters while preserving validity.

    Assumes the input is already sanitized for Telegram by clean_html_for_telegram().
    - Avoids cutting inside a tag or HTML entity
    - Ensures all opened tags are closed
    - Removes trailing partial tag fragments like '<tagnam'
    """
    try:
        if not isinstance(html, str):
            return ""
        if len(html) <= max_len:
            return html

        def strip_partial_tail(s: str) -> str:
            # Remove any partial tag at the end
            s = re.sub(r"<[^>]*$", "", s)
            # Remove any partial HTML entity at the end (e.g., '&amp')
            s = re.sub(r"&[^;\s]{0,10}$", "", s)
            return s

        # Start with a hard cut (reserve one char for ellipsis)
        s = html[: max_len - 1] + "…"
        s = strip_partial_tail(s)

        # Compute stack of still-open tags in s
        tag_re = re.compile(r"</?([a-zA-Z0-9]+)(?:\s[^>]*)?>")
        allowed = {"a", "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote", "br"}
        stack: list[str] = []
        for m in tag_re.finditer(s):
            tag = m.group(1).lower()
            if tag not in allowed:
                continue
            full = m.group(0)
            if full.startswith("</"):
                # closing: pop if present
                if stack and stack[-1] == tag:
                    stack.pop()
                else:
                    # Try to remove matching earlier open elsewhere
                    if tag in stack:
                        idx = len(stack) - 1 - stack[::-1].index(tag)
                        stack.pop(idx)
            else:
                # opening; treat <br> as self-contained
                if tag != "br":
                    stack.append(tag)

        def closers_for_stack(stk: list[str]) -> str:
            return "".join(f"</{t}>" for t in reversed(stk))

        # Ensure closers fit within max_len; if not, shorten s and recompute
        attempts = 0
        while attempts < 5:
            closers = closers_for_stack(stack)
            if len(s) + len(closers) <= max_len:
                s = s + closers
                return s
            # Need to make room: shorten s
            over = (len(s) + len(closers)) - max_len
            # Remove 'over' chars plus some buffer from end, then tidy tail and recompute stack
            cut_by = max(over + 1, 8)
            s = s[: max(0, len(s) - cut_by)]
            s = strip_partial_tail(s)
            # Recompute stack
            stack.clear()
            for m in tag_re.finditer(s):
                tag = m.group(1).lower()
                if tag not in allowed:
                    continue
                full = m.group(0)
                if full.startswith("</"):
                    if stack and stack[-1] == tag:
                        stack.pop()
                    else:
                        if tag in stack:
                            idx = len(stack) - 1 - stack[::-1].index(tag)
                            stack.pop(idx)
                else:
                    if tag != "br":
                        stack.append(tag)
            attempts += 1

        # Fallback: last resort hard trim to max_len without closers (should be rare)
        return (s[:max_len]).rstrip()
    except Exception:
        # On any failure, return a simple hard-clamped string
        return (str(html)[: max_len]).rstrip()


def telegram_truncate_markdown_safe(md: str, max_len: int) -> str:
    """Truncate Markdown safely for Telegram parse_mode=MARKDOWN.

    - Avoid cutting inside a triple code fence; ensure it's closed if opened
    - Remove trailing unmatched '[' or '(' typical for links
    - Keep within max_len (including any appended fences)
    """
    try:
        if not isinstance(md, str):
            return ""

        # Start from original content; truncate only if needed, but we'll
        # still run balancing logic even when not truncated so that code fences
        # are properly closed for Telegram rendering.
        if len(md) > max_len:
            s = md[: max_len - 1] + "…"
        else:
            s = md

        # Remove trailing partial HTML-like fragment (e.g., '<tagnam') since Markdown may include raw HTML
        s = re.sub(r"<[^>]*$", "", s)

        # Balance Markdown links: cut off trailing unmatched '[' or '('
        last_open_sq = s.rfind('[')
        last_close_sq = s.rfind(']')
        if last_open_sq > last_close_sq:
            s = s[:last_open_sq]

        last_open_par = s.rfind('(')
        last_close_par = s.rfind(')')
        # Likely part of a link if last ']' is before '('
        if last_open_par > last_close_par and s.rfind(']') < last_open_par:
            s = s[:last_open_par]

        # Ensure triple backtick fences are balanced
        fence_count = len(re.findall(r"```", s))
        if fence_count % 2 == 1:
            fence = "```"
            # Try to append closing fence if there's room (or no max_len pressure)
            if len(s) + len(fence) <= max_len:
                s += fence
            else:
                # Make room by trimming and then append
                s = s[: max(0, max_len - len(fence))]
                # Remove any trailing partial code block language identifier chunk
                s = re.sub(r"`+$", "", s)
                s += fence

        # Optionally, mitigate trailing single backticks
        backticks = s.count('`') - 3 * (len(re.findall(r"```", s)))
        if backticks % 2 == 1:
            # Remove a trailing '`' if present
            if s.endswith('`'):
                s = s[:-1]

        # Ensure we do not exceed max_len after balancing
        if len(s) > max_len:
            s = s[:max_len]
        return s
    except Exception:
        return (str(md)[: max_len]).rstrip()


# --- Centralized builders -----------------------------------------------------

_MDV2_SPECIAL = r"_[]()~`>#+-=|{}.!*"


def _escape_markdown_v2(text: str) -> str:
    s = text or ""
    # Escape all special characters for MarkdownV2
    for ch in _MDV2_SPECIAL:
        s = s.replace(ch, f"\\{ch}")
    return s


def telegram_prepare_markdown(md: str, max_len: int = 4000, version: str = "v2") -> tuple[str, str]:
    """Return (text, parse_mode) for safe Markdown send.

    - version: "v2" or "v1". Defaults to v2 escaping.
    - Ensures we don't exceed max_len and attempts to balance common constructs.
    """
    try:
        if version.lower() == "v2":
            escaped = _escape_markdown_v2(md or "")
            safe = telegram_truncate_markdown_safe(escaped, max_len)
            return safe, "MarkdownV2"
        else:
            # Basic Markdown (legacy) — rely on truncation only
            safe = telegram_truncate_markdown_safe(md or "", max_len)
            return safe, "Markdown"
    except Exception:
        # Fallback: plain text under limit
        s = (str(md) or "")
        if len(s) > max_len:
            s = s[: max_len - 1] + "…"
        return s, None


def _sanitize_html_minimal(text: str) -> str:
    """Escape everything then restore minimal safe tags we may intentionally add.

    Restores: <b>, </b>, <br>, <br/>
    Extend as needed.
    """
    s = text or ""
    s = re.sub(r"<[^>]*$", "", s)  # cut any trailing partial tag from input
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # restore minimal controlled tags
    s = (
        s.replace("&lt;b&gt;", "<b>")
         .replace("&lt;/b&gt;", "</b>")
         .replace("&lt;br&gt;", "<br>")
         .replace("&lt;br/&gt;", "<br/>")
         .replace("&lt;code&gt;", "<code>")
         .replace("&lt;/code&gt;", "</code>")
         .replace("&lt;pre&gt;", "<pre>")
         .replace("&lt;/pre&gt;", "</pre>")
    )
    return s


def telegram_prepare_html(html: str, max_len: int = 4000) -> tuple[str, str]:
    """Return (text, parse_mode) prepared for Telegram HTML parse mode.

    - Sanitizes to minimal safe subset and truncates without breaking entities/tags.
    """
    try:
        sanitized = _sanitize_html_minimal(html or "")
        safe = telegram_truncate_html_safe(sanitized, max_len)
        return safe, "HTML"
    except Exception:
        s = (str(html) or "")
        if len(s) > max_len:
            s = s[: max_len - 1] + "…"
        return s, None
