from pathlib import Path
import os

from call.app.call import compose_welcome_html


def test_compose_welcome_html_includes_model_and_preview(monkeypatch):
    # Ensure no GitHub link is attempted for this unit test
    monkeypatch.delenv("GITHUB_REMOTE_URL", raising=False)
    monkeypatch.delenv("GITHUB_REMOTE_ORGANIZATION_URL", raising=False)

    html = compose_welcome_html(
        agent_name="FooAgent",
        source_path=None,
        user_input="hello world",
        mcp_servers_started=[],
        vs_list=["vs_abc"],
        model="gpt-5",
    )

    assert "🔌 <b>FooAgent</b>" in html
    assert "hello world" in html
    # mcp is empty in this test, so the section should be omitted
    assert "mcp:" not in html
    assert "vs: ['vs_abc']" in html
    assert "model: gpt-5" in html
