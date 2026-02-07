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

    Supports:
    - YAML frontmatter (AgentCard-style) + Markdown body
    - Markdown cards with METADATA/PROMPT sections
    - Pure YAML cards (YAML-only DTOs)

    For Markdown cards, returns a metadata dictionary with a ``prompt`` field.
    For pure YAML cards, returns a mapping without a ``prompt`` field (legacy behavior).

    Raises:
        ValueError: when the content does not match the expected Markdown or YAML formats.
    """

    if not isinstance(md_text, str):
        raise ValueError("Card content must be a string")

    import re as _re
    import yaml as _yaml

    text = md_text

    def _strip_bom(value: str) -> str:
        return value.lstrip("\ufeff") if value.startswith("\ufeff") else value

    # Step 1: YAML frontmatter (AgentCard-style) at the top of a Markdown file.
    # Format:
    #   ---
    #   key: value
    #   ...
    #   ---
    #   prompt / body...
    #
    # We intentionally detect this before "pure YAML" parsing because PyYAML can
    # misinterpret frontmatter + body as YAML-only and drop the body.
    fm_meta: Dict[str, Any] | None = None
    fm_body: str | None = None
    try:
        fm_text = _strip_bom(text)
        # Only treat as frontmatter if the first non-empty line is exactly '---'
        if fm_text.startswith("---") and (
            fm_text == "---" or fm_text.startswith("---\n") or fm_text.startswith("---\r\n")
        ):
            # Find the closing delimiter on its own line.
            m = _re.match(r"^---\s*\r?\n", fm_text)
            if m:
                start = m.end()
                m_end = _re.search(r"^\s*---\s*$", fm_text[start:], _re.MULTILINE)
                if m_end:
                    yaml_payload = fm_text[start : start + m_end.start()]
                    rest = fm_text[start + m_end.end() :]
                    parsed = _yaml.safe_load(yaml_payload) or {}
                    if not isinstance(parsed, dict):
                        raise ValueError("Frontmatter must be a YAML mapping")
                    fm_meta = dict(parsed)
                    fm_body = rest
    except Exception:
        fm_meta = None
        fm_body = None

    if fm_meta is None:
        # Step 2: try parsing as pure YAML (YAML-only cards).
        try:
            loaded_meta = _yaml.safe_load(text)
            if isinstance(loaded_meta, dict):
                return dict(loaded_meta)
        except Exception:
            pass

    if not isinstance(text, str):
        raise ValueError("Card content must be string or YAML mapping")

    working_text = fm_body if fm_body is not None else text
    # Tolerate legacy variants / malformed tags.
    # Example: '<!-- META:START --' (missing '>') is normalized.
    working_text = _re.sub(
        r"<!--\s*(?:META|METADATA)\s*:??\s*START\s*--\s*>?",
        "<!-- METADATA:START -->",
        working_text,
        flags=_re.IGNORECASE,
    )
    working_text = _re.sub(
        r"<!--\s*(?:META|METADATA)\s*:??\s*END\s*--\s*>?",
        "<!-- METADATA:END -->",
        working_text,
        flags=_re.IGNORECASE,
    )
    meta: Dict[str, Any] = dict(fm_meta or {})

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
        # Merge frontmatter + legacy METADATA (legacy wins on key conflicts).
        meta.update(dict(parsed_meta))
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
