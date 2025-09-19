import asyncio
import os
import sys
import traceback
from typing import Optional, TextIO

async def dump_tasks_periodically(period: int, dump_fp: Optional[TextIO] = None) -> None:
    """Periodically dump asyncio tasks to dump_fp (or stderr) every 'period' seconds.

    When dump_fp is None, printing is gated behind CALL_DEBUG to avoid noisy output.
    """
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
