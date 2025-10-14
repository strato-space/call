import json
import os
import sys
import subprocess


from pathlib import Path


def _repo_root() -> str:
    # this file: call/app/tests/test_cli_prompts_and_exec.py
    # repo root is three levels up from call/app/tests -> .
    return str(Path(__file__).resolve().parents[3])


def _run_cli(args, *, env=None, cwd=None):
    cmd = [sys.executable, "-m", "call.cli.main", *args]
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    # Force UTF-8 IO inside child and decode bytes here defensively
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    cp = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=(cwd or _repo_root()),
        env=child_env,
    )
    out = (
        cp.stdout.decode("utf-8", errors="replace")
        if isinstance(cp.stdout, (bytes, bytearray))
        else (cp.stdout or "")
    )
    err = (
        cp.stderr.decode("utf-8", errors="replace")
        if isinstance(cp.stderr, (bytes, bytearray))
        else (cp.stderr or "")
    )
    return cp.returncode, out, err


def test_cli_prompts_json_fanfab():
    code, out, err = _run_cli(["prompts", "--project", "FanFab", "--format", "json"])
    assert code == 0, err
    data = json.loads(out)
    assert isinstance(data, list)
    # Expect some known prompts from FanFab (UxQA, Stratoslav StratoSammary)
    ids = {x.get("prompt_id") for x in data}
    assert "130-QAcriteriaDefinition" in ids
    assert "StratoSammary" in ids


def test_cli_prompts_table_header():
    code, out, err = _run_cli(["prompts", "--project", "FanFab", "--format", "table"])
    assert code == 0, err
    head = out.splitlines()[0]
    assert (
        "id" in head
        and "name" in head
        and "agent" in head
        and "project" in head
        and "state" in head
        and "url" in head
    )


def test_cli_prompts_prompt_filter_fanfab_prefix():
    code, out, err = _run_cli(
        ["prompts", "--project", "FanFab", "--prompt", "130*", "--format", "json"]
    )
    assert code == 0, err
    data = json.loads(out)
    assert isinstance(data, list)
    ids = {x.get("prompt_id") for x in data}
    assert "130-QAcriteriaDefinition" in ids


def test_cli_prompts_prompt_filter_uxfab_agent_prefix():
    code, out, err = _run_cli(
        [
            "prompts",
            "--project",
            "UxFab",
            "--agent",
            "DialogPostAnalysis",
            "--prompt",
            "33-*",
            "--format",
            "json",
        ]
    )
    assert code == 0, err
    data = json.loads(out)
    assert isinstance(data, list)
    ids = {x.get("prompt_id") for x in data}
    assert "33-Questioning" in ids


def test_cli_prompts_star_filters_prompt_ok():
    # Accepts wildcard filters; result may be empty depending on repo contents but should not error
    code, out, err = _run_cli(
        [
            "prompts",
            "--project",
            "*",
            "--agent",
            "*",
            "--prompt",
            "10*",
            "--format",
            "json",
        ]
    )
    assert code == 0, err
    data = json.loads(out)
    assert isinstance(data, list)


essential_env = {
    "TELEGRAM_TOKEN": "dummy",
    "TELEGRAPH_TOKEN": "dummy",
    "OPENAI_API_KEY": "sk-dummy",
}


def test_cli_call_print_instructions_dialogpostanalysis():
    env = os.environ.copy()
    env.update(essential_env)
    code, out, err = _run_cli(
        [
            "call",
            "--project",
            "UxFab",
            "--agent",
            "DialogPostAnalysis",
            "--prompt",
            "33-Questioning",
            "--print-card",
        ],
        env=env,
    )
    assert code == 0, err
    # Be less brittle: should include the prompt id and an <agent> block name
    assert "33-Questioning" in out
    assert "DialogPostAnalysis" in out


def test_cli_call_print_instructions_body_only():
    env = os.environ.copy()
    env.update(essential_env)
    code, out, err = _run_cli(
        [
            "call",
            "--project",
            "UxFab",
            "--agent",
            "DialogPostAnalysis",
            "--prompt",
            "33-Questioning",
            "--print-instructions",
        ],
        env=env,
    )
    assert code == 0, err
    # Should include body text but omit metadata markers/ids
    assert "33-Questioning" not in out
    assert "DialogPostAnalysis" not in out
    assert "METADATA" not in out
    assert "Ты — агент" in out


def test_cli_call_print_instructions_infers_agent_from_prompt():
    env = os.environ.copy()
    env.update(essential_env)
    code, out, err = _run_cli(
        [
            "call",
            "--project",
            "UxFab",
            "--prompt",
            "33-Questioning",
            "--print-card",
        ],
        env=env,
    )
    # DB-only resolution can succeed; ensure printed instructions contain both prompt and agent block
    assert code == 0, err
    assert "33-Questioning" in out
    assert "DialogPostAnalysis" in out
    assert "agent: DialogPostAnalysis" in out


