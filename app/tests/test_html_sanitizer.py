import os
import pytest

from call.app.utils.html_sanitizer import (
    clean_html_for_telegram,
    clean_html_for_telegraph,
    minify_html_func,
)


def test_clean_html_for_telegram_converts_lists_and_strips_attrs():
    html = """
    <div class="wrap">
      <ul id="x"><li>First</li><li>Second</li></ul>
      <p>Click <a href="https://example.com" onclick="evil()">here</a></p>
      <span>Keep <b>bold</b> and <i>italic</i>.</span>
    </div>
    """
    out = clean_html_for_telegram(html)
    # Unsupported wrappers removed; lists converted to paragraph with bullets
    assert "<ul" not in out and "<li" not in out
    assert "• First" in out and "• Second" in out
    # Only href should remain on links
    assert "onclick" not in out and "href=\"https://example.com\"" in out


def test_clean_html_for_telegraph_unwraps_headings_and_disallowed_tags():
    html = """
    <html><body>
      <h2>Title</h2>
      <section><small>tiny</small> <div>content</div></section>
      <hr/>
    </body></html>
    """
    out = clean_html_for_telegraph(html)
    # No document wrappers remain
    assert "<html" not in out and "<body" not in out
    # Headings transformed into strong inside paragraph
    assert "<p><strong>Title</strong></p>" in out
    # Disallowed tags unwrapped
    assert "<section" not in out and "<div" not in out and "<small" not in out
    # hr replaced by two br elements
    assert out.count("<br/>") >= 1 or out.count("<br>") >= 1


def test_minify_html_func_strips_comments_and_collapses_whitespace():
    html = """
    <!-- comment -->
    <p> Hello </p>  
    <p>World</p>
    """
    out = minify_html_func(html)
    assert "comment" not in out
    # Ensure no inter-tag whitespace like ">   <"
    assert "> <" not in out
    assert out.strip().startswith("<p>")
