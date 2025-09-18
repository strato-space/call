"""
CLI for the call subsystem.

Command Reference (one-line):
  # List projects/agents/prompts (hierarchical JSON)
  python -m call.cli.main agents [--project UxFab] [--agent Agent*] [--prompt 10*]

  # Call an agent with optional prompt override
  python -m call.cli.main call --agent BusinessAnalyticAgent --input "Analyze Q3" [--project UxFab]
  python -m call.cli.main call --agent BusinessAnalyticAgent --prompt Draft --input "Analyze Q3" [--project UxFab]

Debug flags:
  --trace SECONDS       Dump all thread stacks every N seconds
  --trace-file PATH     Write stack dumps to a file instead of stderr
  --echo                Include echo metadata in response
"""
from __future__ import annotations

import argparse
import json
import sys
import io
import os
import faulthandler

from call.lib import api as call_api
from call.lib import discovery as call_discovery
from call.lib.logging import configure_logging


def cmd_list(args: argparse.Namespace) -> int:
    items = call_api.list(
        project=(args.project or None),
        agent=(args.agent or None),
        prompt=(args.prompt or None),
    )
    _safe_print(json.dumps(items, ensure_ascii=False, indent=2))
    return 0


def _safe_print(s: str) -> None:
    try:
        print(s)
    except UnicodeEncodeError:
        try:
            import sys as _sys
            _sys.stdout.buffer.write(s.encode('utf-8', 'replace'))
            _sys.stdout.buffer.write(b"\n")
        except Exception:
            # last resort: strip non-ascii
            print(s.encode('ascii', 'ignore').decode('ascii'))


def _print_table(rows: list[dict], columns: list[tuple[str, str]]) -> None:
    """Print a simple table to console.

    columns: list of (key, header)
    """
    # Compute column widths
    widths = []
    for key, header in columns:
        w = len(header)
        for r in rows:
            w = max(w, len(str(r.get(key, ''))))
        widths.append(w)
    # Header
    header_cells = []
    for i, (key, header) in enumerate(columns):
        header_cells.append(header.ljust(widths[i]))
    _safe_print(" | ".join(header_cells))
    _safe_print("-+-".join('-' * w for w in widths))
    # Rows
    for r in rows:
        cells = []
        for i, (key, header) in enumerate(columns):
            cells.append(str(r.get(key, '')).ljust(widths[i]))
        _safe_print(" | ".join(cells))


