"""
Library API for the call subsystem.

Public surface (keyword-only):
- call(*, project: str | None, agent: str | None, prompt: str | None = None, input: str | None = None, chat_id: int | None = None, thread_id: int | None = None, echo: bool = False, debug: bool = False) -> dict
- call_async(*, project: str | None, agent: str | None, prompt: str | None = None, input: str | None = None, chat_id: int | None = None, thread_id: int | None = None, echo: bool = False, debug: bool = False) -> dict
- list(*, project: str | None = None, agent: str | None = None, prompt: str | None = None) -> list[dict]  # hierarchical
- resolve_agent(*, project: str | None = None, agent: str | None = None, prompt: str | None = None) -> dict

Behavior:
- Success: { ok: true, agent, agent_path, final_output, echo, resolved }
- Failure: { ok: false, error_code: <int>, description: <str>, code?: <str>, options?: [...], agent, project, final_output: null, echo }
  Codes: INTERNAL_ERROR, NOT_FOUND, NO_DATA_FOUND, TOO_MANY_ROWS, PIPELINE_ERROR

This module reuses discovery and pipeline utilities from `call/app/call.py`. It focuses on a
stable facade for the CLI and Telegram bot, including selection, wildcard filtering, and structured
errors suitable for LLM consumption.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
import builtins as _builtins
import os
import sys
import traceback
import sqlite3

# Discovery helpers are centralized in call.lib.discovery to avoid circular imports
from call.lib.discovery import (
    discover_prompt_repo,
    _ensure_indices,           # private helper; internal use by the lib facade
    _load_agents_index,        # private helper; internal use by the lib facade
    discover_agent_yaml,
    load_yaml,
)



def _error_payload(
    agent: str,
    input: str,
    exc: BaseException | str,
    *,
    status: int | None = None,
    echo: bool = False,
    debug: bool = False,
    code: Optional[str] = None,
    options: Optional[List[Dict[str, Any]]] = None,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a consistent error payload for API/CLI/Bot.

    Shape:
    { ok: false, error_code: <int>, description: <str>, code?: <str>, options?: [..], agent, final_output: null, echo }
    """
    try:
        msg = str(exc)
    except Exception:
        msg = ""
    # Heuristic mapping for Not Found
    if status is None and isinstance(exc, (KeyError, FileNotFoundError, ValueError)) and "not found" in msg.lower():
        status = 404
    payload: Dict[str, Any] = {
        "ok": False,
        "error_code": int(status or 400),
        "description": msg,
        "agent": agent,
        "project": (project or ""),
        "final_output": None,
        "echo": bool(echo),
    }
    if code:
        payload["code"] = code
    if options is not None:
        payload["options"] = options

    # Optional debug details (file/line/stack) for CLI usage
    try:
        debug_enabled = bool(debug) or str(os.environ.get("CALL_DEBUG", "")).lower() in ("1", "true", "yes", "on")
    except Exception:
        debug_enabled = bool(debug)
    if debug_enabled:
        try:
            import traceback
            tb = exc.__traceback__
            frames = traceback.extract_tb(tb) if tb is not None else []
            stack_items: List[Dict[str, Any]] = []
            for fr in frames[-20:]:
                stack_items.append({
                    "file": fr.filename,
                    "line": fr.lineno,
                    "function": fr.name,
                    "code": fr.line,
                })
            top_file = stack_items[-1]["file"] if stack_items else None
            top_line = stack_items[-1]["line"] if stack_items else None
            payload["debug"] = {
                "file": top_file,
                "line": top_line,
                "stack": stack_items,
            }
        except Exception:
            # best-effort only
            pass

    return payload