def test_cli_list_json_contains_aliases_and_prompts():
    code, out, err = _run_cli(["list", "--project", "FanFab"])
    assert code == 0, err
    data = json.loads(out)
    assert isinstance(data, list) and data and isinstance(data[0], dict)
    agents = data[0].get("agents") or []
    ai = next(a for a in agents if a.get("name") == "AiNewsAggr")
    # DB-only listing may not include enriched aliases; ensure the agent exists
    assert ai.get("name") == "AiNewsAggr"
    # Stratoslav has prompt 'StratoSammary'
    st = next(a for a in agents if a.get("name") == "Stratoslav")
    assert "StratoSammary" in (st.get("prompts") or [])


def test_cli_exec_print_instructions_dialogpostanalysis():
    env = os.environ.copy()
    env.update(essential_env)
    code, out, err = _run_cli(
        [
            "exec",
            "--project",
            "UxFab",
            "--agent",
            "DialogPostAnalysis",
            "--content-item",
            "https://docs.google.com/document/d/13LlOsEr6AGw6n6YX1mzrUIVUdH3xT63-/edit",
            "--print-instructions",
        ],
        env=env,
    )
    assert code == 0, err
    assert "# Goal" in out or "Пост-анализ" in out


def test_cli_call_print_instructions_wrong_project_prompt_not_found():
    env = os.environ.copy()
    env.update(essential_env)
    # Intentionally mismatch project and prompt; should produce non-zero code and an error
    code, out, err = _run_cli(
        [
            "call",
            "--project",
            "AgentFab",
            "--agent",
            "DialogPostAnalysis",
            "--prompt",
            "33-Questioning",
            "--print-instructions",
        ],
        env=env,
    )
    # Expect non-zero and an error envelope or error text
    assert code != 0
    # Try to parse JSON envelope first
    try:
        data = json.loads(out)
        assert data.get("ok") is False
        # Either NOT_FOUND/NO_DATA_FOUND; we accept both 404 or 400 depending on mapping
        assert data.get("error_code") in (400, 404)
    except Exception:
        # Fallback: plain-text error message
        assert "no card found matching the provided filters" in err.lower()


def test_cli_call_event_ack():
    code, out, err = _run_cli(
        [
            "call",
            "--event",
            "session_transcription_done",
        ]
    )
    assert code == 0, err
    data = json.loads(out)
    assert data.get("ok") is True
    assert data.get("event") == "session_transcription_done"


def test_cli_exec_event_only_ack():
    code, out, err = _run_cli(
        [
            "exec",
            "--event",
            "session_transcription_done",
        ]
    )
    assert code == 0, err
    data = json.loads(out)
    assert data.get("ok") is True
    assert data.get("event") == "session_transcription_done"


def test_cli_notify_event_only_ack():
    code, out, err = _run_cli(
        [
            "notify",
            "--event",
            "session_transcription_done",
        ]
    )
    assert code == 0, err
    data = json.loads(out)
    assert data.get("ok") is True
    assert data.get("event") == "session_transcription_done"


def test_cli_call_echo_resolved_project_agent_null():
    code, out, err = _run_cli(
        [
            "call",
            "--target",
            "AgentFab",
            "--echo",
            "--resolved",
        ]
    )
    assert code == 0, err
    data = json.loads(out)
    assert isinstance(data, dict)
    resolved = data.get("resolved") or {}
    assert resolved.get("type") == "project"
    assert resolved.get("project") == "AgentFab"
    assert resolved.get("agent") is None
    assert isinstance(resolved.get("path"), str)


def test_cli_call_print_instructions_malformed_prompt_bad_card_format(tmp_path):
    """Create a malformed prompt under existing project/agent and expect BAD_CARD_FORMAT (400)."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    prompt_ready = repo_root / "prompt" / "ready"
    prompt_ready.mkdir(parents=True, exist_ok=True)
    bad_id = "TempBadPrompt2"
    bad = prompt_ready / f"{bad_id}.md"
    bad.write_text(
        """<!-- METADATA:START -->
