"""
CLI for the call subsystem.

Command Reference (one-line):
  # List projects/agents/prompts (hierarchical JSON)
  python -m call.cli.main agents [--project UxFab] [--agent Agent*] [--prompt 10*]

  # Call: keyword-based API (best for MCP/REST/Actions). Selectors via flags, optional echo/trace.
  python -m call.cli.main call --agent BusinessAnalyticAgent --input "Analyze Q3" [--project UxFab]
  python -m call.cli.main call --agent BusinessAnalyticAgent --prompt Draft --input "Analyze Q3" [--project UxFab]

  # Exec: payload-based API (best for buckets of content items). All args merged into JSON payload.
  python -m call.cli.main exec --target Name --content-item "{...}" --input "text" [--echo]

Debug flags:
  --debug              Force DEBUG logging (overrides CALL_DEBUG)
  --json-logs          Emit logs as JSON lines
  --trace SECONDS      Dump all thread stacks every N seconds
  --trace-file PATH    Write stack dumps to a file instead of stderr
  --echo               Include echo metadata in response
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
from call.lib.logging import debug_print
from dotenv import load_dotenv
from pathlib import Path as _Path
import logging as _logging


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


def cmd_call(args: argparse.Namespace) -> int:
    # Normalize selectors
    agent = call_api.normalize_selector(getattr(args, 'agent', None)) or None
    project = call_api.normalize_selector(getattr(args, 'project', None)) or None
    prompt = call_api.normalize_selector(getattr(args, 'prompt', None)) or None
    target = call_api.normalize_selector(getattr(args, 'target', None)) or None
    trace_fp = None
    try:
        # Optional: print instructions and exit
        if getattr(args, "print_instructions", False) or getattr(args, "print_card", False):
            # Build directly; this handles wildcard interpretation and strict validation (MD-only)
            cfg, err = call_api.build_runnable_instructions_config(
                project=project,
                agent=agent,
                prompt=prompt,
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
            if getattr(args, "print_card", False):
                _safe_print((cfg.card_text if cfg else "") or "")
            else:
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

        # Inputs: --input is raw text; --parse-input builds a JSON payload via shared builder
        raw_input = (args.input or "") if hasattr(args, "input") else ""
        parsed_input = (args.parse_input or "") if hasattr(args, "parse_input") else ""
        if raw_input and parsed_input:
            _safe_print(json.dumps({"ok": False, "error_code": 400, "code": "BAD_REQUEST", "description": "Use either --input or --parse-input, not both."}, ensure_ascii=False))
            return 1

        payload_json = None
        payload_dict = None
        if parsed_input:
            # Build Telegram-identical payload from CLI flags (predictable ordering, no FS fallback)
            try:
                # If parsed_input is a JSON object, use its keys as hints
                eff_target = target
                eff_main = parsed_input
                eff_ctx = None
                eff_replay = None
                try:
                    if (parsed_input.strip().startswith('{') and parsed_input.strip().endswith('}')):
                        obj = json.loads(parsed_input)
                        if isinstance(obj, dict):
                            # CLI flag takes precedence
                            eff_target = eff_target or (obj.get('target') or obj.get('taget') or None)
                            eff_main = str(obj.get('input') or '')
                            ctx_val = obj.get('context')
                            eff_ctx = ctx_val if isinstance(ctx_val, list) else None
                            eff_replay = obj.get('replay') or obj.get('reply')
                except Exception:
                    pass
                try:
                    payload_json, payload_dict = call_api.build_input_payload(
                        target=eff_target,
                        main_text=(eff_main or ''),
                        extra_context=eff_ctx,
                        reply_text=(eff_replay if isinstance(eff_replay, str) else None),
                        download=bool(getattr(args, 'download_context', False)),
                    )
                except TypeError:
                    # Backward-compat for test stubs without 'download' kw
                    payload_json, payload_dict = call_api.build_input_payload(
                        target=eff_target,
                        main_text=(eff_main or ''),
                        extra_context=eff_ctx,
                        reply_text=(eff_replay if isinstance(eff_replay, str) else None),
                    )
                # Pretty-print payload to debug logs (capped to ~2000 chars)
                try:
                    import json as _json
                    txt = _json.dumps(payload_dict or {}, ensure_ascii=False, indent=2)
                    if len(txt) > 2000:
                        txt = txt[:1997] + "..."
                    debug_print("[cli]", "[PAYLOAD]", txt)
                except Exception:
                    pass
            except Exception:
                payload_json, payload_dict = (parsed_input or None), None

        # If --echo is set, do NOT call the LLM pipeline; just emit the prepared payload
        if bool(getattr(args, "echo", False)):
            try:
                # If we didn't build payload yet (no --parse-input), DO NOT build token context.
                # Echo only top-level fields: target and input
                if not payload_json:
                    payload_json = json.dumps({
                        "target": target,
                        "input": (raw_input or ''),
                    }, ensure_ascii=False)
                # Prepare resolved selection snapshot without executing the pipeline
                resolved: dict | None = None
                if bool(getattr(args, 'resolved', False)):
                    try:
                        cfg, err = call_api.build_runnable_instructions_config(
                            project=project,
                            agent=agent,
                            prompt=prompt,
                            target=target,
                            input=None,
                        )
                        if not err and cfg:
                            agent_val = getattr(cfg, 'agent', None)
                            if not agent_val:
                                agent_val = None
                            elif getattr(cfg, 'type', None) == "project":
                                agent_val = None
                            resolved = {
                                "id": getattr(cfg, 'id', None),
                                "type": getattr(cfg, 'type', None),
                                "project": getattr(cfg, 'project', None),
                                "agent": agent_val,
                                "prompt": getattr(cfg, 'prompt', None),
                                "path": getattr(cfg, 'path', None),
                                "url": getattr(cfg, 'url', None),
                            }
                    except Exception:
                        resolved = None

                # Print a compact echo object containing payload and resolved selection
                try:
                    payload_obj = json.loads(payload_json) if payload_json else {}
                except Exception:
                    payload_obj = payload_json or {}
                # Flatten: print top-level payload fields; add resolved when requested
                if resolved:
                    payload_obj["resolved"] = resolved
                _safe_print(json.dumps(payload_obj, ensure_ascii=False, indent=2))
                return 0
            except Exception as e:
                print(json.dumps({"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR"}, ensure_ascii=False), file=sys.stderr)
                return 1

        result = call_api.call(
            project=project,
            agent=agent,
            prompt=prompt,
            target=target if hasattr(args, "target") else None,
            input=(payload_json if payload_dict else (raw_input or None)),
            session_id=((args.session_id or None) if hasattr(args, "session_id") else None),
            echo=bool(getattr(args, "echo", False)),
        )
        # Honor --format for output (json|yaml|text)
        _emit_output(result, getattr(args, "format", "json"))
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
    # Load environment from call/.env first, then repo-root .env; do not override existing OS env
    try:
        _here = _Path(__file__).resolve()
        _call_dir = _here.parent.parent  # .../call/
        load_dotenv(dotenv_path=str(_call_dir / ".env"), override=False)
        load_dotenv(dotenv_path=str(_call_dir.parent / ".env"), override=False)
    except Exception:
        pass

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
    p_call.add_argument("--project", default="", help="Project name (exact or with * wildcard). '@' and '.md' suffix are stripped")
    p_call.add_argument("--agent", default="", help="Agent name or @Alias (exact or with * wildcard). '@' and '.md' suffix are stripped")
    p_call.add_argument("--prompt", default="", help="Prompt override (exact or with *). '@' and '.md' suffix are stripped")
    p_call.add_argument("--target", default="", help="Unified selector (project|agent|prompt). '@' and '.md' suffix are stripped")
    p_call.add_argument("--input", default="", help="Raw input text for the agent (passed as-is)")
    p_call.add_argument("--parse-input", default="", help="Parse input and build JSON payload identical to Telegram (uses shared builder)")
    p_call.add_argument("--session-id", default="", help="Override session id (format: chat or chat:thread)")
    p_call.add_argument("--download-context", action="store_true", help="Download/inline context by url/path (content for text, base64 for binaries)")
    p_call.add_argument("--echo", action="store_true", help="Return additional echo metadata from the run")
    p_call.add_argument("--resolved", action="store_true", help="Include resolved selection snapshot in echo output")
    p_call.add_argument("--print-instructions", action="store_true", help="Print the instructions for the selection and exit")
    p_call.add_argument("--print-card", action="store_true", help="Print full card text from the selected record")
    p_call.add_argument("--trace", type=int, default=0, metavar="SECONDS", help="Dump all thread stacks every N seconds (debug)")
    p_call.add_argument("--trace-file", type=str, default="", help="Write stack dumps to a file instead of stderr")
    p_call.add_argument("--format", default="json", choices=["json", "yaml", "text"], help="Output format")
    p_call.set_defaults(func=cmd_call)

    # prompts subcommand
    def cmd_prompts(args: argparse.Namespace) -> int:
        try:
            # Normalize selectors (API-level)
            pj = call_api.normalize_selector(args.project) or None
            ag = call_api.normalize_selector(args.agent) or None
            pr = call_api.normalize_selector(args.prompt) or None
            tg = call_api.normalize_selector(args.target) or None
            rows = call_api.list_prompts(
                project=pj,
                agent=ag,
                prompt=pr,
                state=(args.state or None),
                target=tg,
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
                if r.get("path"):
                    item["abs_path"] = r.get("path")
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
            res = call_api.reload(repos=repos, full_form=bool(getattr(args, "full_form", False)))
            _emit_output(res, args.format or 'json')
            return 0 if (isinstance(res, dict) and res.get('ok')) else 1
        except Exception as e:
            print(json.dumps({"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR"}, ensure_ascii=False), file=sys.stderr)
            return 1

    p_reload = sub.add_parser("reload", help="Scan repositories and rebuild repo.db")
    p_reload.add_argument("--repos", default="", help="Comma- or semicolon-separated list (agent,prompt)")
    p_reload.add_argument("--format", default="json", choices=["json", "yaml", "text"], help="Output format")
    p_reload.add_argument("--full-form", action="store_true", help="Emit detailed per-directory output (default off)")
    p_reload.set_defaults(func=cmd_reload)

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
        # Build payload by merging all provided args (no strict mutual exclusivity).
        payload: dict = {}
        # Selectors: merged into payload only; interpretation is deferred to the library
        if args.target:
            payload["target"] = args.target
        if args.agent:
            payload["agent"] = args.agent
        if args.prompt:
            payload["prompt"] = args.prompt
        if args.project:
            payload["project"] = args.project

        # Input and content/context
        if args.input:
            payload["input"] = args.input
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

        # Optional parse-input: build payload identical to Telegram builder and merge
        if args.parse_input:
            try:
                eff_target = payload.get("target") or None
                eff_main = args.parse_input
                eff_ctx = payload.get("context") if isinstance(payload.get("context"), list) else None
                eff_replay = None
                try:
                    if (eff_main.strip().startswith('{') and eff_main.strip().endswith('}')):
                        obj = json.loads(eff_main)
                        if isinstance(obj, dict):
                            eff_target = eff_target or (obj.get('target') or obj.get('taget') or None)
                            eff_main = str(obj.get('input') or '')
                            ctx_val = obj.get('context')
                            eff_ctx = ctx_val if isinstance(ctx_val, list) else eff_ctx
                            eff_replay = obj.get('replay') or obj.get('reply')
                except Exception:
                    pass
                pj, pd = call_api.build_input_payload(
                    target=eff_target,
                    main_text=(eff_main or ''),
                    extra_context=eff_ctx,
                    reply_text=(eff_replay if isinstance(eff_replay, str) else None),
                    download=bool(getattr(args, 'download_context', False)) if hasattr(args, 'download_context') else False,
                )
                # Merge keys from builder payload into our payload (builder keys take precedence)
                try:
                    pobj = json.loads(pj) if pj else {}
                except Exception:
                    pobj = {}
                if isinstance(pobj, dict):
                    payload.update({k: v for k, v in pobj.items() if v is not None})
            except Exception:
                pass

        # Optional: print instructions only (interprets selectors from args, not payload)
        if getattr(args, "print_instructions", False) or getattr(args, "print_card", False):
            cfg, err = call_api.build_runnable_instructions_config(
                project=(args.project or None),
                agent=(args.agent or None),
                prompt=(args.prompt or None),
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
            if getattr(args, "print_card", False):
                _safe_print((getattr(cfg, "card_text", None) or ""))
            else:
                _safe_print((cfg.instructions if cfg else "") or "")
            return 0

        # Echo path: print payload JSON and exit (do not execute)
        if bool(getattr(args, "echo", False)):
            output = dict(payload)
            if bool(getattr(args, "resolved", False)):
                resolved = None
                try:
                    cfg, err = call_api.build_runnable_instructions_config(
                        project=(args.project or None),
                        agent=(args.agent or None),
                        prompt=(args.prompt or None),
                        target=(args.target or None),
                        input=None,
                    )
                    if not err and cfg:
                        agent_val = getattr(cfg, "agent", None)
                        if getattr(cfg, "type", None) == "project":
                            agent_val = None
                        resolved = {
                            "id": getattr(cfg, "id", None),
                            "type": getattr(cfg, "type", None),
                            "project": getattr(cfg, "project", None),
                            "agent": agent_val,
                            "prompt": getattr(cfg, "prompt", None),
                            "path": getattr(cfg, "path", None),
                            "url": getattr(cfg, "url", None),
                        }
                except Exception:
                    resolved = None
                if resolved:
                    output["resolved"] = resolved
            _emit_output(output, getattr(args, "format", "json"))
            return 0

        # Optional: MCP build-and-stop — construct cfg and exit without running
        if bool(getattr(args, "mcp_build_and_stop", False)):
            cfg, err = call_api.build_runnable_instructions_config(
                project=(args.project or None),
                agent=(args.agent or None),
                prompt=(args.prompt or None),
                target=(args.target or None),
                input=None,
            )
            if err:
                _emit_output(err, getattr(args, "format", "json"))
                return 1
            # Preflight start to initialize MCP and emit logs, then exit without a turn
            try:
                import asyncio as _asyncio
                from call.app.call import build_and_run_agent as _build_and_run_agent

                async def _preflight_start():
                    try:
                        # Enter and exit immediately; this triggers MCP init and logs
                        async with _build_and_run_agent(cfg, user_input=""):
                            return
                    except Exception:
                        # Do not fail CLI on preflight errors; continue to print cfg
                        return

                _asyncio.run(_preflight_start())
            except Exception:
                # If app layer missing or import error, still print cfg below
                pass

            # Emit compact snapshot suitable for MCP preflight
            try:
                from dataclasses import asdict as _asdict
                snap = _asdict(cfg) if cfg is not None else {}
            except Exception:
                snap = {
                    "name": getattr(cfg, "name", None),
                    "project": getattr(cfg, "project", None),
                    "prompt_override": getattr(cfg, "prompt_override", None),
                    "type": getattr(cfg, "type", None),
                    "path": getattr(cfg, "path", None),
                    "url": getattr(cfg, "url", None),
                    "model": getattr(cfg, "model", None),
                }
            _emit_output({"ok": True, "cfg": snap}, getattr(args, "format", "json"))
            return 0

        # Execute: prefer payload-only interpretation when exactly one selector is present.
        selectors = [str(getattr(args, k) or "").strip() for k in ("project", "agent", "prompt", "target")]
        sel_count = sum(1 for s in selectors if s)
        if sel_count == 1:
            kwargs, err = call_api.api_interpret_exec_payload(payload)
            if err:
                _emit_output(err, getattr(args, "format", "json"))
                return 1
            result = call_api.call(**kwargs)
        else:
            # Back-compat path: allow multiple selectors and pass full payload as input
            import json as _json
            result = call_api.call(
                project=(args.project or None),
                agent=(args.agent or None),
                prompt=(args.prompt or None),
                target=(args.target or None),
                input=_json.dumps(payload, ensure_ascii=False),
                session_id=((args.session_id or None) if hasattr(args, "session_id") else None),
                echo=False,
            )
        _emit_output(result, getattr(args, "format", "json"))
        return 0 if (isinstance(result, dict) and result.get("ok")) else 1

    # Add exec subparser now that cmd_exec is defined
    p_exec = sub.add_parser("exec", help="Execute via payload (best for content buckets)")
    p_exec.add_argument("--project", default="", help="Project name (optional; merged into payload)")
    p_exec.add_argument("--agent", default="", help="Agent name (merged into payload)")
    p_exec.add_argument("--prompt", default="", help="Prompt name (merged into payload)")
    p_exec.add_argument("--target", default="", help="Unified target (project|agent|prompt) merged into payload")
    p_exec.add_argument("--input", default="", help="Plain LLM input text (merged into payload)")
    p_exec.add_argument("--parse-input", default="", help="Parse user text into payload (identical to Telegram builder)")
    p_exec.add_argument("--content-item", action="append", help="Content item (JSON or URL or text). Repeat for multiple items.")
    p_exec.add_argument("--output-type", default="", help="Desired output type (e.g., html)")
    p_exec.add_argument("--session-id", default="", help="Override session id (format: chat or chat:thread)")
    p_exec.add_argument("--echo", action="store_true", help="Print the payload and exit (no execution)")
    p_exec.add_argument("--resolved", action="store_true", help="Include resolved selection snapshot in echo output")
    p_exec.add_argument("--print-instructions", action="store_true", help="Print the instructions for the selection and exit")
    p_exec.add_argument("--print-card", action="store_true", help="Print full card text from the selected record")
    p_exec.add_argument("--mcp-build-and-stop", dest="mcp_build_and_stop", action="store_true", help="Build runnable config, print and exit (no execution)")
    p_exec.add_argument("--format", default="json", choices=["json", "yaml", "text"], help="Output format")
    p_exec.set_defaults(func=cmd_exec)

    # clear-session subcommand (uses global handler defined above)
    p_clear = sub.add_parser("clear-session", help="Clear conversation session(s) for a chat/thread from SQLite")
    p_clear.add_argument("--name", default="", help="Agent name to clear (optional). If omitted, clears all sessions for chat/thread")
    p_clear.add_argument("--chat-id", required=True, help="Telegram chat id (required)")
    p_clear.add_argument("--thread-id", default=None, help="Telegram thread id (optional)")
    p_clear.set_defaults(func=cmd_clear_session)

    # Global flags
    parser.add_argument("--json-logs", action="store_true", help="Emit JSON logs (overrides CALL_LOG_JSON)")
    parser.add_argument("--debug", action="store_true", help="Force DEBUG logging (overrides CALL_DEBUG)")

    args = parser.parse_args()

    # Configure logging once per CLI process (DEBUG if CALL_DEBUG=1, else INFO)
    try:
        call_logging(level=(_logging.DEBUG if bool(getattr(args, "debug", False)) else None), json=bool(getattr(args, "json_logs", False)))
    except Exception:
        pass

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