async def call_async(
    *,
    project: Optional[str],
    agent: Optional[str],
    prompt: Optional[str] = None,
    input: Optional[str] = None,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    echo: bool = False,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Run the digest pipeline for a given agent name and input text.
    Returns a dict with basic run metadata and the final_output.

    Policy (2025-09-12): name may be empty/None. In that case, we skip agent
    discovery and construct an Agent with empty instructions, using only the user input.

    Notes:
    - This will initialize the Telegram bot (so that downstream utils can publish).
    - No explicit welcome message is sent here to avoid duplicates; the app pipeline will send a single digest.
    - If agent discovery fails (when name is provided), returns 404 error envelope.
    """
    # Lazily import app-layer functions to avoid hard import at module load time
    from call.app import call as app_call

    # Initialize bot: use provided project or default to StratoSpaceAi when not set
    try:
        eff_project = (project or "").strip() or "StratoSpaceAi"
        await app_call.init_bot(project_name=eff_project)
    except Exception as _e:
        # If bot init fails, continue; downstream may still function without telegram
        pass

    # Resolve agent selection first
    resolved_env = resolve_agent(project=project, agent=agent, prompt=prompt)
    if not resolved_env.get("ok"):
        # Error envelope already prepared
        return resolved_env
    resolved = resolved_env.get("resolved") or {}
    chosen_name = resolved.get("name")
    chosen_project = resolved.get("project") or (project or "")
    yaml_path = resolved.get("path")

    # Align with app/main: set effective targets (falling back to env defaults)
    selected_chat_id = chat_id or app_call.TELEGRAM_CHAT_ID
    selected_thread_id = thread_id or (app_call.TELEGRAM_THREAD_ID or None)
    # Update the app module globals so downstream utils see them
    app_call.selected_chat_id = selected_chat_id
    app_call.selected_thread_id = selected_thread_id

    # No welcome banner here (avoid duplicate messages). The pipeline will emit a single digest.

    # Use default samples dir from discovery module to avoid importing app layer here
    from call.lib.discovery import default_samples_dir

    # Optionally enable periodic asyncio tasks dump (for diagnosing long waits)
    dump_period_s = 0
    try:
        dump_period_s = int(os.environ.get("CALL_DUMP_TASKS_EVERY", "0") or "0")
    except Exception:
        dump_period_s = 0
    dump_file_path = os.environ.get("CALL_DUMP_TASKS_FILE", "")
    dump_fp = None

    async def _dump_tasks_periodically(period: int):
        # Delay once to let the run start
        await asyncio.sleep(period)
        while True:
            try:
                out = dump_fp if dump_fp is not None else sys.stderr
                # Gate stderr dumps behind CALL_DEBUG to reduce noise in production
                if dump_fp is None:
                    try:
                        enabled = str(os.environ.get("CALL_DEBUG", "")).strip().lower() in ("1", "true", "yes", "on")
                    except Exception:
                        enabled = False
                    if not enabled:
                        await asyncio.sleep(period)
                        continue
                print("\n=== asyncio tasks dump ===", file=out)
                for t in asyncio.all_tasks():
                    if t is asyncio.current_task():
                        continue
                    print(f"Task: {t!r}", file=out)
                    for fr in t.get_stack(limit=20):
                        traceback.print_stack(f=fr, file=out)
                    print("---", file=out)
                print("=== end ===\n", file=out)
                if dump_fp is not None:
                    try:
                        dump_fp.flush()
                    except Exception:
                        pass
            except Exception:
                pass
            await asyncio.sleep(period)

    dump_task = None
    try:
        if dump_period_s > 0:
            if dump_file_path:
                try:
                    dump_fp = open(dump_file_path, "a", encoding="utf-8", buffering=1)
                except Exception:
                    dump_fp = None
            dump_task = asyncio.create_task(_dump_tasks_periodically(dump_period_s))

        try:
            agent_obj, history, final_output = await app_call.run_digest_pipeline(
                default_samples_dir,
                user_input=(input or ""),
                cli_agent_name=(chosen_name if isinstance(chosen_name, str) else ""),
                prompt_override=(prompt or None),
                project_name=(project or None),
            )
        except Exception as e:
            # Convert pipeline errors to structured error
            return _error_payload(agent=(chosen_name or ""), input=(input or ""), exc=e, status=500, echo=echo, debug=debug, code="PIPELINE_ERROR", project=chosen_project)
    finally:
        if dump_task is not None:
            try:
                dump_task.cancel()
            except Exception:
                pass
        if dump_fp is not None:
            try:
                dump_fp.close()
            except Exception:
                pass

    return {
        "ok": True,
        "agent": (chosen_name if isinstance(chosen_name, str) else ""),
        "agent_path": (str(yaml_path) if yaml_path else None),
        "final_output": final_output,
        # echo flag included for callers that want to inspect behavior upstream
        "echo": bool(echo),
        "resolved": resolved,
    }


def call(
    *,
    project: Optional[str],
    agent: Optional[str],
    prompt: Optional[str] = None,
    input: Optional[str] = None,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    echo: bool = False,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Thin sync wrapper over call_async. All selection and error handling is in call_async.
    """
    try:
        return asyncio.run(
            call_async(
                project=project,
                agent=agent,
                prompt=prompt,
                input=input,
                chat_id=chat_id,
                thread_id=thread_id,
                echo=echo,
                debug=debug,
            )
        )
    except Exception as e:
        return _error_payload(agent or "", input or "", e, status=500, echo=echo, debug=debug, code="INTERNAL_ERROR", project=project)


def _load_projects_index() -> list[str]:
    """Return list of project names from prompt/projects.yaml (exact, case-sensitive)."""
    repo = discover_prompt_repo()
    index = repo / 'projects.yaml'
    try:
        data = load_yaml(index) if index.exists() else {"projects": {}}
    except Exception:
        data = {"projects": {}}
    pr = data.get("projects") or {}
    if isinstance(pr, dict):
        return _builtins.list(pr.keys())
    return []

def _scan_project_agents(project_dir) -> list[dict]:
    """Scan a project directory for agents and extract aliases and prompts from agent.yaml."""
    from pathlib import Path as _Path
    out: list[dict] = []
    if not _Path(project_dir).exists():
        return out
    # 0) Prefer new unified project.yaml schema when present
    try:
        proj_yaml = _Path(project_dir) / 'project.yaml'
        if proj_yaml.exists():
            try:
                y = load_yaml(proj_yaml) or {}
            except Exception:
                y = {}
            # Root project agent
            root_block = {}
            if isinstance(y.get('project'), dict):
                root_block = y.get('project') or {}
            name = str(root_block.get('name') or y.get('name') or _Path(project_dir).name)
            # aliases may be at top-level, under project, or under root
            aliases_val = root_block.get('aliases', y.get('aliases', []))
            aliases = [str(a).strip() for a in aliases_val or []] if isinstance(aliases_val, _builtins.list) else []
            # prompts may be mapping or list; accept both
            prompts_val = root_block.get('prompts', y.get('prompts', {}))
            if isinstance(prompts_val, dict):
                prompts_list = [str(k) for k in prompts_val.keys()]
            elif isinstance(prompts_val, _builtins.list):
                prompts_list = [str(k) for k in prompts_val]
            else:
                prompts_list = []
            out.append({
                "type": "agent",
                "id": "",
                "name": name,
                "aliases": aliases,
                "prompts": prompts_list,
                "path": str(proj_yaml),
            })
            # Agents section: dict of name -> (desc | {aliases, prompts, desc})
            agents_section = root_block.get('agents', y.get('agents', {}))
            if isinstance(agents_section, dict):
                for nm, spec in agents_section.items():
                    ag_name = str(nm)
                    ag_aliases: list[str] = []
                    ag_prompts: list[str] = []
                    if isinstance(spec, dict):
                        av = spec.get('aliases', [])
                        if isinstance(av, _builtins.list):
                            ag_aliases = [str(a).strip() for a in av if str(a).strip()]
                        pv = spec.get('prompts', {})
                        if isinstance(pv, dict):
                            ag_prompts = [str(k) for k in pv.keys()]
                        elif isinstance(pv, _builtins.list):
                            ag_prompts = [str(k) for k in pv]
                    # Resolve path: prefer subdir/agent.yaml when present; else project.yaml as definition source
                    ay = _Path(project_dir) / ag_name / 'agent.yaml'
                    path_str = str(ay) if ay.exists() else str(proj_yaml)
                    out.append({
                        "type": "agent",
                        "id": "",
                        "name": ag_name,
                        "aliases": ag_aliases,
                        "prompts": ag_prompts,
                        "path": path_str,
                    })
            return out
    except Exception:
        # best-effort; fall back to legacy layout
        pass
    # 1) Legacy: include root project agent if present (e.g., AgentFab/agent.yaml)
    try:
        root_ay = _Path(project_dir) / 'agent.yaml'
        if root_ay.exists():
            try:
                y = load_yaml(root_ay) or {}
            except Exception:
                y = {}
            name = _Path(project_dir).name
            try:
                id_or_name = y.get('id') or y.get('name')
                if isinstance(id_or_name, str) and id_or_name.strip():
                    name = id_or_name.strip()
            except Exception:
                pass
            aliases: list[str] = []
            raw_aliases = y.get('aliases') or []
            if isinstance(raw_aliases, _builtins.list):
                aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]
            prompts_list: list[str] = []
            raw_prompts = y.get('prompts') or {}
            if isinstance(raw_prompts, dict):
                prompts_list = [str(k) for k in raw_prompts.keys()]
            out.append({
                "type": "agent",
                "id": "",
                "name": name,
                "aliases": aliases,
                "prompts": prompts_list,
                "path": str(root_ay),
            })
    except Exception:
        # best-effort only
        pass
    for child in _Path(project_dir).iterdir():
        if not child.is_dir():
            continue
        ay = child / 'agent.yaml'
        if not ay.exists():
            continue
        try:
            y = load_yaml(ay) or {}
        except Exception:
            y = {}
        name = child.name
        try:
            id_or_name = y.get('id') or y.get('name')
            if isinstance(id_or_name, str) and id_or_name.strip():
                name = id_or_name.strip()
        except Exception:
            pass
        aliases: list[str] = []
        raw_aliases = y.get('aliases') or []
        if isinstance(raw_aliases, _builtins.list):
            aliases = [str(a).strip() for a in raw_aliases if str(a).strip()]
        prompts_list: list[str] = []
        raw_prompts = y.get('prompts') or {}
        if isinstance(raw_prompts, dict):
            prompts_list = [str(k) for k in raw_prompts.keys()]
        out.append({
            "type": "agent",
            "id": "",
            "name": name,
            "aliases": aliases,
            "prompts": prompts_list,
            "path": str(ay),
        })
    return out


