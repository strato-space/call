"""
CLI for the call subsystem.

Usage examples:
  python -m call.cli.main list
  python -m call.cli.main list --aliases --q "ux"
  python -m call.cli.main call @AgentFab "Build a new agent"
"""
from __future__ import annotations

import argparse
import json
import sys
import io
import os
import faulthandler

from call.lib import api as call_api


def cmd_list(args: argparse.Namespace) -> int:
    items = call_api.list(query=args.q, include_aliases=args.aliases)
    print(json.dumps(items, ensure_ascii=False, indent=2))
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    name = args.name
    if name.startswith("@"):
        name = name[1:]
    trace_fp = None
    try:
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

        result = call_api.call(name=name, input=args.input or "", echo=bool(getattr(args, "echo", False)))
        print(json.dumps(result, ensure_ascii=False))
        return 0
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
        description="call — CLI for listing and invoking agents",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List available agents")
    p_list.add_argument("--aliases", action="store_true", help="Include aliases in output")
    p_list.add_argument("--q", type=str, default=None, help="Filter substring for names/paths")
    p_list.set_defaults(func=cmd_list)

    p_call = sub.add_parser("call", help="Call an agent with input text")
    p_call.add_argument("name", type=str, help="Agent name or @Alias")
    p_call.add_argument("input", type=str, nargs="?", default="", help="Input text for the agent")
    p_call.add_argument("--echo", action="store_true", help="Return additional echo metadata from the run")
    p_call.add_argument("--trace", type=int, default=0, metavar="SECONDS", help="Dump all thread stacks every N seconds (debug)")
    p_call.add_argument("--trace-file", type=str, default="", help="Write stack dumps to a file instead of stderr")
    p_call.set_defaults(func=cmd_call)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