def cmd_call(args: argparse.Namespace) -> int:
    agent = (args.agent or "").lstrip("@") if isinstance(args.agent, str) else (args.agent or None)
    trace_fp = None
    try:
        # Optional: print instructions and exit
        if getattr(args, "print_instructions", False):
            # Normalize selection first and surface consistent error envelopes
            sel = call_api.resolve_agent(project=(args.project or None), agent=(agent or None), prompt=(args.prompt or None))
            if not sel.get("ok"):
                _safe_print(json.dumps(sel, ensure_ascii=False))
                return 1
            try:
                # Build config to get merged instructions
                from call.app.call import build_agent_config
                cfg = call_api.asyncio.run(build_agent_config(agent, prompt_override=(args.prompt or None), project_name=(args.project or None), merge=not bool(getattr(args, "no_merge", False))))  # type: ignore[attr-defined]
                _safe_print(cfg.instructions or "")
                return 0
            except Exception as e:
                # Uniform error envelope with error_code for this path
                msg = str(e)
                status = 404 if "not found" in msg.lower() else 400
                err = {
                    "ok": False,
                    "error_code": status,
                    "description": msg,
                    "agent": agent or "",
                    "project": (args.project or ""),
                    "final_output": None,
                    "echo": bool(getattr(args, "echo", False)),
                }
                _safe_print(json.dumps(err, ensure_ascii=False))
                return 1

        # Optional periodic stack dumps for debugging long runs
        if getattr(args, "trace", 0):
            delay = max(1, int(args.trace))
            # Choose output target
            target = None
            if getattr(args, "trace_file", None):
                path = os.fspath(args.trace_file)
                # Open in append mode to keep prior dumps
                trace_fp = open(path, "a", encoding="utf-8", buffering=1)
                target = trace_fp
            else:
                target = sys.stderr
            # Enable and schedule repeating dumps
            try:
                faulthandler.enable(file=target)
            except Exception:
                # Ignore if already enabled
                pass
            faulthandler.dump_traceback_later(delay, repeat=True, file=target)

        result = call_api.call(
            project=(args.project or None),
            agent=agent or None,
            prompt=(args.prompt or None),
            target=(args.target or None) if hasattr(args, "target") else None,
            input=(args.input or None),
            session_id=((args.session_id or None) if hasattr(args, "session_id") else None),
            echo=bool(getattr(args, "echo", False)),
            merge=not bool(getattr(args, "no_merge", False)),
        )
        _safe_print(json.dumps(result, ensure_ascii=False))
        return 0 if (isinstance(result, dict) and result.get("ok")) else 1
    except Exception as e:
        err = {"ok": False, "error": {"type": type(e).__name__, "message": str(e)}}
        print(json.dumps(err, ensure_ascii=False))
        return 1
    finally:
        # Cancel periodic dumps and close file if opened
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass
        if trace_fp:
            try:
                trace_fp.close()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="call — CLI for listing and invoking agents (keyword-only API)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_agents = sub.add_parser("agents", aliases=["list"], help="List projects and agents (hierarchical)")
    p_agents.add_argument("--project", default="", help="Project filter (supports * wildcard)")
    p_agents.add_argument("--agent", default="", help="Agent filter (supports * and aliases)")
    p_agents.add_argument("--prompt", default="", help="Prompt filter (supports *)")
    p_agents.set_defaults(func=cmd_list)

    p_call = sub.add_parser("call", help="Call an agent with input text")
    p_call.add_argument("--project", default="", help="Project name (exact or with * wildcard)")
    p_call.add_argument("--agent", default="", help="Agent name or @Alias (exact or with * wildcard)")
    p_call.add_argument("--prompt", default="", help="Prompt override (exact or with * for selection)")
    p_call.add_argument("--target", default="", help="Unified selector (project|agent|prompt pattern)")
    p_call.add_argument("--input", default="", help="Input text for the agent")
    p_call.add_argument("--session-id", default="", help="Override session id (format: AgentName:chat or AgentName:chat:thread)")
    p_call.add_argument("--echo", action="store_true", help="Return additional echo metadata from the run")
    p_call.add_argument("--print-instructions", action="store_true", help="Print the merged instructions for the selection and exit")
    p_call.add_argument("--no-merge", dest="no_merge", action="store_true", help="Disable attribute/instructions merge (use prompt/agent/project only)")
    p_call.add_argument("--trace", type=int, default=0, metavar="SECONDS", help="Dump all thread stacks every N seconds (debug)")
    p_call.add_argument("--trace-file", type=str, default="", help="Write stack dumps to a file instead of stderr")
    p_call.set_defaults(func=cmd_call)

    # prompts subcommand
    def cmd_prompts(args: argparse.Namespace) -> int:
        items = call_discovery.prompts(project=(args.project or None), agent=(args.agent or None), prompt=(args.prompt or None), state=(args.state or None))
        if (args.format or 'table').lower() == 'json':
            _safe_print(json.dumps(items, ensure_ascii=False, indent=2))
            return 0
        # table view
        cols = [
            ("prompt_id", "id"),
            ("name", "name"),
            ("agent", "agent"),
            ("project", "project"),
            ("state", "state"),
            ("url", "url"),
        ]
        _print_table(items, cols)
        return 0

    p_prompts = sub.add_parser("prompts", help="List prompts (flat)")
    p_prompts.add_argument("--project", default="", help="Filter by project")
    p_prompts.add_argument("--agent", default="", help="Filter by agent")
    p_prompts.add_argument("--prompt", default="", help="Filter by prompt id or name (supports *)")
    p_prompts.add_argument("--state", default="", help="Filter by state (draft|ready)")
    p_prompts.add_argument("--format", default="table", choices=["table", "json"], help="Output format")
    p_prompts.set_defaults(func=cmd_prompts)

    # exec subcommand
    def _parse_content_item(raw: str) -> dict:
        raw = raw.strip()
        # JSON form
        if raw.startswith('{') and raw.endswith('}'):
            try:
                return json.loads(raw)
            except Exception:
                pass
        # URL or text heuristic
        import re as _re
        if _re.match(r"^https?://", raw):
            item = {"type": "url", "url": raw}
        else:
            item = {"type": "text", "text": raw}
        # Extract Google Docs id for known hosts
        try:
            url = item.get("url")
            if url:
                # docs.google.com/document/d/<id>/ or .../spreadsheets/d/<id>/
                m = _re.search(r"/d/([A-Za-z0-9_-]{10,})", url)
                if m:
                    item["source"] = {"type": "file", "file_id": m.group(1)}
        except Exception:
            pass
        return item

    def cmd_exec(args: argparse.Namespace) -> int:
        # Build input payload
        payload: dict = {}
        if args.prompt and args.agent:
            print(json.dumps({"ok": False, "error": "Specify only one of --prompt or --agent"}, ensure_ascii=False))
            return 1
        if args.prompt:
            payload["prompt"] = args.prompt
        if args.agent:
            payload["agent"] = args.agent
        ctx: list = []
        for ci in (args.content_item or []):
            try:
                ctx.append(_parse_content_item(ci))
            except Exception:
                continue
        if ctx:
            payload["context"] = ctx
        if args.output_type:
            payload["output-type"] = args.output_type

        # Optional: print instructions only
        if getattr(args, "print_instructions", False):
            try:
                # Build config to get merged instructions
                from call.app.call import build_agent_config
                name = args.agent or ""
                cfg = call_api.asyncio.run(build_agent_config(name, prompt_override=(args.prompt or None), project_name=(args.project or None), merge=True))  # type: ignore[attr-defined]
            except Exception:
                import asyncio as _asyncio
                async def _go():
                    from call.app.call import build_agent_config
                    return await build_agent_config(args.agent or "", prompt_override=(args.prompt or None), project_name=(args.project or None), merge=True)
                cfg = _asyncio.run(_go())
            _safe_print(cfg.instructions or "")
            return 0

        # Execute via call API, passing payload as input JSON string
        result = call_api.call(
            project=(args.project or None),
            agent=(args.agent or None),
            prompt=(args.prompt or None),
            input=json.dumps(payload, ensure_ascii=False),
            session_id=((args.session_id or None) if hasattr(args, "session_id") else None),
            echo=bool(getattr(args, "echo", False)),
        )
        _safe_print(json.dumps(result, ensure_ascii=False))
        return 0 if (isinstance(result, dict) and result.get("ok")) else 1

    p_exec = sub.add_parser("exec", help="Execute with context items (JSON input)")
    p_exec.add_argument("--project", default="", help="Project name (optional)")
    p_exec.add_argument("--agent", default="", help="Agent name (mutually exclusive with --prompt)")
    p_exec.add_argument("--prompt", default="", help="Prompt name (mutually exclusive with --agent)")
    p_exec.add_argument("--content-item", action="append", help="Content item (JSON or URL or text). Repeat for multiple items.")
    p_exec.add_argument("--output-type", default="", help="Desired output type (e.g., html)")
    p_exec.add_argument("--session-id", default="", help="Override session id (format: AgentName:chat or AgentName:chat:thread)")
    p_exec.add_argument("--echo", action="store_true", help="Return additional echo metadata from the run")
    p_exec.add_argument("--print-instructions", action="store_true", help="Print the merged instructions for the selection and exit")
    p_exec.set_defaults(func=cmd_exec)

    # clear-session subcommand
    def cmd_clear_session(args: argparse.Namespace) -> int:
        try:
            if not args.chat_id:
                _safe_print(json.dumps({"ok": False, "error_code": 400, "description": "--chat-id is required"}, ensure_ascii=False))
                return 1
            # name is optional; empty clears all for chat/thread
            res = call_api.asyncio.run(call_api.clear_session((args.name or None), chat_id=int(args.chat_id), thread_id=(int(args.thread_id) if args.thread_id is not None else None)))  # type: ignore[attr-defined]
            _safe_print(json.dumps(res, ensure_ascii=False))
            return 0 if (isinstance(res, dict) and res.get("ok")) else 1
        except Exception as e:
            _safe_print(json.dumps({"ok": False, "error_code": 500, "description": str(e)}, ensure_ascii=False))
            return 1

    p_clear = sub.add_parser("clear-session", help="Clear conversation session(s) for a chat/thread from SQLite")
    p_clear.add_argument("--name", default="", help="Agent name to clear (optional). If omitted, clears all sessions for chat/thread")
    p_clear.add_argument("--chat-id", required=True, help="Telegram chat id (required)")
    p_clear.add_argument("--thread-id", default=None, help="Telegram thread id (optional)")
    p_clear.set_defaults(func=cmd_clear_session)

    # Global flags
    parser.add_argument("--json-logs", action="store_true", help="Emit JSON logs (overrides CALL_LOG_JSON)")

    args = parser.parse_args()

    # Configure logging once per CLI process (DEBUG if CALL_DEBUG=1, else INFO)
    try:
        configure_logging(json=bool(getattr(args, "json_logs", False)))
    except Exception:
        pass
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
