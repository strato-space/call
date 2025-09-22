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
from call.lib.logging import configure_logging as call_logging


def _emit_output(obj, fmt: str) -> None:
    fmt_l = (fmt or "json").lower()
    if fmt_l == "json":
        _safe_print(json.dumps(obj, ensure_ascii=False, indent=2))
        return
    if fmt_l == "yaml":
        try:
            import yaml  # type: ignore
            _safe_print(yaml.safe_dump(obj, allow_unicode=True, sort_keys=False))
            return
        except Exception:
            _safe_print(json.dumps(obj, ensure_ascii=False, indent=2))
            return
    # text fallback
    if isinstance(obj, (list, tuple)):
        for it in obj:
            _safe_print(str(it))
    else:
        _safe_print(str(obj))


def _agents_tree_to_text(items: list[dict]) -> str:
    lines: list[str] = []
    for proj in (items or []):
        pname = str(proj.get("name") or "").strip()
        if not pname:
            continue
        lines.append(pname)
        agents = proj.get("agents") or []
        for ag in agents:
            nm = str(ag.get("name") or "").strip()
            if not nm:
                continue
            lines.append(f"  - {nm}")
    return "\n".join(lines)


def cmd_list(args: argparse.Namespace) -> int:
    try:
        items = call_api.list(
            project=(args.project or None),
            agent=(args.agent or None),
            prompt=(args.prompt or None),
            state=(args.state or None) if hasattr(args, "state") else None,
            target=(args.target or None) if hasattr(args, "target") else None,
        )
        fmt = getattr(args, "format", "json")
        if fmt == "text":
            _safe_print(_agents_tree_to_text(items))
        elif fmt == "yaml":
            _emit_output(items, "yaml")
        else:
            _emit_output(items, "json")
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR"}, ensure_ascii=False), file=sys.stderr)
        return 1


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
            # Build directly; this handles wildcard interpretation and strict validation (MD-only)
            cfg, err = call_api.build_runnable_instructions_config(
                project=(args.project or None),
                agent=(agent or None),
                prompt=(args.prompt or None),
                merge=bool(getattr(args, "merge", False)),
            )
            if err:
                _safe_print(json.dumps(err, ensure_ascii=False))
                return 1
            # Dump full cfg in DEBUG (pretty JSON)
            try:
                from dataclasses import asdict as _asdict
                from call.lib.logging import debug_print as _dbg
                _dbg("[cli]", "[CFG]", json.dumps(_asdict(cfg) if cfg is not None else {}, ensure_ascii=False, indent=2))
            except Exception:
                pass
            _safe_print((cfg.instructions if cfg else "") or "")
            return 0

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

        # Build Telegram-identical payload from CLI flags (predictable ordering, no FS fallback)
        try:
            payload_json, payload_dict = call_api.build_input_payload(
                target=(args.target or None),
                main_text=(args.input or ""),
                extra_context=None,
                reply_text=None,
            )
        except Exception:
            payload_json, payload_dict = (args.input or None), None

        result = call_api.call(
            project=(args.project or None),
            agent=agent or None,
            prompt=(args.prompt or None),
            target=(args.target or None) if hasattr(args, "target") else None,
            input=(payload_json if payload_dict else (args.input or None)),
            session_id=((args.session_id or None) if hasattr(args, "session_id") else None),
            echo=bool(getattr(args, "echo", False)),
            merge=bool(getattr(args, "merge", False)),
        )
        _safe_print(json.dumps(result, ensure_ascii=False))
        return 0 if (isinstance(result, dict) and result.get("ok")) else 1
    except Exception as e:
        err = {"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR"}
        print(json.dumps(err, ensure_ascii=False), file=sys.stderr)
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

    p_agents = sub.add_parser("agents", aliases=["list", "projects"], help="List projects and agents (hierarchical)")
    p_agents.add_argument("--project", default="", help="Project filter (supports * wildcard)")
    p_agents.add_argument("--agent", default="", help="Agent filter (supports * and aliases)")
    p_agents.add_argument("--prompt", default="", help="Prompt filter (supports *)")
    p_agents.add_argument("--state", default="", help="State filter for prompts within agents (ready|draft; supports *)")
    p_agents.add_argument("--target", default="", help="Target filter (supports *; applied last)")
    p_agents.add_argument("--format", default="json", choices=["json", "yaml", "text"], help="Output format")
    p_agents.set_defaults(func=cmd_list)

    p_call = sub.add_parser("call", help="Call an agent with input text")
    p_call.add_argument("--project", default="", help="Project name (exact or with * wildcard)")
    p_call.add_argument("--agent", default="", help="Agent name or @Alias (exact or with * wildcard)")
    p_call.add_argument("--prompt", default="", help="Prompt override (exact or with * for selection)")
    p_call.add_argument("--target", default="", help="Unified selector (project|agent|prompt pattern)")
    p_call.add_argument("--input", default="", help="Input text for the agent")
    p_call.add_argument("--session-id", default="", help="Override session id (format: chat or chat:thread)")
    p_call.add_argument("--echo", action="store_true", help="Return additional echo metadata from the run")
    p_call.add_argument("--print-instructions", action="store_true", help="Print the merged instructions for the selection and exit")
    p_call.add_argument("--merge", dest="merge", action="store_true", help="Enable attribute/instructions merge (off by default)")
    p_call.add_argument("--trace", type=int, default=0, metavar="SECONDS", help="Dump all thread stacks every N seconds (debug)")
    p_call.add_argument("--trace-file", type=str, default="", help="Write stack dumps to a file instead of stderr")
    p_call.set_defaults(func=cmd_call)

    # prompts subcommand
    def cmd_prompts(args: argparse.Namespace) -> int:
        try:
            rows = call_api.list_prompts(
                project=(args.project or None),
                agent=(args.agent or None),
                prompt=(args.prompt or None),
                state=(args.state or None),
                target=(args.target or None),
            )
            # Normalize schema for CLI output
            items = []
            for r in (rows or []):
                # Base fields expected by existing tests
                item = {
                    "prompt_id": r.get("prompt", ""),
                    "name": r.get("prompt", ""),
                    "agent": r.get("agent", ""),
                    "project": r.get("project", ""),
                    "state": r.get("state", ""),
                    # Prefer canonical URL from DB; fallback to path for older rows
                    "url": r.get("url", "") or r.get("path", ""),
                }
                # Extra details for richer JSON/YAML output (table remains unchanged)
                if r.get("type"):
                    item["type"] = r.get("type")
                if r.get("rel_path"):
                    item["rel_path"] = r.get("rel_path")
                if r.get("goal"):
                    item["goal"] = r.get("goal")
                items.append(item)

            fmt = (args.format or 'table').lower()
            if fmt == 'json':
                _emit_output(items, 'json')
                return 0
            if fmt == 'yaml':
                _emit_output(items, 'yaml')
                return 0
            # text/table view with expected headers
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
        except Exception as e:
            print(json.dumps({"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR"}, ensure_ascii=False), file=sys.stderr)
            return 1

    p_prompts = sub.add_parser("prompts", help="List prompts (flat)")
    p_prompts.add_argument("--project", default="", help="Filter by project")
    p_prompts.add_argument("--agent", default="", help="Filter by agent")
    p_prompts.add_argument("--prompt", default="", help="Filter by prompt id or name (supports *)")
    p_prompts.add_argument("--state", default="", help="Filter by state (draft|ready; supports *)")
    p_prompts.add_argument("--target", default="", help="Filter by target (supports *; applied last)")
    p_prompts.add_argument("--format", default="table", choices=["table", "json", "yaml", "text"], help="Output format")
    p_prompts.set_defaults(func=cmd_prompts)

    # reload subcommand (alias: scan)
    def cmd_reload(args: argparse.Namespace) -> int:
        try:
            repos = None
            if args.repos:
                raw = str(args.repos)
                repos = [t.strip() for t in raw.replace(';', ',').split(',') if t.strip()]
            res = call_api.reload(repos=repos)
            _emit_output(res, args.format or 'json')
            return 0 if (isinstance(res, dict) and res.get('ok')) else 1
        except Exception as e:
            print(json.dumps({"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR"}, ensure_ascii=False), file=sys.stderr)
            return 1

    p_reload = sub.add_parser("reload", help="Scan repositories and rebuild repo.db")
    p_reload.add_argument("--repos", default="", help="Comma- or semicolon-separated list (agent,prompt)")
    p_reload.add_argument("--format", default="json", choices=["json", "yaml", "text"], help="Output format")
    p_reload.set_defaults(func=cmd_reload)
    # Backward-compatible alias
    p_scan = sub.add_parser("scan", help="Alias of reload (will be removed)")
    p_scan.add_argument("--repos", default="", help="Comma- or semicolon-separated list (agent,prompt)")
    p_scan.add_argument("--format", default="json", choices=["json", "yaml", "text"], help="Output format")
    p_scan.set_defaults(func=cmd_reload)

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
            cfg, err = call_api.build_runnable_instructions_config(
                project=(args.project or None),
                agent=(args.agent or None),
                prompt=(args.prompt or None),
                merge=True,
            )
            if err:
                _safe_print(json.dumps(err, ensure_ascii=False))
                return 1
            try:
                from dataclasses import asdict as _asdict
                from call.lib.logging import debug_print as _dbg
                snap = _asdict(cfg) if cfg is not None else {}
                _dbg("[cli]", "[CFG]", json.dumps(snap, ensure_ascii=False, indent=2))
            except Exception:
                pass
            _safe_print((cfg.instructions if cfg else "") or "")
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
    p_exec.add_argument("--session-id", default="", help="Override session id (format: chat or chat:thread)")
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
        call_logging(json=bool(getattr(args, "json_logs", False)))
    except Exception:
        pass

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
