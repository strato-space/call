from __future__ import annotations

import os
import re
import html as _py_html
from typing import Optional

from telegraph import Telegraph

from .html_sanitizer import minify_html_func

__all__ = [
    "publish_results",
    "create_telegrath_account",
]


async def create_telegrath_account(token: Optional[str] = None) -> str:
    """Create a Telegraph account and return the access token.

    If a token is provided, it will be used to initialize the client; otherwise,
    the TELEGRAPH_TOKEN environment variable will be used if available. The call
    mirrors the previous synchronous usage; it is safe to call from async code.
    """
    access_token = token or os.environ.get("TELEGRAPH_TOKEN")
    telegraph = Telegraph(access_token) if access_token else Telegraph()

    acc = telegraph.create_account(
        short_name="strato.space",
        author_name="AI Agent @ strato.space",
        author_url="https://linkedin.com/in/iqdoctor",
    )
    new_token = acc.get("access_token")
    print(f"Telegraph access_token: {new_token}")
    return new_token


async def publish_results(
    title: str = "AgentName Results",
    content: str | None = None,
    token: Optional[str] = None,
) -> str:
    """Publish aggregation results on Telegra.ph and return the page URL.

    The HTML content is sanitized and lightly minified before publishing.
    """
    access_token = token or os.environ.get("TELEGRAPH_TOKEN")
    telegraph = Telegraph(access_token) if access_token else Telegraph()

    raw_content = content or ""

    def _looks_like_html_fragment(s: str) -> bool:
        """Heuristic: treat text as preformatted HTML only when rich tags are present.

        We intentionally look only for a small set of explicit formatting tags
        (<b>, <i>, <a>, <code>, <pre>) so that plain text with symbols like
        '<' and '>' is still treated as plain and wrapped into a code block.
        """
        try:
            t = str(s or "").lower()
            if "<b" in t or "<i" in t or "<a" in t:
                return True
            if "<code" in t or "<pre" in t:
                return True
            return False
        except Exception:
            return False

    # If the content does not look like HTML, treat it as plain text and wrap it
    # into a <code>…</code> block so Telegraph renders it as monospace text.
    use_content = raw_content
    try:
        if isinstance(raw_content, str) and raw_content.strip() and not _looks_like_html_fragment(raw_content):
            escaped = _py_html.escape(raw_content)
            use_content = f"<code>{escaped}</code>"
    except Exception:
        # Best-effort only; on any failure fall back to the original content.
        use_content = raw_content

    clear_context = minify_html_func(use_content)

    # Build dynamic title: if caller passed an agent name, append ' Results' unless already present
    page_title = (
        f"{title} Results"
        if title and "Results" not in str(title)
        else (title or "AgentName Results")
    )

    response = telegraph.create_page(
        title=page_title,
        html_content=clear_context,
    )

    url = f"https://telegra.ph/{response['path']}"
    print("Results published to:", url)
    return url
