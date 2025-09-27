from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString
import re

__all__ = [
    "sanitize_telegram_html",
    "truncate_telegram_html_safe",
    "prepare_telegram_html",
    "clean_html_for_telegraph",
    "minify_html_func",
]

# Telegram Bot API — allowed HTML subset for parse_mode=HTML
# https://core.telegram.org/bots/api#html-style
ALLOWED_TELEGRAM_TAGS = {
    "a", "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "blockquote", "tg-spoiler", "tg-emoji"
}


def clean_html_for_telegraph(html_content: str) -> str:
    """Sanitize HTML for Telegra.ph.

    Notes:
    - Telegraph rejects many tags (e.g., h1/h2, hr, html/body/head, small, etc.)
    - We unwrap unsupported blocks while preserving text content.
    - We keep a minimal set of safe attributes.
    """
    # Some inputs may arrive HTML-escaped (e.g., '&lt;h3&gt;'). Unescape once.
    try:
        import html as _py_html
        if isinstance(html_content, str) and ("&lt;" in html_content or "&gt;" in html_content):
            html_content = _py_html.unescape(html_content)
    except Exception:
        pass

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
        allowed_attrs = {}
        if tag.name == "a":
            if tag.has_attr("href"):
                allowed_attrs["href"] = tag["href"]
        elif tag.name == "img":
            if tag.has_attr("src"):
                allowed_attrs["src"] = tag["src"]
            if tag.has_attr("alt"):
                allowed_attrs["alt"] = tag["alt"]
        tag.attrs = allowed_attrs

    # 7) Final string without newlines; ensure non-empty content
    cleaned = str(soup).replace('\n', '')
    if not soup.get_text(strip=True):
        raise ValueError("Cleaned HTML has no text content!")
    return cleaned.strip()


def sanitize_telegram_html(html_content: str) -> str:
    """Return sanitized HTML compatible with Telegram parse_mode=HTML.

    Applies Telegram Bot API rules: strip unsupported tags/attrs, normalize headings,
    lists, hr, spoilers, and fix self-closing tags.
    """
    if not isinstance(html_content, str):
        return ""

    # Some inputs may arrive HTML-escaped (e.g., '&lt;h3&gt;'). Unescape once so headers/lists can be normalized.
    try:
        import html as _py_html
        if "&lt;" in html_content or "&gt;" in html_content:
            html_content = _py_html.unescape(html_content)
    except Exception:
        pass

    soup = BeautifulSoup(html_content, "html.parser")

    # Normalize headings: convert <h1>.. <h6> into <b>Title</b> plus a newline
    for level in ("h1", "h2", "h3", "h4", "h5", "h6"):
        for h in list(soup.find_all(level)):
            bold = soup.new_tag("b")
            bold.string = h.get_text(strip=False)
            h.replace_with(bold)
            bold.insert_after(NavigableString("\n"))

    # Convert Telegram spoilers: <span class="tg-spoiler"> -> <tg-spoiler>
    for sp in list(soup.find_all("span", class_="tg-spoiler")):
        tg = soup.new_tag("tg-spoiler")
        tg.string = sp.get_text(strip=False)
        sp.replace_with(tg)

    # Replace <hr> with two newline characters
    for hr in list(soup.find_all("hr")):
        nl1 = NavigableString("\n")
        nl2 = NavigableString("\n")
        hr.replace_with(nl1)
        nl1.insert_after(nl2)

    # Replace existing <br> tags with newline characters (not listed in supported tags)
    for br in list(soup.find_all("br")):
        br.replace_with(NavigableString("\n"))

    # Convert lists to plain text lines
    for ul in list(soup.find_all("ul")):
        lines = []
        for li in ul.find_all("li", recursive=False):
            txt = li.get_text(" ", strip=True)
            if txt:
                lines.append(f"• {txt}")
        replacement_text = "\n".join(lines) if lines else ""
        ul.replace_with(replacement_text)

    for ol in list(soup.find_all("ol")):
        lines = []
        idx = 1
        for li in ol.find_all("li", recursive=False):
            txt = li.get_text(" ", strip=True)
            if txt:
                lines.append(f"{idx}. {txt}")
                idx += 1
        replacement_text = "\n".join(lines) if lines else ""
        ol.replace_with(replacement_text)

    # Unwrap unsupported tags
    for tag in list(soup.find_all(True)):
        if tag.name not in ALLOWED_TELEGRAM_TAGS:
            tag.unwrap()

    # Strip attributes, keep only allowed per Bot API:
    # - <a href="...">
    # - <blockquote expandable>
    # - <tg-emoji emoji-id="...">
    # - <code class="language-xyz"> (when nested in <pre> or standalone)
    for tag in soup.find_all(True):
        allowed_attrs = {}
        if tag.name == "a":
            if tag.has_attr("href"):
                allowed_attrs["href"] = tag["href"]
        elif tag.name == "blockquote":
            # Preserve boolean 'expandable' attribute if present (valueless boolean)
            if tag.has_attr("expandable"):
                # BeautifulSoup serializes None as a valueless attribute: <blockquote expandable>
                allowed_attrs["expandable"] = None
        elif tag.name == "tg-emoji":
            if tag.has_attr("emoji-id"):
                allowed_attrs["emoji-id"] = tag["emoji-id"]
        elif tag.name == "code":
            # Keep 'class' tokens that start with 'language-'
            cls = tag.get("class")
            if isinstance(cls, list):
                keep = [c for c in cls if isinstance(c, str) and c.startswith("language-")]
                if keep:
                    allowed_attrs["class"] = keep
        tag.attrs = allowed_attrs

    cleaned = str(soup)

    # Fix self-closing tags that Telegram doesn't like
    cleaned = re.sub(r'<(\w+)/>', r'<\1>', cleaned)
    cleaned = re.sub(r'<(\w+)\s+/>', r'<\1>', cleaned)

    return cleaned.strip()
