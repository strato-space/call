from __future__ import annotations

from typing import Optional, List, Dict, Any
from agents.model_settings import ModelSettings
from dataclasses import dataclass, field, asdict
from contextvars import ContextVar
import os
import sqlite3
import asyncio
import json
import logging
from pathlib import Path as _Path
from call.lib import repo_db as call_repo
from call.lib import repo_fs as repo_fs
from call.lib.logging import debug_print
import builtins as _bi
import re
from collections import deque
from collections.abc import Mapping, Sequence, Set as AbstractSet


_attribute_overrides_var: ContextVar[Dict[str, Any] | None] = ContextVar(
    "call_attribute_overrides",
    default=None,
)


def read(card_id: str) -> str:
    """Return raw card text stored in repo.db for the given identifier."""

    if not card_id or not str(card_id).strip():
        raise ValueError("card id is required")

    conn = call_repo._ensure_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT card FROM repo WHERE target = ? LIMIT 1",
            (str(card_id),),
        )
        row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not row:
        raise call_repo.CardNotFoundError(f"card '{card_id}' not found")

    card_text = row[0]
    if not isinstance(card_text, str) or not card_text:
        raise call_repo.CardNotFoundError(f"card '{card_id}' not found")

    return card_text


def write(card_id: str, card_text: str) -> None:
    """Persist card text to repo.db and the filesystem (DB first)."""

    if not card_id or not str(card_id).strip():
        raise ValueError("card id is required")
    if card_text is None:
        raise ValueError("card text is required")

    card_str = str(card_text)
    conn = call_repo._ensure_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT path FROM repo WHERE target = ? LIMIT 1",
            (str(card_id),),
        )
        row = cur.fetchone()
        if not row:
            raise call_repo.CardNotFoundError(f"card '{card_id}' not found")
        cur.execute(
            "UPDATE repo SET card = ? WHERE target = ?",
            (card_str, str(card_id)),
        )
        if cur.rowcount <= 0:
            raise call_repo.CardNotFoundError(f"card '{card_id}' not found")
        conn.commit()
    finally:
        cur.close()
        conn.close()

    fs_path_raw = row[0] if row else ""
    try:
        fs_path = str(fs_path_raw or "").strip()
    except Exception:
        fs_path = ""

    if not fs_path:
        return

    path_obj = _Path(fs_path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    try:
        path_obj.write_text(card_str, encoding="utf-8")
    except Exception as exc:
        try:
            debug_print(
                "[cards.write]",
                "filesystem_write_failed",
                str(path_obj),
                str(exc),
            )
        except Exception:
            pass
        raise


def _dict_with_str_keys(data: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    normalized: Dict[str, Any] = {}
    for key, value in data.items():
        try:
            key_str = str(key)
        except Exception:
            continue
        normalized[key_str] = value
    return normalized


def _normalize_attribute_overrides(overrides: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(overrides, dict):
        return {}
    normalized: Dict[str, Any] = {}
    for key, value in overrides.items():
        if value is None:
            continue
        try:
            key_str = str(key)
        except Exception:
            continue
        normalized[key_str] = value
    return normalized


def _serialize_model_item(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    for attr_name in ("model_dump", "dict", "to_dict"):
        attr = getattr(item, attr_name, None)
        if callable(attr):
            try:
                data = attr()
            except Exception:
                continue
            if isinstance(data, dict):
                return data
    try:
        data = vars(item)
    except Exception:
        data = None
    if isinstance(data, dict) and data:
        return {k: v for k, v in data.items() if not k.startswith("_")}
    identifier = getattr(item, "id", None)
    try:
        identifier = str(identifier) if identifier is not None else str(item)
    except Exception:
        identifier = None
    return {"id": identifier}


_SNAPSHOT_ID_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_TEXT_MODE_MARKERS = ("text", "chat", "completion")
_NON_TEXT_IDENTIFIER_MARKERS = (
    "embedding",
    "embed",
    "whisper",
    "speech",
    "voice",
    "audio",
    "image",
    "vision-only",
    "vision",
    "realtime-audio",
    "realtime",
    "dall-e",
    "dalle",
)
_TEXT_IDENTIFIER_MARKERS = (
    "gpt",
    "davinci",
    "curie",
    "babbage",
    "ada",
    "o1",
    "o3",
)


def _iter_string_values(value: Any):
    queue: deque[Any] = deque([value])
    while queue:
        current = queue.popleft()
        if isinstance(current, str):
            yield current
            continue
        if isinstance(current, (bytes, bytearray)):
            try:
                queue.append(current.decode())
            except Exception:
                continue
            continue
        if isinstance(current, Mapping):
            queue.extend(current.values())
            continue
        if isinstance(current, (Sequence, AbstractSet)):
            queue.extend(current)


def _model_supports_text_output(item: Dict[str, Any]) -> bool:
    for key in ("modes", "modalities", "response_types"):
        for raw in _iter_string_values(item.get(key)):
            normalized = raw.strip().lower()
            if not normalized:
                continue
            if any(marker in normalized for marker in _TEXT_MODE_MARKERS):
                return True
    capabilities = item.get("capabilities")
    if isinstance(capabilities, dict):
        for cap_name, enabled in capabilities.items():
            if not enabled:
                continue
            if not isinstance(cap_name, str):
                try:
                    cap_name = str(cap_name)
                except Exception:
                    continue
            normalized = cap_name.strip().lower()
            if any(marker in normalized for marker in _TEXT_MODE_MARKERS):
                return True
    type_value = item.get("type")
    if isinstance(type_value, str):
        normalized = type_value.strip().lower()
        if any(marker in normalized for marker in _TEXT_MODE_MARKERS):
            return True
    identifier = item.get("id") or item.get("name")
    try:
        identifier_str = str(identifier).strip()
    except Exception:
        identifier_str = ""
    identifier_lower = identifier_str.lower()
    if not identifier_lower:
        return False
    if any(marker in identifier_lower for marker in _NON_TEXT_IDENTIFIER_MARKERS):
        return False
    if any(marker in identifier_lower for marker in _TEXT_IDENTIFIER_MARKERS):
        return True
    return True


def _model_is_snapshot(item: Dict[str, Any]) -> bool:
    identifier = item.get("id")
    try:
        identifier_str = str(identifier) if identifier is not None else ""
    except Exception:
        return False
    if not identifier_str:
        return False
    return bool(_SNAPSHOT_ID_PATTERN.search(identifier_str))


def _compile_wildcard_regex(pattern: str | None):
    """Compile a case-insensitive full-string regex from a wildcard pattern ('*' -> '.*')."""
    if not pattern:
        return None
    try:
        import re as _re

        return _re.compile(
            "^" + _re.escape(pattern).replace("\\*", ".*") + "$", _re.IGNORECASE
        )
    except Exception:
        return None


def normalize_selector(val: Optional[str]) -> Optional[str]:
    """Normalize selectors by stripping leading '@' and trailing '.md' / '.markdown'.

    Returns empty string for empty inputs, and None passes through unchanged.
    """
    if val is None:
        return None
    if not isinstance(val, str):
        try:
            val = str(val)
        except Exception:
            return None
    s = val.strip()
    if not s:
        return ""
    if s.startswith("@"):
        s = s[1:]
    sl = s.lower()
    if sl.endswith(".markdown"):
        s = s[:-9]
    elif sl.endswith(".md"):
        s = s[:-3]
    return s


def _maybe_inline_context_content(items: list[dict]) -> None:
    try:
        import mimetypes as _mimes
        import base64 as _b64

        try:
            import httpx as _httpx
        except Exception:
            _httpx = None

        def _is_text(mime: str | None, name: str) -> bool:
            if not mime:
                lower = name.lower()
                return lower.endswith(
                    (".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".tsv")
                )
            return mime.startswith("text/") or mime in (
                "application/json",
                "application/yaml",
                "application/x-yaml",
            )

        for it in items:
            try:
                if not isinstance(it, dict):
                    continue
                if it.get("content") or it.get("base64"):
                    continue
                url_val = str(it.get("url") or "").strip()
                path_val = str(it.get("path") or "").strip()

                if url_val and _httpx:
                    try:
                        guess, _ = _mimes.guess_type(url_val)
                        with _httpx.Client(
                            timeout=15.0, follow_redirects=True
                        ) as client:
                            resp = client.get(url_val)
                            data = resp.content or b""
                        if _is_text(guess, url_val):
                            it["content"] = data.decode("utf-8", "replace")
                        else:
                            it["base64"] = _b64.b64encode(data).decode("ascii")
                    except Exception:
                        continue
                    else:
                        continue

                if path_val:
                    try:
                        p = _Path(path_val)
                        if not p.exists():
                            continue
                        guess, _ = _mimes.guess_type(p.name)
                        data = p.read_bytes()
                        if _is_text(guess, p.name):
                            try:
                                it["content"] = data.decode("utf-8")
                            except Exception:
                                it["content"] = data.decode("utf-8", "replace")
                        else:
                            it["base64"] = _b64.b64encode(data).decode("ascii")
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        return


def list_prompts(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    state: Optional[str] = None,
    target: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Flat prompts listing facade for upper layers (CLI, Actions, Bot, MCP).

    Delegates to repo_db.list_prompts() via compatibility alias 'repo'.
    Do not swallow exceptions; let callers see failures.
    """
    return call_repo.list_prompts(
        project=project, agent=agent, state=state, target=target, prompt=prompt
    )


def interpret_target(
    *,
    project: str | None,
    agent: str | None,
    prompt: str | None,
    target: str | None,
) -> "call_repo.RepoCardRow":
    """Resolve a single repo card row using the provided filters."""

    project = normalize_selector(project)
    agent = normalize_selector(agent)
    prompt = normalize_selector(prompt)
    target = normalize_selector(target)

    if target:
        return call_repo.select_card(
            project=project,
            agent=agent,
            prompt=prompt,
            target=target,
        )

    if prompt:
        return call_repo.select_card(
            project=project,
            agent=agent,
            prompt=prompt,
            kind="prompt",
        )

    if agent:
        return call_repo.select_card(
            project=project,
            agent=agent,
            kind="agent",
        )

    if project:
        return call_repo.select_card(
            project=project,
            kind="project",
        )

    raise call_repo.SelectionNotFoundError(
        "No card filters provided",
        kind="card",
        filters={},
    )


def build_input_payload(
    *,
    target: Optional[str],
    main_text: str,
    extra_context: Optional[list] = None,
    reply_text: Optional[str] = None,
    download: bool = False,
) -> tuple[str, dict | None]:
    """Build a structured JSON payload used by Telegram bot and CLI echo.

    - Ordered keys: target, replay, input, context
    - Token parsing: extracts @Tokens and plain tokens; strips .md/.markdown suffixes
    - Resolution: attempts build_runnable_instructions_config per token
    - When download=True, inlines content for text files and base64 for binaries (by url/path)
    """
    import re as _re

    payload: dict = {}
    if isinstance(target, str) and target.strip():
        payload["target"] = target.strip()
    ctx_items: list = []
    if isinstance(extra_context, _bi.list) and extra_context:
        try:
            ctx_items.extend([x for x in extra_context if isinstance(x, dict)])
        except Exception:
            ctx_items = [x for x in extra_context if isinstance(x, dict)]

    # Tokenize (allow '*' inside tokens to support wildcard patterns like '@31-*')
    tokens: list[str] = []
    try:
        s = (main_text or "").strip()
        if s:
            raw = _re.findall(r"[@]?[A-Za-zА-Яа-я0-9*][A-Za-zА-Яа-я0-9._:/\\\-*]*", s)
            for t in raw:
                u = t.lstrip("@").strip().strip(",.;:")
                ul = u.lower()
                if ul.endswith(".md"):
                    u = u[:-3]
                elif ul.endswith(".markdown"):
                    u = u[:-9]
                if u and u not in tokens:
                    tokens.append(u)
            tokens = tokens[:12]
    except Exception:
        tokens = []

    # Resolve tokens → context via repo index only (no runtime builder calls)
    refs: list[dict] = []
    seen_refs: set[tuple[str, str, str]] = set()

    def _append_rows(rows: list[dict]) -> None:
        if not rows:
            return
        for row in rows:
            row_id = row["id"]
            rpath = row["rel_path"]
            ref_type = row["type"]
            ref = {
                "type": ref_type,
                "path": rpath,
                "id": row_id,
                "mutable": True,
            }
            for key in (
                "id",
                "type",
                "target",
                "project",
                "agent",
                "prompt",
                "state",
                "goal",
                "engine",
                "orchestration",
                "url",
            ):
                if key in row and row[key] not in (None, ""):
                    ref[key] = row[key]

            key_id = (ref.get("id"), ref.get("path"))
            if key_id in seen_refs:
                continue
            seen_refs.add(key_id)
            refs.append(ref)

    for tok in tokens:
        try:
            proj_rows = call_repo.find_projects(project=tok, target=None)
        except Exception:
            proj_rows = []
        _append_rows(proj_rows)

        try:
            agent_rows = call_repo.find_agents(project=None, agent=tok, target=None)
        except Exception:
            agent_rows = []
        _append_rows(agent_rows)

        try:
            prompt_rows = list_prompts(project=None, agent=None, prompt=tok)
        except Exception:
            prompt_rows = []

        _append_rows(prompt_rows)

    if refs:
        try:
            ctx_items.extend(refs)
        except Exception:
            ctx_items = refs

    # Optional download
    if download and ctx_items:
        _maybe_inline_context_content(ctx_items)

    ordered: dict = {}
    if payload.get("target"):
        ordered["target"] = payload["target"]
    if isinstance(reply_text, str) and reply_text.strip():
        ordered["replay"] = reply_text.strip()
    if (main_text or "").strip():
        ordered["input"] = (main_text or "").strip()
    if ctx_items:
        ordered["context"] = ctx_items
    if ordered:
        return (json.dumps(ordered, ensure_ascii=False), ordered)
    return ((main_text or ""), None)


def reload(
    *, repos: Optional[List[str]] = None, full_form: bool = True
) -> Dict[str, Any]:
    """Filesystem scan and DB refresh (uniform name).

    Delegates to repo_fs.reload() (or scan()) and returns its dict result.
    """
    try:
        res = repo_fs.reload(repos, full_form=full_form)
        # After a successful filesystem reload, clear cached Agents so that
        # sub-agents/tools pick up updated prompt instructions on next use.
        try:
            if isinstance(res, dict) and res.get("ok"):
                from call.app import call as app_call

                app_call.AGENT_CACHE.clear()
                debug_print("[api.reload]", "agent_cache_cleared")
        except Exception as hook_exc:
            logging.debug("[api] reload post-hook failed: %s", hook_exc)
        return res
    except Exception as e:
        return {
            "ok": False,
            "error_code": 500,
            "description": str(e),
            "code": "INTERNAL_ERROR",
        }


@dataclass
class RunnableConfig:
    """Minimal ready-to-run config consumed by app.build_and_run_agent."""

    # Primary identifiers and descriptive metadata
    id: str | None = None
    type: str | None = None  # 'project' | 'agent' | 'prompt'
    path: str | None = None  # Repo-relative card path when available
    url: str | None = None  # Public URL (e.g., GitHub blob) for the selected card
    goal: str | None = None
    role: str | None = None

    # Hierarchy identifiers resolved from metadata (prefer *_id values)
    project: str | None = None
    agent: str | None = None
    prompt: str | None = None

    # Convenience selectors mirroring the original user request
    target: str | None = None
    # User-provided input text (never used as instructions)
    input: str = ""

    # Text payloads
    # Raw prompt body extracted from the primary card prior to merges
    prompt_text: str = ""
    # Final instructions dispatched to the runtime after merges/overlays
    instructions: str = ""
    # Raw Markdown/structured card text (if available)
    card_text: str = ""

    # Runtime configuration and attributes
    model: str = "gpt-5"
    model_settings: ModelSettings = field(default_factory=ModelSettings)
    attributes: Dict[str, Any] = field(default_factory=dict)
    mcp: List[Dict[str, Any]] = field(default_factory=list)
    # Declared tools to enable for the run (e.g., ["WebSearchTool", "image_genetation_tool"])
    tools: List[str] = field(default_factory=list)

    # Additional execution context
    base_dir: str = ""

    @staticmethod
    def minimal(model: str, input: str = "") -> "RunnableConfig":
        """Create a minimal config for pure GPT calls without instructions.

        Args:
            model: Model identifier (e.g., 'gpt-5', 'gpt-4o-mini')
            input: User input text

        Returns:
            RunnableConfig with empty instructions and minimal metadata
        """
        return RunnableConfig(
            id="void",
            type=None,
            path=None,
            url=None,
            goal=None,
            role=None,
            project=None,
            agent="void",
            prompt=None,
            target=None,
            input=input,
            prompt_text="",
            instructions="",
            card_text="",
            model=model,
            model_settings=ModelSettings(),
            attributes={"model": model},
            mcp=[],
            tools=[],
            base_dir="",
        )


# todo исключить обращение к файловой системе, использовать repo.db и радиально упростить код исключив взаимное влиние prompt / agent /project за исключением model и model-settings /  model-settings-${model}


def build_runnable_instructions_config(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    target: Optional[str] = None,
    input: Optional[str] = None,
    attributes_override: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[RunnableConfig], Optional[Dict[str, Any]]]:
    """Build a minimal runnable configuration DTO from repository selection.

    When all selectors (project, agent, prompt, target) are None, returns a minimal
    config with only the default model and input, allowing pure GPT calls without instructions.
    """

    import os as _os
    from pathlib import Path as _Path

    # Get default model from environment
    default_env_model = (
        str(_os.environ.get("LLM_MODEL", "gpt-5") or "gpt-5").strip() or "gpt-5"
    )

    missing_card_exc = getattr(call_repo, "CardNotFoundError", FileNotFoundError)
    malformed_card_exc = getattr(call_repo, "CardFormatError", ValueError)
    selection_error_cls = getattr(call_repo, "SelectionError", Exception)

    def _listify(value: Any) -> List[Any]:
        if isinstance(value, _bi.list):
            return [item for item in value if item is not None]
        if value is None:
            return []
        return [value]

    def _string_items(value: Any) -> List[str]:
        items: List[str] = []
        for raw in _listify(value):
            if isinstance(raw, _bi.str):
                text = raw.strip()
            else:
                text = str(raw).strip()
            if text:
                items.append(text)
        return items

    overrides_in = attributes_override
    if overrides_in is None:
        try:
            overrides_in = _attribute_overrides_var.get()
        except LookupError:
            overrides_in = None
    attribute_overrides = _normalize_attribute_overrides(overrides_in)

    requested_project = normalize_selector(project)
    requested_agent = normalize_selector(agent)
    requested_prompt = normalize_selector(prompt)

    # Pure GPT path: if all selectors are None, return minimal config with input only
    if not any([requested_project, requested_agent, requested_prompt, target]):
        final_model = default_env_model
        override_model = attribute_overrides.get("model")
        if isinstance(override_model, _bi.str) and override_model.strip():
            final_model = override_model.strip()

        minimal_config = RunnableConfig.minimal(
            model=final_model, input=str(input or "")
        )
        return minimal_config, None

    try:
        selected_row = interpret_target(
            project=requested_project,
            agent=requested_agent,
            prompt=requested_prompt,
            target=target,
        )
    except selection_error_cls as exc:
        status = getattr(exc, "status", 400)
        code = getattr(exc, "code", "BAD_REQUEST")
        options = getattr(exc, "options", None)
        details = getattr(exc, "filters", None)
        return None, _error_payload(
            agent=(requested_agent or agent or ""),
            input=str(input or ""),
            exc=str(exc),
            status=int(status or 400),
            code=str(code) if code else "BAD_REQUEST",
            project=requested_project or project,
            options=options if isinstance(options, _bi.list) else None,
            details=details if isinstance(details, dict) else None,
        )
    except Exception as exc:
        return None, _error_payload(
            agent=(requested_agent or agent or ""),
            input=str(input or ""),
            exc=str(exc),
            status=500,
            code="INTERNAL_ERROR",
            project=requested_project or project,
        )

    card_identifier = selected_row.id or selected_row.target or selected_row.path
    if not card_identifier:
        return None, _error_payload(
            agent=(requested_agent or agent or ""),
            input=str(input or ""),
            exc="Card identifier is missing",
            status=404,
            code="NO_DATA_FOUND",
            project=requested_project or project,
        )

    try:
        meta, prompt_text, raw_text = call_repo.get_card(str(card_identifier))
    except malformed_card_exc as exc:
        return None, _error_payload(
            agent=(requested_agent or agent or ""),
            input=str(input or ""),
            exc=str(exc),
            status=400,
            code="BAD_CARD_FORMAT",
            project=requested_project or project,
        )
    except missing_card_exc as exc:
        return None, _error_payload(
            agent=(requested_agent or agent or ""),
            input=str(input or ""),
            exc=str(exc),
            status=404,
            code="NO_DATA_FOUND",
            project=requested_project or project,
        )

    attributes: Dict[str, Any] = _dict_with_str_keys(
        meta if isinstance(meta, dict) else {}
    )
    prompt_body = str(prompt_text or "")
    card_body = str(raw_text or "")

    # Instructions come from prompt body only, never from user input
    instructions_text = prompt_body

    goal_value = selected_row.goal
    role_raw = attributes.get("role") if isinstance(attributes, dict) else None
    role_value = role_raw if isinstance(role_raw, _bi.str) else None

    final_model = default_env_model
    meta_model = attributes.get("model")
    if isinstance(meta_model, _bi.str) and meta_model.strip():
        final_model = meta_model.strip()
    override_model = attribute_overrides.get("model")
    if isinstance(override_model, _bi.str) and override_model.strip():
        final_model = override_model.strip()

    attributes["model"] = final_model

    for key, value in attribute_overrides.items():
        if key == "model":
            continue
        if value is None:
            attributes.pop(key, None)
        else:
            attributes[key] = value

    def _build_model_settings(
        attrs: Dict[str, Any], model_name: Optional[str]
    ) -> ModelSettings:
        if not isinstance(attrs, dict):
            return ModelSettings()
        scoped: Dict[str, Any] = {}
        if model_name:
            scoped_raw = attrs.get(f"model-settings-{model_name}")
            if isinstance(scoped_raw, dict):
                scoped = dict(scoped_raw)
        if not scoped:
            generic = attrs.get("model-settings")
            if isinstance(generic, dict):
                scoped = dict(generic)
        if not isinstance(scoped, dict):
            scoped = {}
        try:
            return ModelSettings(**scoped)
        except Exception:
            return ModelSettings()

    model_settings = _build_model_settings(attributes, final_model)

    mcp_raw = attributes.get("mcp")
    if isinstance(mcp_raw, _bi.list):
        mcp_list = [item for item in mcp_raw if isinstance(item, dict)]
    elif isinstance(mcp_raw, dict):
        mcp_list = [dict(mcp_raw)]
    else:
        mcp_list = []

    tools_list = _string_items(attributes.get("tools"))

    path_value = selected_row.rel_path
    url_value = selected_row.url
    type_value = selected_row.type

    project_value = selected_row.project
    agent_value = selected_row.agent
    prompt_value = selected_row.prompt

    base_dir = ""
    if selected_row.path:
        try:
            base_dir = str(_Path(selected_row.path).parent)
        except Exception:
            base_dir = ""

    cfg = RunnableConfig(
        id=selected_row.id,
        type=type_value,
        path=path_value,
        url=url_value,
        goal=goal_value,
        role=role_value,
        project=project_value,
        agent=agent_value,
        prompt=prompt_value,
        target=selected_row.target,
        input=str(input or ""),
        prompt_text=prompt_body,
        instructions=instructions_text,
        card_text=card_body,
        model=final_model,
        model_settings=model_settings,
        attributes=attributes,
        mcp=mcp_list,
        tools=tools_list,
        base_dir=base_dir,
    )

    try:
        setattr(cfg, "name", cfg.prompt or cfg.agent or cfg.project or cfg.id or "")
    except Exception:
        pass

    if isinstance(cfg.attributes, dict):
        cfg.attributes["model"] = cfg.model

    return cfg, None


def _error_payload_event(
    event: str,
    exc: BaseException | str,
    *,
    status: int | None = None,
    debug: bool = False,
    code: Optional[str] = None,
    options: Optional[List[Dict[str, Any]]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if isinstance(exc, BaseException):
        msg_attr = getattr(exc, "message", None)
        message = (
            msg_attr if isinstance(msg_attr, str) and msg_attr else str(exc) or "Error"
        )
        code_attr = getattr(exc, "code", None)
        if isinstance(code_attr, int):
            effective_status = code_attr
        elif isinstance(code_attr, str) and code_attr.isdigit():
            effective_status = int(code_attr)
        else:
            effective_status = int(status or 400)
        err_attr = getattr(exc, "error", None)
        error_obj = err_attr if isinstance(err_attr, dict) else {"message": message}
    else:
        message = str(exc) if exc is not None else "Error"
        effective_status = int(status or 400)
        error_obj = {"message": message}

    if isinstance(error_obj, dict):
        err_msg = error_obj.get("message")
        if isinstance(err_msg, str) and err_msg.strip():
            message = err_msg.strip()

    payload: Dict[str, Any] = {
        "ok": False,
        "event": event,
        "error_code": effective_status,
        "description": message,
        "error": error_obj,
    }
    if options is not None:
        payload["options"] = options
    if code is not None:
        payload["code"] = code

    if debug:
        try:
            import traceback

            payload["debug"] = traceback.format_exc().strip().splitlines()[-20:]
        except Exception as e:
            logging.debug("[api] Failed to format debug traceback in _error_payload_event: %s", e)

    if details is not None:
        payload["details"] = details

    return payload


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
    session_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if isinstance(exc, BaseException):
        msg_attr = getattr(exc, "message", None)
        message = (
            msg_attr if isinstance(msg_attr, str) and msg_attr else str(exc) or "Error"
        )
        code_attr = getattr(exc, "code", None)
        if isinstance(code_attr, int):
            effective_status = code_attr
        elif isinstance(code_attr, str) and code_attr.isdigit():
            effective_status = int(code_attr)
        else:
            effective_status = int(status or 400)
        err_attr = getattr(exc, "error", None)
        error_obj = err_attr if isinstance(err_attr, dict) else {"message": message}
    else:
        message = str(exc) if exc is not None else "Error"
        effective_status = int(status or 400)
        error_obj = {"message": message}

    if isinstance(error_obj, dict):
        err_msg = error_obj.get("message")
        if isinstance(err_msg, str) and err_msg.strip():
            message = err_msg.strip()

    payload: Dict[str, Any] = {
        "ok": False,
        "error_code": effective_status,
        "description": message,
        "agent": agent,
        "project": (project or ""),
        "final_output": None,
        "echo": bool(echo),
        "error": error_obj,
    }

    if session_id:
        payload["session_id"] = session_id
    if options is not None:
        payload["options"] = options
    if code is not None:
        payload["code"] = code

    if debug:
        try:
            import traceback

            payload["debug"] = traceback.format_exc().strip().splitlines()[-20:]
        except Exception as e:
            logging.debug("[api] Failed to format debug traceback in _error_payload: %s", e)

    if details is not None:
        payload["details"] = details

    return payload


def _parse_session_id(raw: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    """Extract chat_id and thread_id from session id in the form "chat" or "chat:thread".

    No AgentName prefix is supported.
    Returns (chat_id, thread_id).
    """
    if not raw:
        return None, None
    try:
        s = str(raw).strip()
        parts = s.split(":")
        if not parts:
            return None, None
        chat = int(parts[0]) if parts[0] else None
        thread = int(parts[1]) if len(parts) > 1 and parts[1] else None
        return chat, thread
    except Exception:
        return None, None


async def call_async(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    target: Optional[str] = None,
    input: Optional[str] = None,
    event: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    session_id: Optional[str] = None,
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

    Selection convenience:
    - When 'target' is provided, we interpret it with precedence using the repo index (SQLite):
      1) prompt name
      2) agent name/alias
      3) project name
      The first match sets the corresponding field if it wasn't already set explicitly.
    """
    # Lazily import app-layer functions to avoid hard import at module load time
    from call.app import call as app_call

    # Wait for MCP servers to be ready (lazy init on first call)
    try:
        await app_call.wait_for_mcp_init(timeout=120.0)
    except Exception as e:
        logging.debug("[api] MCP init wait: %s", e)
        # Continue even if MCP init fails - not all calls require MCP
    
    # Event short-circuit: when event is supplied, acknowledge without invoking the pipeline
    if event is not None:
        event_str = str(event)
        try:
            call_repo.push_event(event_str, input)
        except Exception as push_exc:
            debug_print(
                "[api]",
                "[events]",
                f"Failed to persist event '{event_str}': {push_exc}",
            )
        if event_str.strip().lower() == "error_test":
            return _error_payload_event(
                event=event_str,
                debug=debug,
                exc="Synthetic test error",
                status=500,
                code="FAKE_EVENT_ERROR",
            )
        return {"ok": True, "event": event_str, "targets": []}

    attribute_overrides = _normalize_attribute_overrides(attributes)
    token_override = None
    if attribute_overrides:
        token_override = _attribute_overrides_var.set(attribute_overrides)

    def _reset_override() -> None:
        nonlocal token_override
        if token_override is not None:
            try:
                _attribute_overrides_var.reset(token_override)
            except Exception:
                pass
            token_override = None

    # Project-only guard: ensure selection errors bubble up before building cfg
    auto_selected_agent = False
    if project and not any([agent, prompt, target]):
        probe = resolve_agent(project=project, agent=None, prompt=None, target=None)
        if isinstance(probe, dict) and probe.get("ok"):
            resolved_probe = probe.get("resolved") or {}
            candidate_agent = (
                resolved_probe.get("name")
                or resolved_probe.get("agent")
            )
            if candidate_agent:
                agent = candidate_agent
                auto_selected_agent = True
        else:
            if isinstance(probe, dict):
                if session_id and "session_id" not in probe:
                    probe["session_id"] = session_id
                _reset_override()
                return probe
            _reset_override()
            return _error_payload(
                agent="",
                input=str(input or ""),
                exc="No agent found matching criteria",
                status=404,
                code="NO_DATA_FOUND",
                project=project,
                session_id=session_id,
            )

    # Build ready-to-run config (handles target, wildcard prompt, selection, and blank agent)
    cfg, cfg_err = build_runnable_instructions_config(
        project=project,
        agent=agent,
        prompt=prompt,
        target=target,
        input=input,
        attributes_override=(attribute_overrides or None),
    )
    if isinstance(cfg_err, dict):
        # Preserve original error envelope (status/code) from resolve_agent
        try:
            if session_id:
                cfg_err["session_id"] = session_id
        except Exception:
            pass
        _reset_override()
        return cfg_err

    cfg_type = str(getattr(cfg, "type", "") or "").lower()
    if cfg_type == "project":
        # If project has prompt text (card_text or prompt_text), it's executable directly
        # Skip agent resolution for executable projects
        has_prompt = bool(
            getattr(cfg, "card_text", None) or 
            getattr(cfg, "prompt_text", None) or 
            getattr(cfg, "instructions", None)
        )
        if not has_prompt:
            # Non-executable project: try to find an agent to run
            agent_probe = resolve_agent(
                project=getattr(cfg, "project", None), agent=None, prompt=None, target=None
            )
            if not isinstance(agent_probe, dict) or not agent_probe.get("ok"):
                if isinstance(agent_probe, dict):
                    if session_id and "session_id" not in agent_probe:
                        agent_probe["session_id"] = session_id
                    _reset_override()
                    return agent_probe
                _reset_override()
                return _error_payload(
                    agent=str(getattr(cfg, "agent", "") or ""),
                    input=str(input or ""),
                    exc="No agent found matching criteria",
                    status=404,
                    code="NO_DATA_FOUND",
                    project=getattr(cfg, "project", None),
                    session_id=session_id,
                )

    # Debug CFG payload removed - too verbose for normal operation
    # Uncomment if needed for deep debugging:
    # try:
    #     cfg_payload = asdict(cfg)
    # except Exception:
    #     cfg_payload = getattr(cfg, "__dict__", {})
    # try:
    #     debug_print(
    #         "[api]", "[CFG]", json.dumps(cfg_payload, ensure_ascii=False, indent=2)
    #     )
    # except Exception:
    #     debug_print("[api]", "[CFG]", str(cfg_payload))

    # Initialize bot: if a project is provided, pass it; otherwise allow app layer
    # to prefer CALL_TELEGRAM_TOKEN or TELEGRAM_TOKEN per its own logic.
    try:
        await app_call.init_bot(
            project_name=(project if (project or "").strip() else None)
        )
    except Exception as _e:
        # If bot init fails, continue; downstream may still function without telegram
        pass

    # Proceed with cfg-driven run; build 'resolved' from cfg
    # Resolved descriptor for response/echo (new schema)
    resolved = {
        "id": cfg.id,
        "type": cfg.type,
        "project": cfg.project,
        "agent": cfg.agent,
        "prompt": cfg.prompt,
        # path is repo-relative (e.g., 'agent/Proj/Agent/agent.md' or 'prompt/ready/...')
        "path": cfg.path,
        # Optional helpful fields
        "url": cfg.url,
        "goal": cfg.goal,
    }
    # For project selections, resolved.agent should be null
    try:
        if str(resolved.get("type") or "").lower() == "project":
            resolved["agent"] = None
    except Exception:
        pass

    # Align with app/main: set effective targets according to session rules
    # Priority:
    #   1) If session_id override provided: parse chat/thread from it
    #   2) Else if chat_id/thread_id args provided: use them (fallback to env for missing)
    #   3) Else: use TELEGRAM_CHAT_ID/TELEGRAM_THREAD_ID from .env as fallback
    sel_chat: Optional[int] = None
    sel_thread: Optional[int] = None
    sid_override = (session_id or "").strip()
    if sid_override:
        c, t = _parse_session_id(sid_override)
        sel_chat, sel_thread = c, t
    else:
        if (chat_id is not None) or (thread_id is not None):
            sel_chat = chat_id if chat_id is not None else app_call.TELEGRAM_CHAT_ID
            sel_thread = (
                thread_id
                if thread_id is not None
                else (app_call.TELEGRAM_THREAD_ID or None)
            )
        else:
            # Fallback to .env values when running from CLI without explicit session
            sel_chat = app_call.TELEGRAM_CHAT_ID
            sel_thread = app_call.TELEGRAM_THREAD_ID or None

    selected_chat_id = sel_chat
    selected_thread_id = sel_thread
    # Update the app module globals so downstream utils see them
    app_call.selected_chat_id = selected_chat_id
    app_call.selected_thread_id = selected_thread_id
    # Signal to app layer whether to create a session or not
    try:
        setattr(app_call, "force_no_session", bool(selected_chat_id is None))
    except Exception:
        pass
    # Prefer simple routing rules (KISS). No app-level override flags.

    # No welcome banner here (avoid duplicate messages). The pipeline will emit a single digest.

    # Optionally enable periodic asyncio tasks dump (for diagnosing long waits)
    dump_period_s = 0
    try:
        dump_period_s = int(os.environ.get("CALL_DUMP_TASKS_EVERY", "0") or "0")
    except Exception:
        dump_period_s = 0
    dump_file_path = os.environ.get("CALL_DUMP_TASKS_FILE", "")
    dump_fp = None

    from call.lib.utils import dump_tasks_periodically as _dump_tasks_periodically

    dump_task = None
    try:
        if dump_period_s > 0:
            if dump_file_path:
                try:
                    dump_fp = open(dump_file_path, "a", encoding="utf-8", buffering=1)
                except Exception:
                    dump_fp = None
            dump_task = asyncio.create_task(
                _dump_tasks_periodically(dump_period_s, dump_fp)
            )

        try:
            # Build and run the agent once with a ready config (returns directly, no context manager)
            agent_obj, _cfg, _session = await app_call.build_and_run_agent(
                cfg=cfg, user_input=((getattr(cfg, "input", None) or input) or "")
            )
            final_output = getattr(_cfg, "_last_final_output", None)
            try:
                actual_sid = getattr(_session, "id", None)
            except Exception:
                actual_sid = None
        except Exception as e:
            # Convert pipeline errors to structured error; map known tracing 403 to 403
            logging.exception("[api] Pipeline execution failed")
            msg = str(e)
            status = 500
            err_code = "PIPELINE_ERROR"
            details = None
            if (
                ("Tracing client error" in msg)
                or ("request_forbidden" in msg)
                or ("unsupported_country_region_territory" in msg)
            ):
                status = 403
                err_code = "REQUEST_FORBIDDEN"
                try:
                    brace = msg.find("{")
                    if brace != -1:
                        details = json.loads(msg[brace:])
                except Exception:
                    details = None
            if isinstance(e, app_call.MCPInitializationError):
                status = 503
                err_code = "MCP_INIT_FAILED"
                cause = getattr(e, "cause", None)
                cause_msg = str(cause) if cause else ""
                if "voice.stratospace.fun" in cause_msg:
                    msg = "Voice service is unavailable (voice.stratospace.fun timeout). Please try again later."
                else:
                    msg = "MCP tools initialization failed. One of external MCP servers is unavailable. Please try again later."
                if cause_msg:
                    details = {"cause": cause_msg}
            if status == 403:
                return _error_payload(
                    agent=(cfg.id or ""),
                    input=(input or ""),
                    exc="Tracing client request forbidden",
                    status=403,
                    echo=echo,
                    debug=debug,
                    code=err_code,
                    project=cfg.project,
                    details=details,
                    session_id=(session_id or None),
                )
            return _error_payload(
                agent=(cfg.id or ""),
                input=(input or ""),
                exc=msg,
                status=status,
                echo=echo,
                debug=debug,
                code=err_code,
                project=cfg.project,
                details=details,
                session_id=(session_id or None),
            )
    finally:
        if dump_task is not None:
            try:
                dump_task.cancel()
            except Exception as e:
                logging.debug("[api] Failed to cancel dump task: %s", e)
        if dump_fp is not None:
            try:
                dump_fp.close()
            except Exception as e:
                logging.debug("[api] Failed to close dump file: %s", e)
        _reset_override()

    # If the pipeline returned a plain-text error (e.g., "Error: ...\n\nTraceback ..."),
    # convert it to a structured error envelope to avoid printing stack traces to users.
    if isinstance(final_output, str) and final_output.strip().lower().startswith(
        "error:"
    ):
        msg = final_output.strip()
        # Derive a concise description (first line without "Error: ")
        first_line = msg.splitlines()[0]
        desc = (
            first_line[len("Error:") :].strip()
            if first_line.lower().startswith("error:")
            else first_line
        )
        status = 502
        err_code = "PIPELINE_ERROR"
        if "connection error" in msg.lower():
            err_code = "UPSTREAM_CONNECT_ERROR"
            status = 502
        return _error_payload(
            agent=(cfg.id or ""),
            input=(input or ""),
            exc=desc or msg,
            status=status,
            echo=echo,
            debug=debug,
            code=err_code,
            project=cfg.project,
            session_id=(session_id or None),
        )

    # Emit session id: prefer explicit override; else actual runtime; else agentless chat[:thread]
    # Only emit when: override provided, session created, or routing explicitly configured
    session_id_out = None
    try:
        if isinstance(session_id, str) and session_id.strip():
            # Explicit override provided
            session_id_out = session_id
        elif "actual_sid" in locals() and actual_sid:
            # Session was created
            session_id_out = actual_sid
        elif (
            chat_id is not None or thread_id is not None
        ) and selected_chat_id is not None:
            # Routing was explicitly configured via args
            session_id_out = (
                f"{selected_chat_id}:{selected_thread_id}"
                if (selected_thread_id is not None)
                else f"{selected_chat_id}"
            )
    except Exception:
        session_id_out = None

    return {
        "ok": True,
        "agent": cfg.id,
        "agent_path": cfg.path,
        "final_output": final_output,
        # echo flag included for callers that want to inspect behavior upstream
        "echo": echo,
        "resolved": resolved,
        **({"session_id": session_id_out} if session_id_out else {}),
    }


def call(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    target: Optional[str] = None,
    input: Optional[str] = None,
    event: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    session_id: Optional[str] = None,
    echo: bool = False,
    debug: bool = False,
) -> Dict[str, Any]:
    """
    Thin sync wrapper over call_async. All selection and error handling is in call_async.
    """
    async def _call_with_owner_cleanup() -> Dict[str, Any]:
        try:
            from call.app import call as app_call
        except Exception:
            return await call_async(
                project=project,
                agent=agent,
                prompt=prompt,
                target=target,
                input=input,
                event=event,
                attributes=attributes,
                chat_id=chat_id,
                thread_id=thread_id,
                session_id=session_id,
                echo=echo,
                debug=debug,
            )

        owner_was_running = False
        try:
            owner_was_running = app_call.is_mcp_owner_running()
        except Exception:
            owner_was_running = False

        try:
            return await call_async(
                project=project,
                agent=agent,
                prompt=prompt,
                target=target,
                input=input,
                event=event,
                attributes=attributes,
                chat_id=chat_id,
                thread_id=thread_id,
                session_id=session_id,
                echo=echo,
                debug=debug,
            )
        finally:
            try:
                owner_running_after = app_call.is_mcp_owner_running()
                owner_tag = app_call.get_mcp_owner_tag()
            except Exception:
                owner_running_after = False
                owner_tag = None

            if (
                not owner_was_running
                and owner_running_after
                and owner_tag == "waiter"
            ):
                try:
                    await app_call.stop_mcp_owner_task()
                except Exception:
                    logging.getLogger("call.api").exception(
                        "Failed to stop MCP owner task after call()"
                    )

    try:
        return asyncio.run(_call_with_owner_cleanup())
    except Exception as e:
        return _error_payload(
            agent or "",
            input or "",
            e,
            status=500,
            echo=echo,
            debug=debug,
            code="INTERNAL_ERROR",
            project=project,
            session_id=(session_id or None),
        )


# Projects/agents listing — monkeypatch-friendly wrappers for tests


def load_projects_index() -> List[str]:
    """Wrapper delegating to discovery.load_projects_index(); exposed for test monkeypatching."""
    try:
        from call.lib import discovery as _disc

        return _disc.load_projects_index()
    except Exception:
        return []


def scan_project_agents(project_dir: str) -> List[Dict[str, Any]]:
    """Wrapper delegating to discovery.scan_project_agents(); accepts project name or absolute path."""
    try:
        from pathlib import Path as _Path
        from call.lib import discovery as _disc

        p = _Path(project_dir)
        if not p.exists():
            try:
                base = _disc.discover_agent_repo()
                p = _Path(base) / str(project_dir)
            except Exception:
                p = _Path(str(project_dir))
        return _disc.scan_project_agents(p)
    except Exception:
        return []


def list(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    state: Optional[str] = None,
    target: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return hierarchical structure from the repo DB.

    Delegates to call.lib.repo.list(), which applies wildcard filters and returns:
      [ { name: <project>, agents: [ { name, aliases, prompts, path, ... } ] } ]
    """
    try:
        return call_repo.list(
            project=project, agent=agent, prompt=prompt, state=state, target=target
        )
    except Exception:
        return []


def models() -> List[Dict[str, Any]]:
    """Retrieve available OpenAI models using the official client."""

    from openai import OpenAI

    client = OpenAI()
    response = client.models.list()
    data = getattr(response, "data", None)
    items: List[Dict[str, Any]] = []
    for entry in data or []:
        try:
            serialized = _serialize_model_item(entry)
        except Exception:
            continue
        if not isinstance(serialized, dict):
            continue
        if _model_is_snapshot(serialized):
            continue
        if not _model_supports_text_output(serialized):
            continue
        items.append(serialized)
    return items


def resolve_agent(
    *,
    project: Optional[str] = None,
    agent: Optional[str] = None,
    prompt: Optional[str] = None,
    target: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a single agent strictly via repo DB queries.

    Rules:
    - If agent is provided: query find_agents(project, agent). Must match exactly one.
    - Else if prompt is provided: query list_prompts(project, agent, prompt). Must match exactly one; then resolve its agent row.
    - Else if only project is provided: ambiguity — return TOO_MANY_ROWS with options from find_agents(project).
    - No filesystem reads, no alias expansion.
    """
    try:
        # 1) Resolve by agent name
        agent_filter = isinstance(agent, str) and agent.strip()
        if agent_filter:
            rows = call_repo.find_agents(project=(project or None), agent=agent)
            if rows:
                if len(rows) > 1:
                    return _error_payload(
                        agent=agent,
                        input="",
                        exc="Multiple agents matched your criteria",
                        status=400,
                        code="TOO_MANY_ROWS",
                        project=project,
                        options=rows[:20],
                    )
                r = rows[0]
                return {
                    "ok": True,
                    "resolved": {
                        "project": r.get("project"),
                        "name": r.get("agent"),
                        "path": r.get("path"),
                        "aliases": [],
                        "prompts": [],
                    },
                }
            if not (isinstance(prompt, str) and prompt.strip()):
                return _error_payload(
                    agent=agent,
                    input="",
                    exc="No agent found matching criteria",
                    status=404,
                    code="NO_DATA_FOUND",
                    project=project,
                    options=[],
                )

        # 2) Resolve by prompt
        if isinstance(prompt, str) and prompt.strip():
            recs = call_repo.list_prompts(
                project=(project or None), agent=(agent or None), prompt=prompt
            )
            if not recs:
                alt_recs: list[dict] = []
                try:
                    alt_recs = call_repo.list_prompts(
                        project=None, agent=None, prompt=prompt
                    )
                except Exception:
                    alt_recs = []
                if alt_recs:
                    # Only project is required; agent is optional for project-level prompts
                    valid_alt = [
                        r
                        for r in alt_recs
                        if str(r.get("project") or "").strip()
                    ]
                    if not valid_alt:
                        return _error_payload(
                            agent=(agent or ""),
                            input="",
                            exc="Prompt metadata could not be parsed",
                            status=400,
                            code="BAD_CARD_FORMAT",
                            project=project,
                            options=alt_recs[:20],
                        )
                    project_norm = (project or "").strip().lower()
                    candidates = []
                    for row in valid_alt:
                        pj = str(row.get("project") or "").strip()
                        if project_norm and pj.lower() != project_norm:
                            continue
                        candidates.append(row)
                    if candidates:
                        chosen = candidates[0]
                        pj = chosen.get("project") or project
                        ag = chosen.get("agent") or agent
                        # For project-level prompts without agent, return path directly
                        if not ag:
                            return {
                                "ok": True,
                                "resolved": {
                                    "project": pj,
                                    "name": "",
                                    "path": chosen.get("path") or "",
                                    "aliases": [],
                                    "prompts": [prompt] if prompt else [],
                                },
                            }
                        # For prompts with agent, resolve agent
                        arows = call_repo.find_agents(project=pj, agent=ag)
                        if len(arows) == 1:
                            ar = arows[0]
                            return {
                                "ok": True,
                                "resolved": {
                                    "project": ar.get("project"),
                                    "name": ar.get("agent"),
                                    "path": ar.get("path"),
                                    "aliases": [],
                                    "prompts": [],
                                },
                            }
                    return _error_payload(
                        agent=(agent or ""),
                        input="",
                        exc="No agent found matching criteria",
                        status=404,
                        code="NO_DATA_FOUND",
                        project=project,
                        options=valid_alt[:20],
                    )
                resolved_stub: Dict[str, Any] = {}
                if isinstance(project, str) and project:
                    resolved_stub["project"] = project
                if isinstance(agent, str) and agent:
                    resolved_stub["name"] = agent
                if isinstance(prompt, str) and prompt.strip():
                    resolved_stub["prompts"] = [prompt.strip()]
                return {"ok": True, "resolved": resolved_stub}
            if len(recs) > 1:
                return _error_payload(
                    agent=(agent or ""),
                    input="",
                    exc="Multiple prompts matched your criteria",
                    status=400,
                    code="TOO_MANY_ROWS",
                    project=project,
                    options=recs[:20],
                )
            pr = recs[0]
            pj = pr.get("project") or project
            ag = pr.get("agent") or agent
            # For project-level prompts without agent, return path directly
            if not ag:
                return {
                    "ok": True,
                    "resolved": {
                        "project": pj,
                        "name": "",
                        "path": pr.get("path") or "",
                        "aliases": [],
                        "prompts": [prompt] if prompt else [],
                    },
                }
            # Agent row must exist for agent-based prompts
            arows = call_repo.find_agents(project=pj, agent=ag)
            if len(arows) != 1:
                return _error_payload(
                    agent=str(ag or ""),
                    input="",
                    exc="No agent found matching criteria",
                    status=404,
                    code="NO_DATA_FOUND",
                    project=pj,
                    options=(arows or []),
                )
            ar = arows[0]
            return {
                "ok": True,
                "resolved": {
                    "project": ar.get("project"),
                    "name": ar.get("agent"),
                    "path": ar.get("path"),
                    "aliases": [],
                    "prompts": [],
                },
            }

        # 3) Only project provided -> ambiguous
        if isinstance(project, str) and project.strip():
            opts = call_repo.find_agents(project=project, agent=None)
            if len(opts) == 1:
                r = opts[0]
                return {
                    "ok": True,
                    "resolved": {
                        "project": r.get("project"),
                        "name": r.get("agent"),
                        "path": r.get("path"),
                        "aliases": [],
                        "prompts": [],
                    },
                }
            return _error_payload(
                agent=(agent or ""),
                input="",
                exc=(
                    "No agent found matching criteria"
                    if not opts
                    else "Multiple agents matched your criteria"
                ),
                status=(404 if not opts else 400),
                code=("NO_DATA_FOUND" if not opts else "TOO_MANY_ROWS"),
                project=project,
                options=opts[:20] if opts else [],
            )

        # Nothing to resolve
        return _error_payload(
            agent=(agent or ""),
            input="",
            exc="No agent found matching criteria",
            status=404,
            code="NO_DATA_FOUND",
            project=project,
            options=[],
        )
    except Exception as e:
        return _error_payload(
            agent=(agent or ""),
            input="",
            exc=e,
            status=500,
            code="INTERNAL_ERROR",
            project=project,
        )


async def clear_session(
    name: Optional[str], *, chat_id: Optional[int], thread_id: Optional[int]
) -> Dict[str, Any]:
    """Clear conversation session(s) for this chat/thread from SQLite.

    Rules (agentless ids only):
    - If `name` is given: ignored for session id derivation; delete only "chat[:thread]" for the provided chat/thread.
    - If `name` is empty/None: same behavior — delete only "chat[:thread]".

    We operate on two tables if present: messages(session_id) and sessions(id).
    """

    # Validate inputs
    if not chat_id:
        return {"ok": False, "error_code": 400, "description": "chat_id is required"}

    def _sid_new(chat: int, thread: Optional[int]) -> str:
        return f"{chat}:{thread}" if thread is not None else f"{chat}"

    db_path = os.getenv("CALL_DB", "call/call.db")
    cleared: List[str] = []
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Detect existing tables once
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        has_messages = bool(cur.fetchone())
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        )
        has_sessions = bool(cur.fetchone())

        # Single candidate: new format only
        sids: List[str] = []
        candidate = _sid_new(int(chat_id), thread_id)
        if has_sessions:
            cur.execute("SELECT id FROM sessions WHERE id = ?", (candidate,))
            sids += [row[0] for row in cur.fetchall()]
        if has_messages:
            cur.execute(
                "SELECT DISTINCT session_id FROM messages WHERE session_id = ?",
                (candidate,),
            )
            sids += [row[0] for row in cur.fetchall()]

        if not sids:
            cur.close()
            conn.close()
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


def api_interpret_exec_payload(
    payload: Dict[str, object],
) -> Tuple[Dict[str, object], Optional[Dict[str, object]]]:
    """Validate and normalize a single exec payload into kwargs for call().

    Rules:
    - Exactly one of project|agent|prompt|target must be present (truthy string).
    - Always use the full payload JSON as the input string.

    Returns (kwargs, err) where kwargs can be passed to call(**kwargs) and err is an error envelope on validation error.
    """
    try:
        try:
            debug_print(
                "[api]",
                "interpret_exec_payload:|-\n"
                + json.dumps(payload, ensure_ascii=False, indent=2),
            )
        except Exception:
            pass
        # Determine exactly one among project|agent|prompt|target (allow zero when event present)
        f_project = payload.get("project")
        f_agent = payload.get("agent")
        f_prompt = payload.get("prompt")
        f_target = payload.get("target")
        f_event = payload.get("event")
        fields = [
            f
            for f in [f_project, f_agent, f_prompt, f_target]
            if (str(f or "").strip())
        ]
        event_present = f_event is not None and str(f_event).strip() != ""
        if not event_present and len(fields) != 1:
            return {}, {
                "ok": False,
                "error_code": 400,
                "description": "Provide exactly one of 'project' or 'agent' or 'prompt' or 'target'",
                "code": "BAD_REQUEST",
            }
        if event_present and len(fields) > 0:
            return {}, {
                "ok": False,
                "error_code": 400,
                "description": "When 'event' is provided, do not include project|agent|prompt|target selectors",
                "code": "BAD_REQUEST",
            }
        # Always use full payload JSON as input
        inp = json.dumps(payload, ensure_ascii=False)
        kwargs = {
            "project": None,
            "agent": None,
            "prompt": None,
            "target": None,
            "input": inp,
            "echo": bool(payload.get("echo", False)),
        }
        # Assign only the provided selector
        if str(f_project or "").strip():
            kwargs["project"] = str(f_project)
        elif str(f_agent or "").strip():
            kwargs["agent"] = str(f_agent)
        elif str(f_prompt or "").strip():
            kwargs["prompt"] = str(f_prompt)
        elif str(f_target or "").strip():
            kwargs["target"] = str(f_target)
        sid = payload.get("session_id")
        if sid:
            kwargs["session_id"] = str(sid)
        if event_present:
            kwargs["event"] = str(f_event)
        model_value = payload.get("model")
        if model_value is not None:
            try:
                model_str = str(model_value).strip()
            except Exception:
                model_str = ""
            if model_str:
                existing_attrs = kwargs.get("attributes")
                attrs = dict(existing_attrs) if isinstance(existing_attrs, dict) else {}
                attrs["model"] = model_str
                kwargs["attributes"] = attrs
        return kwargs, None
    except Exception as e:
        return {}, {
            "ok": False,
            "error_code": 400,
            "description": str(e),
            "code": "BAD_REQUEST",
        }