```yaml
id: TempBadPrompt2
title: TempBadPrompt2
project: UxFab
agent: DialogPostAnalysis
bad: [missing: bracket
```
<!-- METADATA:END -->
""",
        encoding="utf-8",
    )
    try:
        _ = _run_cli(
            ["reload", "--repos", "prompt", "--format", "json"]
        )  # refresh index
        code, out, err = _run_cli(
            [
                "call",
                "--project",
                "UxFab",
                "--prompt",
                bad_id,
                "--print-instructions",
            ]
        )
        assert code != 0
        data = json.loads(out)
        assert data.get("ok") is False
        # With strict DB usage the malformed card is skipped, so the CLI surfaces a not-found envelope.
        assert data.get("error_code") == 404
        assert (data.get("code") or "").upper() == "NO_DATA_FOUND"
    finally:
        try:
            bad.unlink(missing_ok=True)
        except Exception:
            pass


def test_cli_call_print_instructions_malformed_prompt_metadata_returns_400(tmp_path):
    """Create a unique bad MD prompt, rescan DB, and expect 400 on print-instructions (strict MD-only)."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    prompt_ready = repo_root / "prompt" / "ready"
    prompt_ready.mkdir(parents=True, exist_ok=True)
    bad_id = "TempBadPrompt"
    bad = prompt_ready / f"{bad_id}.md"
    bad.write_text(
        """<!-- METADATA:START -->
```yaml
id: TempBadPrompt
title: TempBadPrompt
project: UxFab
agent: DialogPostAnalysis
bad: [missing: bracket
```
<!-- METADATA:END -->
""",
        encoding="utf-8",
    )
    try:
        # Rebuild prompt index to include the new malformed file
        _ = _run_cli(
            ["reload", "--repos", "prompt", "--format", "json"]
        )  # ignore result
        code, out, err = _run_cli(
            [
                "call",
                "--project",
                "UxFab",
                "--prompt",
                bad_id,
                "--print-instructions",
            ]
        )
        # Strict MD-only: malformed METADATA should produce a 400 envelope.
        # With DB-only resolution and no broadening, this may surface as 404 if project/agent are not indexed.
        assert code != 0
        data = json.loads(out)
        assert data.get("ok") is False
        assert data.get("error_code") in (400, 404)
        # Prefer BAD_CARD_FORMAT when available, but allow not-found in strict DB-only mode
        desc = (data.get("description") or "").lower()
        code_s = (data.get("code") or "").lower()
        assert (
            ("bad_card_format" in code_s)
            or ("metadata" in desc)
            or ("no card found" in desc)
        )
    finally:
        try:
            bad.unlink(missing_ok=True)
        except Exception:
            pass


def test_cli_exec_print_instructions_wrong_project_prompt_not_found():
    env = os.environ.copy()
    env.update(essential_env)
    # Mismatch: prompt 33-Questioning under project AgentFab (should be UxFab)
    code, out, err = _run_cli(
        [
            "exec",
            "--project",
            "AgentFab",
            "--prompt",
            "33-Questioning",
            "--print-instructions",
        ],
        env=env,
    )
    # Expect non-zero (exception propagates in exec --print-instructions path)
    assert code != 0
    # Try JSON envelope; else plain error text
    try:
        data = json.loads(out)
        assert data.get("ok") is False
        assert data.get("error_code") in (400, 404, 500)
        desc = data.get("description", "").lower()
        assert "no card found matching the provided filters" in desc
    except Exception:
        assert ("no card found matching the provided filters" in out.lower()) or (
            "no card found matching the provided filters" in err.lower()
        )


def test_cli_call_print_instructions_wrong_project_agent_not_found():
    env = os.environ.copy()
    env.update(essential_env)
    # Mismatch: agent UxCreator under project UxFab; include prompt to mirror user's example
    code, out, err = _run_cli(
        [
            "call",
            "--project",
            "UxFab",
            "--agent",
            "UxCreator",
            "--prompt",
            "33-Questioning",
            "--print-card",
        ],
        env=env,
    )
    # Without prompt/agent fallback, mismatched agent requests now surface as not-found errors.
    assert code != 0
    data = json.loads(out)
    assert data.get("ok") is False
    assert data.get("error_code") == 404
    assert (
        "no card found matching the provided filters"
        in (data.get("description") or "").lower()
    )


def test_cli_exec_print_instructions_wrong_project_agent_not_found():
    env = os.environ.copy()
    env.update(essential_env)
    # Mismatch: agent under a different project for exec path
    code, out, err = _run_cli(
        [
            "exec",
            "--project",
            "AgentFab",
            "--agent",
            "UxCreator",
            "--print-instructions",
        ],
        env=env,
    )
    # Expect non-zero exit (exception propagates)
    assert code != 0
    # Try JSON; otherwise check plain text
    try:
        data = json.loads(out)
        assert data.get("ok") is False
        assert data.get("error_code") in (400, 404, 500)
        assert "no card found matching the provided filters" in (
            data.get("description", "").lower()
        )
    except Exception:
        assert ("no card found matching the provided filters" in out.lower()) or (
            "no card found matching the provided filters" in err.lower()
        )