def truncate_telegram_html_safe(html: str, max_len: int) -> str:
    """Truncate a sanitized Telegram HTML string to <= max_len while preserving validity.

    Assumes the input is already sanitized by sanitize_telegram_html().
    """
    try:
        if not isinstance(html, str):
            return ""
        if len(html) <= max_len:
            return html

        def strip_partial_tail(s: str) -> str:
            s = re.sub(r"<[^>]*$", "", s)
            s = re.sub(r"&[^;\s]{0,10}$", "", s)
            return s

        s = html[: max_len - 1] + "…"
        s = strip_partial_tail(s)

        tag_re = re.compile(r"</?([a-zA-Z0-9\-]+)(?:\s[^>]*)?>")
        allowed = set(ALLOWED_TELEGRAM_TAGS)
        stack: list[str] = []
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

        def closers_for_stack(stk: list[str]) -> str:
            return "".join(f"</{t}>" for t in reversed(stk))

        attempts = 0
        while attempts < 5:
            closers = closers_for_stack(stack)
            if len(s) + len(closers) <= max_len:
                s = s + closers
                return s
            over = (len(s) + len(closers)) - max_len
            cut_by = max(over + 1, 8)
            s = s[: max(0, len(s) - cut_by)]
            s = strip_partial_tail(s)
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

        return (s[:max_len]).rstrip()
    except Exception:
        return (str(html)[: max_len]).rstrip()


def prepare_telegram_html(html: str, max_len: int = 4000) -> tuple[str, str]:
    """Return (text, parse_mode) for Telegram HTML send: sanitize + truncate.

    Always returns parse_mode="HTML" on success; falls back to plain text on error.
    """
    try:
        sanitized = sanitize_telegram_html(html or "")
        safe = truncate_telegram_html_safe(sanitized, max_len)
        return safe, "HTML"
    except Exception:
        s = (str(html) or "")
        if len(s) > max_len:
            s = s[: max_len - 1] + "…"
        return s, None


def minify_html_func(html_string: str) -> str:
    """Sanitize and lightly minify HTML without external minifiers.

    Steps:
    - Whitelist sanitizer via clean_html_for_telegraph()
    - Strip HTML comments
    - Collapse inter-tag whitespace (">   <" -> "><")
    - Trim leading/trailing whitespace
    """
    cleaned = clean_html_for_telegraph(html_string)
    # Remove HTML comments
    s = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)
    # Collapse inter-tag whitespace only (preserves spacing inside text nodes)
    s = re.sub(r">\s+<", "><", s)
    return s.strip()
