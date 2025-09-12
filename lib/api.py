"""
Library API for the call subsystem.

Public surface:
- call(name: str, input: str, *, chat_id: int | None = None, thread_id: int | None = None, echo: bool = False) -> dict
- list(query: str | None = None, include_aliases: bool = False) -> list[dict]

Behavior:
- On success, returns: { ok: true, agent, agent_path, final_output, echo }
- On operational failure, returns Telegram Bot API–style error envelope:
  { ok: false, error_code: <int>, description: <str>, error_type: <str>, agent, final_output: null, echo }
  * 404 for missing/unknown agent; 500 for internal pipeline errors.

This module intentionally reuses discovery and pipeline utilities from `call/app/call.py` to
avoid duplication. It focuses on a stable facade for the CLI and the Telegram bot.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
import os
import sys
import traceback
import sqlite3

# Discovery helpers are centralized in call.lib.discovery to avoid circular imports
from call.lib.discovery import (
    to_pascal_case,
    discover_prompt_repo,
    _ensure_indices,           # private helper; internal use by the lib facade
    _load_agents_index,        # private helper; internal use by the lib facade
    discover_agent_yaml,
)



def _error_payload(
    name: str,
    input_text: str,
    exc: BaseException,
    *,
    status: int | None = None,
    echo: bool = False,
    debug: bool = False,
) -> Dict[str, Any]:
    """Build a Telegram Bot API–style error payload.

    Shape:
    {"ok": false, "error_code": <int>, "description": <str>, ...}
    Additional fields preserved for our clients: agent, final_output, echo, error_type.
    """
    try:
        msg = str(exc)
    except Exception:
        msg = ""
    err_type = type(exc).__name__
    # Heuristic mapping for Not Found
    if status is None and isinstance(exc, (KeyError, FileNotFoundError, ValueError)) and "not found" in msg.lower():
        status = 404
    payload: Dict[str, Any] = {
        "ok": False,
        "error_code": int(status or 400),
        "description": msg,
        # extra context for our ecosystem
        "error_type": err_type,
        "agent": name,
        "final_output": None,
        "echo": bool(echo),
    }

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
    name: Optional[str],
    input_text: str,
    *,
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

    await app_call.init_bot()

    # Discover agent profile path using existing logic (only when name provided)
    yaml_path = None
    norm_name = (name or "").strip()
    if norm_name:
        try:
            yaml_path = discover_agent_yaml(norm_name)
        except Exception as e:
            # Discovery raised an exception; convert to structured error
            return _error_payload(norm_name, input_text, e, status=404, echo=echo, debug=debug)
        if yaml_path is None:
            return _error_payload(norm_name, input_text, ValueError(f"Agent '{norm_name}' not found"), status=404, echo=echo, debug=debug)

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
            agent, history, final_output = await app_call.run_digest_pipeline(
                default_samples_dir,
                user_input=input_text or "",
                cli_agent_name=norm_name,
            )
        except Exception as e:
            # Convert pipeline errors to structured error
            return _error_payload(norm_name, input_text, e, status=500, echo=echo, debug=debug)
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
        "agent": to_pascal_case(norm_name),
        "agent_path": (str(yaml_path) if yaml_path else None),
        "final_output": final_output,
        # echo flag included for callers that want to inspect behavior upstream
        "echo": bool(echo),
    }


def call(
    name: str,
    input: str,
    *,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    echo: bool = False,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Public sync facade for running an agent. Returns a dict with metadata/final_output.
    Intended for use by CLI and Telegram bot.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name must be a non-empty string")
    if not isinstance(input, str):
        raise ValueError("input must be a string")

    norm = to_pascal_case(name)
    try:
        return asyncio.run(call_async(norm, input, chat_id=chat_id, thread_id=thread_id, echo=echo, debug=debug))
    except Exception as e:
        # Last-resort guard: never explode to callers that expect JSON; provide structured error
        return _error_payload(norm, input, e, status=500, echo=echo, debug=debug)


def _read_indices() -> Dict[str, Dict[str, str]]:
    """
    Load both AgentFab/agents.yaml and agents/agents.yaml indices into a combined mapping
    structure: {"agents": {Name: path}, "aliases": {Alias: path}}.
    Missing files are tolerated.
    """
    repo = discover_prompt_repo()
    _ensure_indices(repo)
    af = _load_agents_index(repo / 'AgentFab' / 'agents.yaml', repo / 'AgentFab')
    ag = _load_agents_index(repo / 'agents' / 'agents.yaml', repo / 'agents')

    # We want to preserve canonical names vs aliases. The _load_* function returns a single
    # name->path map with aliases merged, so we cannot separate after the fact. For /list
    # we will show unique canonical names by scanning directories as fallback if needed.
    # To keep behavior predictable, we will:
    # 1) Build a canonical names set from folder names that contain agent.yaml
    # 2) Build an alias set as (index entries - canonical names)
    from pathlib import Path

    def scan_canon(base: Path) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if base.exists():
            for child in base.iterdir():
                if child.is_dir():
                    y = child / 'agent.yaml'
                    if y.exists():
                        out[to_pascal_case(child.name)] = str(y)
        return out

    canon_af: Dict[str, str] = scan_canon(repo / 'AgentFab')
    canon_ag: Dict[str, str] = scan_canon(repo / 'agents')
    canon: Dict[str, str] = {}
    canon.update(canon_af)
    canon.update(canon_ag)

    # Merge maps for quick lookup
    # Merge and normalize all paths to strings for consistent comparisons/JSON
    merged_raw = {**af, **ag}
    merged: Dict[str, str] = {k: (str(v) if not isinstance(v, str) else v) for k, v in merged_raw.items()}

    # Split into agents (canonical) and aliases (non-canonical keys that still resolve)
    agents_map: Dict[str, str] = {}
    aliases_map: Dict[str, str] = {}
    for k, v in merged.items():
        if k in canon:
            agents_map[k] = v
        else:
            aliases_map[k] = v

    return {"agents": agents_map, "aliases": aliases_map, "agents_af": canon_af, "agents_ag": canon_ag}


def list(query: Optional[str] = None, include_aliases: bool = False, *, grouped: bool = False) -> List[Dict[str, Any]] | Dict[str, List[Dict[str, Any]]]:
    """
    Return list of available agents.

    Policy (2025-09-07): only expose agents from the 'agents' directory; do not
    include AgentFab entries in the default flat list.

    When grouped is False (default): returns a flat list of items, each a dict
    {"name": str, "path": str, "aliases": [str, ...]} from 'agents' only.

    When grouped is True: returns a dict with two lists keyed by registry roots:
      {"AgentFab": [], "agents": [...]} — AgentFab list is intentionally empty.
    """
    data = _read_indices()
    agents_map = data.get("agents", {})
    aliases_map = data.get("aliases", {})
    agents_af = data.get("agents_af", {})
    agents_ag = data.get("agents_ag", {})

    # Build reverse alias listing per canonical name
    alias_by_agent: Dict[str, List[str]] = {k: [] for k in agents_map.keys()}
    for alias, path in aliases_map.items():
        path_s = str(path)
        for name, apath in agents_map.items():
            if str(apath) == path_s:
                alias_by_agent[name].append(alias)
                break

    def match(s: str) -> bool:
        if not query:
            return True
        q = query.lower()
        return q in s.lower()

    def build_items(src: Dict[str, str]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for name, path in sorted(src.items()):
            if not match(name) and not match(path):
                continue
            entry: Dict[str, Any] = {"name": name, "path": str(path)}
            if include_aliases:
                entry["aliases"] = sorted(alias_by_agent.get(name, []))
            items.append(entry)
        return items

    if grouped:
        return {
            "AgentFab": [],  # intentionally empty per policy
            "agents": build_items(agents_ag),
        }

    # Default flat mode exposes only 'agents' entries
    return build_items(agents_ag)


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
        agent_norm = to_pascal_case(agent or "") if agent else ""
        return f"{agent_norm}:{chat}:{thread}" if thread is not None else f"{agent_norm}:{chat}"

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
