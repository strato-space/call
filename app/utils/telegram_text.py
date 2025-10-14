from __future__ import annotations

import re
from .html_sanitizer import (
    prepare_telegram_html as _prepare_html,
    truncate_telegram_html_safe as _truncate_html,
)

__all__ = [
    "telegram_truncate_html_safe",
    "telegram_truncate_markdown_safe",
    "telegram_prepare_html",
    "telegram_prepare_markdown",
]


def telegram_truncate_html_safe(html: str, max_len: int) -> str:
    """Wrapper that delegates truncation to centralized sanitizer module."""
    try:
        return _truncate_html(html, max_len)
    except Exception:
        return (str(html)[:max_len]).rstrip()


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
        last_open_sq = s.rfind("[")
        last_close_sq = s.rfind("]")
        if last_open_sq > last_close_sq:
            s = s[:last_open_sq]

        last_open_par = s.rfind("(")
        last_close_par = s.rfind(")")
        # Likely part of a link if last ']' is before '('
        if last_open_par > last_close_par and s.rfind("]") < last_open_par:
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
        backticks = s.count("`") - 3 * (len(re.findall(r"```", s)))
        if backticks % 2 == 1:
            # Remove a trailing '`' if present
            if s.endswith("`"):
                s = s[:-1]

        # Ensure we do not exceed max_len after balancing
        if len(s) > max_len:
            s = s[:max_len]
        return s
    except Exception:
        return (str(md)[:max_len]).rstrip()


# --- Centralized builders -----------------------------------------------------

# MarkdownV2 specials to escape in plain text.
# We intentionally skip '*', '_' and also '[', ']', '(', ')' so that
# emphasis and link syntax can render when already balanced in the input.
_MDV2_SPECIAL = r"~`>#+-=|{}.!"


def _escape_markdown_v2(text: str) -> str:
    s = text or ""
    # Escape all special characters for MarkdownV2
    for ch in _MDV2_SPECIAL:
        s = s.replace(ch, f"\\{ch}")
    return s


def telegram_prepare_markdown(
    md: str, max_len: int = 4000, version: str = "v2"
) -> tuple[str, str]:
    """Return (text, parse_mode) for safe Markdown send.

    - version: "v2" or "v1". Defaults to v2 escaping.
    - Ensures we don't exceed max_len and attempts to balance common constructs.
    """
    try:
        if version.lower() == "v2":
            # Normalize common formatting issues before escaping
            s0 = md or ""
            # Join Markdown links broken by newlines: "]\n(" -> "]("
            s0 = re.sub(r"\]\s*\n\s*\(", "](", s0)
            # Move outer bold/italic that wraps entire link inside the brackets:
            # **[Text](url)** -> [**Text**](url)
            s0 = re.sub(r"\*\*\[([^\]]+)\]\(([^)]+)\)\*\*", r"[**\1**](\2)", s0)
            # __[Text](url)__ -> [__Text__](url)
            s0 = re.sub(r"__\[([^\]]+)\]\(([^)]+)\)__", r"[__\1__](\2)", s0)
            escaped = _escape_markdown_v2(s0)
            safe = telegram_truncate_markdown_safe(escaped, max_len)
            return safe, "MarkdownV2"
        else:
            # Basic Markdown (legacy) — rely on truncation only
            safe = telegram_truncate_markdown_safe(md or "", max_len)
            return safe, "Markdown"
    except Exception:
        # Fallback: plain text under limit
        s = str(md) or ""
        if len(s) > max_len:
            s = s[: max_len - 1] + "…"
        return s, None


# --- Minimal Markdown -> HTML converter --------------------------------------

# Removed markdown_to_html_minimal in favor of a single HTML pipeline (KISS)


def _sanitize_html_minimal(text: str) -> str:
    """Deprecated: kept for backward-compat in case of imports; no-op passthrough."""
    return text or ""


def telegram_prepare_html(html: str, max_len: int = 4000) -> tuple[str, str]:
    """Wrapper that delegates to centralized html_sanitizer.prepare_telegram_html."""
    try:
        return _prepare_html(html, max_len)
    except Exception:
        s = str(html) or ""
        if len(s) > max_len:
            s = s[: max_len - 1] + "…"
        return s, None
