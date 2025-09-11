from __future__ import annotations

from bs4 import BeautifulSoup
import re

__all__ = [
    "clean_html_for_telegram",
    "clean_html_for_telegraph",
    "minify_html_func",
]


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


def clean_html_for_telegram(html_content: str) -> str:
    """Sanitize HTML for Telegram parse_mode=HTML.

    Telegram supports a limited subset of tags. This function:
    - Converts <ul>/<ol>/<li> into plain-text bullet or numbered lines.
    - Unwraps unsupported tags while preserving text.
    - Strips disallowed attributes, keeping only href on <a>.
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

    # Normalize headings: convert <h1>.. <h6> into <b>Title</b><br>
    # Telegram HTML does not support heading tags.
    for level in ("h1", "h2", "h3", "h4", "h5", "h6"):
        for h in list(soup.find_all(level)):
            bold = soup.new_tag("b")
            bold.string = h.get_text(strip=False)
            br = soup.new_tag("br")
            h.replace_with(bold)
            bold.insert_after(br)

    # Convert Telegram spoilers: <span class="tg-spoiler"> -> <tg-spoiler>
    for sp in list(soup.find_all("span", class_="tg-spoiler")):
        tg = soup.new_tag("tg-spoiler")
        tg.string = sp.get_text(strip=False)
        sp.replace_with(tg)

    # Replace <hr> with two line breaks
    for hr in list(soup.find_all("hr")):
        br1 = soup.new_tag("br")
        br2 = soup.new_tag("br")
        hr.replace_with(br1)
        br1.insert_after(br2)

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

    # Allowed tags for Telegram HTML (per Bot API):
    # a, b/strong, i/em, u/ins, s/strike/del, code, pre, blockquote, br, tg-spoiler
    allowed_tags = {"a", "b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote", "br", "tg-spoiler"}

    # Unwrap unsupported tags
    for tag in list(soup.find_all(True)):
        if tag.name not in allowed_tags:
            tag.unwrap()

    # Strip attributes, keep only href for <a> (no attrs for tg-spoiler)
    for tag in soup.find_all(True):
        allowed_attrs = {}
        if tag.name == "a" and tag.has_attr("href"):
            allowed_attrs["href"] = tag["href"]
        tag.attrs = allowed_attrs

    cleaned = str(soup)

    # Fix self-closing tags that Telegram doesn't like
    cleaned = re.sub(r'<(\w+)/>', r'<\1>', cleaned)
    cleaned = re.sub(r'<(\w+)\s+/>', r'<\1>', cleaned)

    return cleaned.strip()


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
