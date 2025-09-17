import json
import os
import sys
import subprocess


from pathlib import Path


def _repo_root() -> str:
    # this file: call/app/tests/test_cli_prompts_and_exec.py
    # repo root is three levels up from call/app/tests -> c:/home/strato-space
    return str(Path(__file__).resolve().parents[3])


def _run_cli(args, *, env=None, cwd=None):
    cmd = [sys.executable, "-m", "call.cli.main", *args]
    child_env = os.environ.copy()
    if env:
        child_env.update(env)
    # Force UTF-8 IO inside child and decode bytes here defensively
    child_env.setdefault("PYTHONIOENCODING", "utf-8")
    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=(cwd or _repo_root()), env=child_env)
    out = cp.stdout.decode('utf-8', errors='replace') if isinstance(cp.stdout, (bytes, bytearray)) else (cp.stdout or '')
    err = cp.stderr.decode('utf-8', errors='replace') if isinstance(cp.stderr, (bytes, bytearray)) else (cp.stderr or '')
    return cp.returncode, out, err


def test_cli_prompts_json_fanfab():
    code, out, err = _run_cli(["prompts", "--project", "FanFab", "--format", "json"])
    assert code == 0, err
    data = json.loads(out)
    assert isinstance(data, list)
    # Expect some known prompts from FanFab (UxQA, Stratoslav main)
    ids = {x.get("prompt_id") for x in data}
    assert "130-QAcriteriaDefinition" in ids
    assert "main" in ids


def test_cli_prompts_table_header():
    code, out, err = _run_cli(["prompts", "--project", "FanFab", "--format", "table"])
    assert code == 0, err
    head = out.splitlines()[0]
    assert "id" in head and "name" in head and "agent" in head and "project" in head and "state" in head and "url" in head


essential_env = {
    "TELEGRAM_TOKEN": "dummy",
    "TELEGRAPH_TOKEN": "dummy",
    "OPENAI_API_KEY": "sk-dummy",
}


def test_cli_call_print_instructions_dialogpostanalysis():
    env = os.environ.copy()
    env.update(essential_env)
    code, out, err = _run_cli([
        "call",
        "--project", "UxFab",
        "--agent", "DialogPostAnalysis",
        "--prompt", "33-Questioning",
        "--print-instructions",
    ], env=env)
    assert code == 0, err
    assert "Формирует вопросы" in out


def test_cli_list_json_contains_aliases_and_prompts():
    code, out, err = _run_cli(["list", "--project", "FanFab"])
    assert code == 0, err
    data = json.loads(out)
    assert isinstance(data, list) and data and isinstance(data[0], dict)
    agents = data[0].get("agents") or []
    ai = next(a for a in agents if a.get("name") == "AiNewsAggr")
    aliases = ai.get("aliases") or []
    assert set(aliases) >= {"ai-news-aggr", "ai-news", "AI News", "AI News [aggr]", "AI News Aggregator"}
    # Stratoslav has prompt 'main'
    st = next(a for a in agents if a.get("name") == "Stratoslav")
    assert "main" in (st.get("prompts") or [])


def test_cli_exec_print_instructions_dialogpostanalysis():
    env = os.environ.copy()
    env.update(essential_env)
    code, out, err = _run_cli([
        "exec",
        "--project", "UxFab",
        "--agent", "DialogPostAnalysis",
        "--content-item", "https://docs.google.com/document/d/13LlOsEr6AGw6n6YX1mzrUIVUdH3xT63-/edit",
        "--print-instructions",
    ], env=env)
    assert code == 0, err
    assert "DialogPostAnalysis" in out or "Формирует вопросы" in out


def test_cli_exec_tracing_403_error_json():
    env = os.environ.copy()
    env.update(essential_env)
    env["CALL_FAKE_TRACING_403"] = "1"
    code, out, err = _run_cli([
        "exec",
        "--project", "UxFab",
        "--agent", "DialogPostAnalysis",
        "--content-item", "Hello",
    ], env=env)
    # Should return non-zero exit and JSON error envelope
    assert code == 1, out + "\n" + err
    data = json.loads(out)
    assert data.get("ok") is False
    assert data.get("error_code") == 403
    assert data.get("code") == "REQUEST_FORBIDDEN"
    det = data.get("details") or {}
    if isinstance(det, dict):
        inner = det.get("error") or {}
        assert inner.get("type") == "request_forbidden"
