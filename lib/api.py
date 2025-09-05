"""
Library API for the call subsystem.

Public surface:
- call(name: str, input: str, *, chat_id: int | None = None, thread_id: int | None = None) -> dict
- list(query: str | None = None, include_aliases: bool = False) -> list[dict]

This module intentionally reuses discovery and pipeline utilities from `call/app/call.py` to
avoid duplication. It focuses on a stable facade for the CLI and the Telegram bot.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
import os
import sys
import traceback

# Discovery helpers are centralized in call.lib.discovery to avoid circular imports
from call.lib.discovery import (
    to_pascal_case,
    discover_prompt_repo,
    _ensure_indices,           # private helper; internal use by the lib facade
    _load_agents_index,        # private helper; internal use by the lib facade
    discover_agent_yaml,
)



async def call_async(
    name: str,
    input_text: str,
    *,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    echo: bool = False,
) -> Dict[str, Any]:
    """
    Run the digest pipeline for a given agent name and input text.
    Returns a dict with basic run metadata and the final_output.

    Notes:
    - This will initialize OpenAI client and Telegram bot (so that downstream utils can publish).
    - It sends a short welcome message first, then runs the pipeline.
    - If agent discovery fails, raises ValueError.
    """
    # Lazily import app-layer functions to avoid hard import at module load time
    from call.app import call as app_call

    await app_call.init_openai_client()
    await app_call.init_bot()

    # Discover agent profile path using existing logic
    yaml_path = discover_agent_yaml(name)
    if yaml_path is None:
        raise ValueError(f"Agent '{name}' not found")

    # Align with app/main: set effective targets (falling back to env defaults)
    selected_chat_id = chat_id or app_call.TELEGRAM_CHAT_ID
    selected_thread_id = thread_id or (app_call.TELEGRAM_THREAD_ID or None)
    # Update the app module globals so downstream utils see them
    app_call.selected_chat_id = selected_chat_id
    app_call.selected_thread_id = selected_thread_id

    # Welcome banner (kept concise; lib is the owner of messenger side-effects per SRS)
    try:
        disp_name = to_pascal_case(name) or "Agent"
        welcome = f"<b>🔌 {disp_name}</b>\n<code>{(input_text or '')[:3800]}</code>"
        await app_call.send_telegram_welcome_message(welcome, chat_id=selected_chat_id, message_thread_id=selected_thread_id)
    except Exception:
        # Non-fatal for the run
        pass

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

        agent, history, final_output = await app_call.run_digest_pipeline(
        default_samples_dir,
        agent_path=str(yaml_path),
        user_input=input_text or "",
        debug=False,
        cli_agent_name=name,
        )
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
        "agent": to_pascal_case(name),
        "agent_path": str(yaml_path),
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
    return asyncio.run(call_async(norm, input, chat_id=chat_id, thread_id=thread_id, echo=echo))


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

    canon: Dict[str, str] = {}
    canon.update(scan_canon(repo / 'AgentFab'))
    canon.update(scan_canon(repo / 'agents'))

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

    return {"agents": agents_map, "aliases": aliases_map}


def list(query: Optional[str] = None, include_aliases: bool = False) -> List[Dict[str, Any]]:
    """
    Return list of available agents, with optional query filtering and alias inclusion.
    Each item is a dict: {"name": str, "path": str, "aliases": [str, ...]}.
    """
    data = _read_indices()
    agents_map = data.get("agents", {})
    aliases_map = data.get("aliases", {})

    # Build reverse alias listing per canonical name
    alias_by_agent: Dict[str, List[str]] = {k: [] for k in agents_map.keys()}
    for alias, path in aliases_map.items():
        path_s = str(path)
        # Attempt to map alias to a canonical agent by matching path
        for name, apath in agents_map.items():
            if str(apath) == path_s:
                alias_by_agent[name].append(alias)
                break

    items: List[Dict[str, Any]] = []
    def match(s: str) -> bool:
        if not query:
            return True
        q = query.lower()
        return q in s.lower()

    for name, path in sorted(agents_map.items()):
        if not match(name) and not match(path):
            continue
        entry: Dict[str, Any] = {"name": name, "path": str(path)}
        if include_aliases:
            entry["aliases"] = sorted(alias_by_agent.get(name, []))
        items.append(entry)

    return items
