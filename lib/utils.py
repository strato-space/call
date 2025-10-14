import asyncio
import os
import sys
import traceback
from typing import Optional, TextIO, Dict, Any


async def dump_tasks_periodically(
    period: int, dump_fp: Optional[TextIO] = None
) -> None:
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
                    enabled = str(os.environ.get("CALL_DEBUG", "")).strip().lower() in (
                        "1",
                        "true",
                        "yes",
                        "on",
                    )
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


def parse_metadata_and_prompt(
    md_text: str, *, path: str | None = None
) -> Dict[str, Any]:
    """Parse agent/prompt card content.

    Supports Markdown cards with METADATA/PROMPT sections and pure YAML cards.
    Returns a metadata dictionary with a ``prompt`` field (may be ``""`` for YAML-only cards).

    Raises:
        ValueError: when the content does not match the expected Markdown or YAML formats.
    """

    if not isinstance(md_text, str):
        raise ValueError("Card content must be a string")

    import re as _re
    import yaml as _yaml

    text = md_text

    # Step 1: try parsing as pure YAML
    try:
        loaded_meta = _yaml.safe_load(text)
        if isinstance(loaded_meta, dict):
            return dict(loaded_meta)
    except Exception:
        pass

    if not isinstance(text, str):
        raise ValueError("Card content must be string or YAML mapping")

    working_text = text
    meta: Dict[str, Any] = {}

    flags = _re.IGNORECASE | _re.DOTALL
    meta_start = _re.search(r"<!--\s*METADATA\s*:??\s*START\s*-->", working_text, flags)
    meta_end = _re.search(r"<!--\s*METADATA\s*:??\s*END\s*-->", working_text, flags)
    if meta_start and meta_end and meta_end.start() > meta_start.end():
        meta_section = working_text[meta_start.end() : meta_end.start()]
        yaml_match = _re.search(r"```(?:yaml)?\s*\r?\n(.*?)```", meta_section, flags)
        parsed_meta = None
        if yaml_match:
            yaml_payload = yaml_match.group(1)
            try:
                parsed_meta = _yaml.safe_load(yaml_payload) or {}
            except Exception as exc:
                raise ValueError("Markdown card METADATA YAML failed to parse") from exc
        else:
            candidate = meta_section.strip()
            if candidate:
                try:
                    parsed_meta = _yaml.safe_load(candidate) or {}
                except Exception as exc:
                    raise ValueError(
                        "Markdown card METADATA YAML failed to parse"
                    ) from exc
        if parsed_meta is None:
            raise ValueError(
                "Markdown card missing ```yaml block inside METADATA section"
            )
        if not isinstance(parsed_meta, dict):
            raise ValueError("Markdown card METADATA did not parse into a mapping")
        meta = dict(parsed_meta)
        working_text = (
            working_text[: meta_start.start()] + working_text[meta_end.end() :]
        )

    prompt_start = _re.search(r"<!--\s*PROMPT\s*:??\s*START\s*-->", working_text, flags)
    prompt_end = _re.search(r"<!--\s*PROMPT\s*:??\s*END\s*-->", working_text, flags)
    if prompt_start and prompt_end and prompt_end.start() > prompt_start.end():
        prompt_body = working_text[prompt_start.end() : prompt_end.start()].strip()
        meta["prompt"] = prompt_body
        return meta

    prompt_text = working_text.strip()
    meta["prompt"] = prompt_text if prompt_text else ""
    return meta
