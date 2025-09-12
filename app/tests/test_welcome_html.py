from pathlib import Path
import os

from call.app.call import compose_welcome_html


def test_compose_welcome_html_includes_model_and_preview(monkeypatch):
    # Ensure no GitHub link is attempted for this unit test
    monkeypatch.delenv("GITHUB_REMOTE_URL", raising=False)
    monkeypatch.delenv("GITHUB_REMOTE_ORGANIZATION_URL", raising=False)

    html = compose_welcome_html(
        agent_name="FooAgent",
        agent_yaml_path=None,
        user_input="hello world",
        mcp_servers_started=[],
        vs_list=["vs_abc"],
        model="gpt-5",
    )

    assert "<b>FooAgent</b>" in html
    assert "<code>model: gpt-5</code>" in html
    assert "<code>hello world</code>" in html
    assert "<code>mcp: []</code>" in html
    assert "<code>vs: ['vs_abc']</code>" in html