def list(*, project: Optional[str] = None, agent: Optional[str] = None, prompt: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Return hierarchical structure of projects and agents with prompts/aliases.

    - If project is None or empty: return all projects as a list of { name, type:"project", agents:[...] }.
    - If project provided: return a single-element list with that project's structure if found (filtered by agent/prompt patterns when provided).
    - Supports wildcard '*' in project/agent/prompt (treated as '.*', case-insensitive).
    - Removes legacy 'query' and 'include_aliases'. Aliases are always included from agent.yaml when present.
    """
    repo = discover_prompt_repo()

    # Prepare matchers
    import re as _re
    def _compile(pat: Optional[str]):
        if not pat:
            return None
        s = str(pat)
        return _re.compile("^" + _re.escape(s).replace("\\*", ".*") + "$", _re.IGNORECASE)

    m_proj = _compile(project)
    m_agent = _compile(agent)
    m_prompt = _compile(prompt)

    projects = _load_projects_index()
    result: list[dict] = []
    for proj_name in projects:
        if m_proj and not m_proj.match(proj_name):
            continue
        agents = _scan_project_agents(repo / proj_name)
        # Apply agent filter
        if m_agent:
            agents = [a for a in agents if m_agent.match(a.get('name', '')) or any(m_agent.match(al) for al in (a.get('aliases') or []))]
        # Apply prompt filter
        if m_prompt:
            agents = [a for a in agents if any(m_prompt.match(pr) for pr in (a.get('prompts') or []))]
        result.append({
            "name": proj_name,
            "type": "project",
            "agents": agents,
        })

    # If a specific project name without wildcard was provided and not found, return empty list
    if project and not ("*" in project):
        result = [r for r in result if r.get("name") == project]

    return result


def resolve_agent(*, project: Optional[str] = None, agent: Optional[str] = None, prompt: Optional[str] = None) -> Dict[str, Any]:
    """Resolve a single agent using list() filters.

    Returns on success:
      { ok: true, resolved: { project, name, path, aliases, prompts } }

    On error/ambiguity, returns _error_payload with code and optional options.
    """
    try:
        projects = list(project=project, agent=agent, prompt=prompt)
    except Exception as e:
        return _error_payload(agent=(agent or ""), input="", exc=e, status=500, code="INTERNAL_ERROR", project=project)

    matches: list[dict] = []
    for pr in (projects or []):
        for a in pr.get("agents", []) or []:
            matches.append({"project": pr.get("name"), **a})

    if not matches:
        return _error_payload(agent=(agent or ""), input="", exc="no data found", status=404, code="NO_DATA_FOUND", project=project, options=[])
    if len(matches) > 1:
        return _error_payload(agent=(agent or ""), input="", exc="Multiple agents matched your criteria", status=400, code="TOO_MANY_ROWS", project=project, options=matches)

    m = matches[0]
    return {"ok": True, "resolved": {"project": m.get("project"), "name": m.get("name"), "path": m.get("path"), "aliases": m.get("aliases"), "prompts": m.get("prompts")}}


async def clear_session(name: Optional[str], *, chat_id: Optional[int], thread_id: Optional[int]) -> Dict[str, Any]:
    """Clear conversation session(s) for this chat/thread from SQLite.

    Rules:
    - If `name` is given: delete only that exact session id.
    - If `name` is empty/None: delete all sessions for this chat/thread by pattern.

    Session id format: "AgentName:chat" or "AgentName:chat:thread".
    We operate on two tables if present: messages(session_id) and sessions(id).
    """

    # Validate inputs
    if not chat_id:
        return {"ok": False, "error_code": 400, "description": "chat_id is required"}

    def _sid(agent: str, chat: int, thread: Optional[int]) -> str:
        agent_raw = (agent or "").strip() if agent else ""
        return f"{agent_raw}:{chat}:{thread}" if thread is not None else f"{agent_raw}:{chat}"

    db_path = os.getenv("CALL_DB", "call/call.db")
    cleared: List[str] = []
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Detect existing tables once
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        has_messages = bool(cur.fetchone())
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
        has_sessions = bool(cur.fetchone())

        # Build candidate session ids
        sids: List[str] = []
        if isinstance(name, str) and name.strip():
            sids = [_sid(name, int(chat_id), thread_id)]
        else:
            # Pattern-based lookup in sessions/messages tables
            pattern = f":{int(chat_id)}:{int(thread_id)}" if thread_id is not None else f":{int(chat_id)}"
            if has_sessions:
                cur.execute("SELECT id FROM sessions WHERE id LIKE ?", (f"%{pattern}",))
                sids += [row[0] for row in cur.fetchall()]
            if not sids and has_messages:
                cur.execute("SELECT DISTINCT session_id FROM messages WHERE session_id LIKE ?", (f"%{pattern}",))
                sids += [row[0] for row in cur.fetchall()]

        if not sids:
            cur.close(); conn.close()
            return {"ok": True, "cleared": []}

        # Deduplicate and delete
        for sid in sorted(set(sids)):
            try:
                if has_messages:
                    cur.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
                if has_sessions:
                    cur.execute("DELETE FROM sessions WHERE id = ?", (sid,))
                conn.commit()
                cleared.append(sid)
            except Exception:
                conn.rollback()
                continue

        cur.close()
        conn.close()
    except Exception as e:
        return {
            "ok": False,
            "error_code": 500,
            "description": f"clear_session failed: {e}",
            "error_type": type(e).__name__,
        }

    return {"ok": True, "cleared": cleared}
