import sqlite3
from pathlib import Path

import pytest

from call.lib import repo_db, repo_fs


@pytest.mark.parametrize("project_has_metadata", [False, True])
def test_reload_stores_card_text_and_skips_placeholder_prompts(tmp_path, monkeypatch, project_has_metadata):
    """Reloading the repo should persist full card text and avoid placeholder prompt rows."""

    # Build isolated agent/prompt repos in the temporary directory
    agent_root = tmp_path / "agent"
    prompt_root = tmp_path / "prompt"
    agent_root.mkdir()
    prompt_ready = prompt_root / "ready"
    prompt_ready.mkdir(parents=True)

    project_dir = agent_root / "ProjectX"
    project_dir.mkdir()

    project_md = project_dir / "project.md"
    if project_has_metadata:
        project_md.write_text(
            """<!-- METADATA:START -->\n```yaml\nengine: test\n```\n<!-- METADATA:END -->\n# Project Card\n""",
            encoding="utf-8",
        )
    else:
        project_md.write_text("Project card without metadata", encoding="utf-8")

    agent_dir = project_dir / "MainAgent"
    agent_dir.mkdir()
    agent_md = agent_dir / "agent.md"
    agent_md.write_text(
        "Agent card without metadata\nprompts:\n  - PhantomPrompt\n",
        encoding="utf-8",
    )

    prompt_md = prompt_ready / "PromptReal.md"
    prompt_md.write_text("Prompt card without metadata", encoding="utf-8")

    # Redirect repo discovery to the temporary fixtures
    monkeypatch.setattr(repo_fs, "discover_agent_repo", lambda: agent_root)
    monkeypatch.setattr(repo_fs, "discover_prompt_repo", lambda: prompt_root)

    # Use an isolated sqlite database for this test run
    db_path = tmp_path / "repo.db"
    monkeypatch.setattr(repo_db, "DB_PATH", str(db_path))
    monkeypatch.setattr(repo_fs.repo_db, "DB_PATH", str(db_path))

    # Execute a reload to populate the database from the temporary repos
    result = repo_fs.reload(repos=["agent", "prompt"], full_form=False)
    assert result["ok"] is True

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT project, agent, prompt, card FROM repo").fetchall()
    finally:
        conn.close()

    records = {(proj or "", agent or "", prompt or ""): card for proj, agent, prompt, card in rows}

    # Project row should contain the project card content (regardless of metadata)
    assert ("ProjectX", "", "") in records
    assert "Project" in records[("ProjectX", "", "")]

    # Agent row should contain the literal agent.md text (not a path)
    agent_key = ("ProjectX", "MainAgent", "")
    assert agent_key in records
    assert "Agent card without metadata" in records[agent_key]

    # Prompt row from ready/ should be present with its card content
    prompt_keys = [key for key in records if key[2] == "PromptReal"]
    assert len(prompt_keys) == 1
    assert "Prompt card without metadata" in records[prompt_keys[0]]

    # Ensure no placeholder prompt rows (e.g., PhantomPrompt from metadata-only declaration)
    assert not any(key[2] == "PhantomPrompt" for key in records), records

    # Sanity check: card entries should not be file system paths
    for value in records.values():
        normalized = value.replace("\\", "/")
        assert not normalized.endswith(".md"), normalized
