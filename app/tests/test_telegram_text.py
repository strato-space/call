from call.app.utils.telegram_text import (
    telegram_truncate_html_safe,
    telegram_truncate_markdown_safe,
)


def test_telegram_truncate_html_safe_preserves_validity():
    html = "<b>Hello</b> world! " + ("x" * 5000) + "<i>italic</i>"
    out = telegram_truncate_html_safe(html, 200)
    assert len(out) <= 200
    # no dangling partial tags
    assert not out.endswith("<") and not out.endswith("</")
    # All tags must be closed for allowed set
    assert out.count("<b>") == out.count("</b>")
    assert out.count("<i>") == out.count("</i>")


def test_telegram_truncate_markdown_safe_balances_fences():
    md = """
Here is some code:
```
print('hello')
"""  # No closing fence on purpose
    out = telegram_truncate_markdown_safe(md, 80)
    # Ensure fences are balanced
    assert out.count("```") % 2 == 0
    assert len(out) <= 80
