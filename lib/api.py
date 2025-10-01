from __future__ import annotations

from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from contextvars import ContextVar
import os
import sqlite3
import asyncio
from pathlib import Path as _Path
from call.lib import repo_db as call_repo
from call.lib import repo_fs as repo_fs
from call.lib.logging import debug_print
import builtins as _bi


_attribute_overrides_var: ContextVar[Dict[str, Any] | None] = ContextVar(
    "call_attribute_overrides",
    default=None,
)


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


def _compile_wildcard_regex(pattern: str | None):
    """Compile a case-insensitive full-string regex from a wildcard pattern ('*' -> '.*')."""
    if not pattern:
        return None
    try:
        import re as _re
        return _re.compile("^" + _re.escape(pattern).replace("\\*", ".*") + "$", _re.IGNORECASE)
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
    if s.startswith('@'):
        s = s[1:]
    sl = s.lower()
    if sl.endswith('.markdown'):
        s = s[:-9]
    elif sl.endswith('.md'):
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
                return lower.endswith(('.md', '.txt', '.json', '.yaml', '.yml', '.csv', '.tsv'))
            return mime.startswith('text/') or mime in ('application/json', 'application/yaml', 'application/x-yaml')

        for it in items:
            try:
                if not isinstance(it, dict):
                    continue
                if it.get('content') or it.get('base64'):
                    continue
                url_val = str(it.get('url') or '').strip()
                path_val = str(it.get('path') or '').strip()

                if url_val and _httpx:
                    try:
                        guess, _ = _mimes.guess_type(url_val)
                        with _httpx.Client(timeout=15.0, follow_redirects=True) as client:
                            resp = client.get(url_val)
                            data = resp.content or b""
                        if _is_text(guess, url_val):
                            it['content'] = data.decode('utf-8', 'replace')
                        else:
                            it['base64'] = _b64.b64encode(data).decode('ascii')
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
                                it['content'] = data.decode('utf-8')
                            except Exception:
                                it['content'] = data.decode('utf-8', 'replace')
                        else:
                            it['base64'] = _b64.b64encode(data).decode('ascii')
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        return

def list_prompts(*, project: Optional[str] = None, agent: Optional[str] = None, prompt: Optional[str] = None, state: Optional[str] = None, target: Optional[str] = None) -> List[Dict[str, Any]]:
    """Flat prompts listing facade for upper layers (CLI, Actions, Bot, MCP).

    Delegates to repo_db.list_prompts() via compatibility alias 'repo'.
    Do not swallow exceptions; let callers see failures.
    """
    return call_repo.list_prompts(project=project, agent=agent, state=state, target=target, prompt=prompt)

def interpret_target(
    *,
    project: str | None,
    agent: str | None,
    prompt: str | None,
    target: str | None,
) -> tuple[str | None, str | None, str | None, dict | None]:
    """Interpret a free-form target into (project, agent, prompt) using current filters.

    Precedence: prompt > agent > project. Supports '*' wildcards in target.

    Returns (project, agent, prompt, err) where err is a dict with keys
    { code, status, options, description } or None if no error.
    """
    # Apply normalization for non-CLI callers as well
    project = normalize_selector(project)
    agent = normalize_selector(agent)
    prompt = normalize_selector(prompt)
    target = normalize_selector(target)
    try:
        tgt = (target or "").strip()
        if not tgt:
            return project, agent, prompt, None

        low = tgt.lower()

        # 0) Explicit path: syntax (path:project/agent/prompt) with wildcards
        if low.startswith("path:"):
            spec = tgt[5:]
            parts = [p for p in spec.split("/")]
            pr_pat = parts[0] if len(parts) > 0 and parts[0] else None
            ag_pat = parts[1] if len(parts) > 1 and parts[1] else None
            pr_prompt = parts[2] if len(parts) > 2 and parts[2] else None
            # 3 segments -> prompt rows
            if pr_prompt is not None:
                rows = call_repo.list_prompts(project=pr_pat or None, agent=ag_pat or None, prompt=pr_prompt or None)
                if len(rows) == 1:
                    row = rows[0]
                    if not (project or "").strip():
                        project = row.get("project") or project
                    if not (agent or "").strip():
                        agent = row.get("agent") or agent
                    if not (prompt or "").strip():
                        prompt = row.get("prompt") or prompt
                    return project, agent, prompt, None
                if not rows:
                    return project, agent, prompt, {"code": "NO_DATA_FOUND", "status": 404, "description": "No prompt matched path", "options": []}
                return project, agent, prompt, {"code": "TOO_MANY_ROWS", "status": 400, "description": "Multiple prompts matched path", "options": rows[:20]}
            # 2 segments -> agent rows
            if ag_pat is not None:
                rows = call_repo.find_agents(project=pr_pat or None, agent=ag_pat or None)
                if len(rows) == 1:
                    row = rows[0]
                    if not (project or "").strip():
                        project = row.get("project") or project
                    if not (agent or "").strip():
                        agent = row.get("agent") or agent
                    return project, agent, prompt, None
                if not rows:
                    return project, agent, prompt, {"code": "NO_DATA_FOUND", "status": 404, "description": "No agent matched path", "options": []}
                return project, agent, prompt, {"code": "TOO_MANY_ROWS", "status": 400, "description": "Multiple agents matched path", "options": rows[:20]}
            # 1 segment -> project rows
            if pr_pat:
                rows = call_repo.find_projects(project=pr_pat or None)
                if len(rows) == 1:
                    row = rows[0]
                    if not (project or "").strip():
                        project = row.get("project") or project
                    return project, agent, prompt, None
                if not rows:
                    return project, agent, prompt, {"code": "NO_DATA_FOUND", "status": 404, "description": "No project matched path", "options": []}
                return project, agent, prompt, {"code": "TOO_MANY_ROWS", "status": 400, "description": "Multiple projects matched path", "options": rows[:20]}

        # 0.1) Explicit type prefixes p:/a:/r: (optional)
        if low.startswith("p:"):
            pat = tgt[2:]
            rows = call_repo.find_projects(project=pat or None)
            if len(rows) == 1:
                if not (project or "").strip():
                    project = rows[0].get("project") or project
                return project, agent, prompt, None
            if not rows:
                return project, agent, prompt, {"code": "NO_DATA_FOUND", "status": 404, "description": "No project matched", "options": []}
            return project, agent, prompt, {"code": "TOO_MANY_ROWS", "status": 400, "description": "Multiple projects matched", "options": rows[:20]}
        if low.startswith("a:"):
            pat = tgt[2:]
            # Support a:project/agent and a:agent
            p2, a2 = (pat.split("/", 1) + [""])[:2] if ("/" in pat) else (None, pat)
            rows = call_repo.find_agents(project=(p2 or None), agent=(a2 or None))
            if len(rows) == 1:
                row = rows[0]
                if not (project or "").strip():
                    project = row.get("project") or project
                if not (agent or "").strip():
                    agent = row.get("agent") or agent
                return project, agent, prompt, None
            if not rows:
                return project, agent, prompt, {"code": "NO_DATA_FOUND", "status": 404, "description": "No agent matched", "options": []}
            return project, agent, prompt, {"code": "TOO_MANY_ROWS", "status": 400, "description": "Multiple agents matched", "options": rows[:20]}
        if low.startswith("r:"):
            spec = tgt[2:]
            parts = [p for p in spec.split("/")]
            pr_pat = parts[0] if len(parts) > 0 and parts[0] else None
            ag_pat = parts[1] if len(parts) > 1 and parts[1] else None
            pr_prompt = parts[2] if len(parts) > 2 and parts[2] else (parts[0] if (len(parts) == 1) else None)
            rows = call_repo.list_prompts(project=pr_pat or None, agent=ag_pat or None, prompt=pr_prompt or None)
            if len(rows) == 1:
                row = rows[0]
                if not (project or "").strip():
                    project = row.get("project") or project
                if not (agent or "").strip():
                    agent = row.get("agent") or agent
                if not (prompt or "").strip():
                    prompt = row.get("prompt") or prompt
                return project, agent, prompt, None
            if not rows:
                return project, agent, prompt, {"code": "NO_DATA_FOUND", "status": 404, "description": "No prompt matched", "options": []}
            return project, agent, prompt, {"code": "TOO_MANY_ROWS", "status": 400, "description": "Multiple prompts matched", "options": rows[:20]}

        # 1) Exact Project match first (prefer project over agent and prompt when exact name matches)
        try:
            rows_exact = call_repo.find_projects(project=tgt)
        except Exception:
            rows_exact = []
        if rows_exact:
            # If any row has exact name equality (case-insensitive), treat as project
            try:
                tgt_low = tgt.lower()
                exacts = [r for r in rows_exact if str(r.get("project") or "").lower() == tgt_low]
            except Exception:
                exacts = rows_exact
            if len(exacts) == 1 and not (prompt or agent):
                if not (project or "").strip():
                    project = exacts[0].get("project") or project
                return project, agent, prompt, None
            # If multiple exacts (shouldn't happen), fall through to original logic

        # 2) Agent name/alias
        try:
            ra = resolve_agent(project=project, agent=tgt, prompt=prompt, target=None)
        except Exception:
            ra = {"ok": False}
        if isinstance(ra, dict) and ra.get("ok") and not (agent or "").strip():
            agent = tgt
            return project, agent, prompt, None
        elif isinstance(ra, dict) and (not ra.get("ok")) and str(ra.get("code")) == "TOO_MANY_ROWS":
            return project, agent, prompt, ra
        
        # 3) Prompt match via repo index (last)
        p_regex = _compile_wildcard_regex(tgt)
        prompt_matches: list[dict] = []
        if p_regex:
            try:
                items = call_repo.list_prompts(project=project, agent=agent)
            except Exception:
                items = []
            for x in (items or []):
                pid = str(x.get("prompt") or "")
                if pid and p_regex.match(pid):
                    prompt_matches.append(x)
        if prompt_matches and not (prompt or "").strip():
            if len(prompt_matches) == 1:
                match = prompt_matches[0]
                prompt = str(match.get("prompt") or tgt)
                # If project/agent are unset, derive from the match row
                if not (project or "").strip():
                    prj = match.get("project")
                    if isinstance(prj, str) and prj.strip():
                        project = prj
                if not (agent or "").strip():
                    ag = match.get("agent")
                    if isinstance(ag, str) and ag.strip():
                        agent = ag
                return project, agent, prompt, None
            return project, agent, prompt, {
                "code": "TOO_MANY_ROWS",
                "status": 400,
                "options": prompt_matches,
                "description": "Multiple prompts matched your criteria",
            }

        # 4) Project using repo DB (fuzzy/wildcard)
        rows = []
        try:
            rows = call_repo.find_projects(project=tgt)
        except Exception:
            rows = []
        if rows:
            if len(rows) == 1:
                if not (project or "").strip():
                    project = rows[0].get("project") or project
                return project, agent, prompt, None
            return project, agent, prompt, {
                "code": "TOO_MANY_ROWS",
                "status": 400,
                "options": rows[:20],
                "description": "Multiple projects matched your criteria",
            }
    finally:
        # No-op finally; early returns above handle most cases.
        pass

    # Default: return inputs unchanged with no error
    return project, agent, prompt, None

def build_input_payload(*, target: Optional[str], main_text: str, extra_context: Optional[list] = None, reply_text: Optional[str] = None, download: bool = False) -> tuple[str, dict | None]:
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
                u = t.lstrip('@').strip().strip(',.;:')
                ul = u.lower()
                if ul.endswith('.md'):
                    u = u[:-3]
                elif ul.endswith('.markdown'):
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
            for key in ("id", "type", "target", "project", "agent", "prompt", "state", "goal", "engine", "orchestration", "url"):
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
    if payload.get('target'):
        ordered['target'] = payload['target']
    if isinstance(reply_text, str) and reply_text.strip():
        ordered['replay'] = reply_text.strip()
    if (main_text or '').strip():
        ordered['input'] = (main_text or '').strip()
    if ctx_items:
        ordered['context'] = ctx_items
    if ordered:
        import json as _json
        return (_json.dumps(ordered, ensure_ascii=False), ordered)
    return ((main_text or ''), None)


def reload(*, repos: Optional[List[str]] = None, full_form: bool = True) -> Dict[str, Any]:
    """Filesystem scan and DB refresh (uniform name).

    Delegates to repo_fs.reload() (or scan()) and returns its dict result.
    """
    try:
        return repo_fs.reload(repos, full_form=full_form)
    except Exception as e:
        return {"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR"}


@dataclass
class RunnableConfig:
    """Minimal ready-to-run config consumed by app.build_and_run_agent."""

    # Primary identifiers and descriptive metadata
    id: str = ""
    type: str = ""  # 'project' | 'agent' | 'prompt'
    path: str = ""  # Repo-relative card path when available
    url: str = ""   # Public URL (e.g., GitHub blob) for the selected card
    goal: str = ""
    role: str = ""

    # Hierarchy identifiers resolved from metadata (prefer *_id values)
    project: str = ""
    agent: str = ""
    prompt: str = ""

    # Convenience selectors mirroring the original user request
    target: str = ""
    input: str = ""

    # Text payloads
    prompt_text: str = ""  # Raw prompt body extracted from the primary card prior to merges
    instructions: str = ""  # Final instructions dispatched to the runtime after merges/overlays
    card_text: str = ""  # Raw Markdown/structured card text (if available)

    # Runtime configuration and attributes
    model: str = "gpt-5"
    attributes: Dict[str, Any] = field(default_factory=dict)
    mcp: List[Dict[str, Any]] = field(default_factory=list)
    # Declared tools to enable for the run (e.g., ["WebSearchTool", "image_genetation_tool"]) 
    tools: List[str] = field(default_factory=list)

    # Additional execution context
    base_dir: str = ""

# todo исключить обращение к файловой системе, использовать repo.db и радиально упростить код исключив взаимное влиние prompt / agent /project за исключением model и model-settings /  model-settings-${model}

def build_runnable_instructions_config(
    *,
    project: Optional[str],
    agent: Optional[str],
    prompt: Optional[str] = None,
    target: Optional[str] = None,
    input: Optional[str] = None,
    attributes_override: Optional[Dict[str, Any]] = None,
) -> tuple[Optional[RunnableConfig], Optional[Dict[str, Any]]]:
    """Build a minimal runnable configuration DTO from repository selection."""

    import os as _os
    from pathlib import Path as _Path

    missing_card_exc = getattr(call_repo, "CardNotFoundError", FileNotFoundError)
    malformed_card_exc = getattr(call_repo, "CardFormatError", ValueError)

    @dataclass
    class _CardBundle:
        metadata: Dict[str, Any]
        prompt: str
        raw: str

    def _ensure_list(value: Any) -> List[Any]:
        if isinstance(value, _bi.list):
            return [item for item in value if item is not None]
        if value is None:
            return []
        return [value]

    def _string_items(value: Any) -> List[str]:
        result: List[str] = []
        for item in _ensure_list(value):
            if isinstance(item, _bi.str):
                text = item.strip()
                if text:
                    result.append(text)
            else:
                text = str(item).strip()
                if text:
                    result.append(text)
        return result

    def _model_from(meta: Dict[str, Any] | None) -> Optional[str]:
        if not isinstance(meta, dict):
            return None
        value = meta.get("model")
        if isinstance(value, _bi.str) and value.strip():
            return value.strip()
        for key, val in meta.items():
            if isinstance(key, str) and key.startswith("model-") and not key.startswith("model-params"):
                if isinstance(val, _bi.str) and val.strip():
                    return val.strip()
                return str(val)
        return None

    overrides_in = attributes_override
    if overrides_in is None:
        try:
            overrides_in = _attribute_overrides_var.get()
        except LookupError:
            overrides_in = None
    attribute_overrides = _normalize_attribute_overrides(overrides_in)

    try:
        project_sel, agent_sel, prompt_sel, target_err = interpret_target(
            project=project,
            agent=agent,
            prompt=prompt,
            target=target,
        )
    except Exception:
        project_sel, agent_sel, prompt_sel, target_err = project, agent, prompt, None

    if target_err is not None:
        return None, _error_payload(
            agent=(agent or ""),
            input=(input or ""),
            exc=target_err.get("description", "bad target"),
            status=int(target_err.get("status", 400)),
            code=str(target_err.get("code")),
            project=project,
            options=target_err.get("options") or [],
        )

    if not (str(project_sel or "").strip() or str(agent_sel or "").strip() or str(prompt_sel or "").strip()):
        cfg = RunnableConfig(
            id="",
            type="",
            path="",
            url="",
            goal="",
            role="",
            project="",
            agent="",
            prompt="",
            target=target or "",
            input=str(input or ""),
            prompt_text="",
            instructions=str(input or ""),
            card_text="",
            model=str(_os.environ.get("LLM_MODEL", "gpt-5")),
            attributes={},
            mcp=[],
            tools=[],
            base_dir="",
        )
        if attribute_overrides:
            attrs = dict(attribute_overrides)
            model_override = attrs.get("model")
            if isinstance(model_override, str) and model_override.strip():
                cfg.model = model_override.strip()
            cfg.attributes.update(attribute_overrides)
        try:
            setattr(cfg, "name", cfg.prompt or cfg.agent or cfg.project or cfg.id or "")
        except Exception:
            pass
        return cfg, None

    resolved_project = project_sel or project or ""
    resolved_agent = agent_sel or agent or ""
    resolved_prompt = prompt_sel or prompt or ""
    resolved: dict[str, Any] = {}

    needs_agent_resolution = bool(str(resolved_agent).strip() or str(resolved_prompt).strip())

    try:
        env = resolve_agent(
            project=project_sel,
            agent=agent_sel,
            prompt=prompt_sel,
            target=target,
        )
    except Exception as exc:
        return None, _error_payload(
            agent=(agent_sel or agent or ""),
            input=(input or ""),
            exc=exc,
            status=500,
            code="INTERNAL_ERROR",
            project=project_sel or project,
        )

    if not isinstance(env, dict):
        return None, _error_payload(
            agent=(agent_sel or agent or ""),
            input=(input or ""),
            exc="Agent resolution failed",
            status=500,
            code="INTERNAL_ERROR",
            project=project_sel or project,
        )

    if env.get("ok"):
        resolved = env.get("resolved") or {}
        resolved_project = resolved.get("project") or resolved_project
        resolved_agent = resolved.get("name") or resolved_agent
    else:
        code_val = str(env.get("code") or "").upper()
        original_project_requested = isinstance(project, str) and project.strip()
        if (
            not needs_agent_resolution
            and code_val == "TOO_MANY_ROWS"
            and not original_project_requested
            and (target or "").strip()
        ):
            resolved = {}
        else:
            return None, env

    try:
        project_row, agent_row, prompt_row = call_repo.select_unique_rows(
            project=resolved_project or None,
            agent=resolved_agent or None,
            prompt=resolved_prompt or None,
            require_project=False,
            require_agent=False,
            require_prompt=False,
        )
    except getattr(call_repo, "SelectionError", Exception) as exc:  # type: ignore[attr-defined]
        status = getattr(exc, "status", 400)
        code = getattr(exc, "code", "INTERNAL_ERROR")
        options = getattr(exc, "options", None)
        details = getattr(exc, "filters", None)
        if not isinstance(details, dict):
            details = None
        return None, _error_payload(
            agent=(resolved_agent or agent or ""),
            input=(input or ""),
            exc=str(exc),
            status=int(status or 400),
            code=str(code) if code is not None else "INTERNAL_ERROR",
            project=resolved_project or project,
            options=options if isinstance(options, _bi.list) else None,
            details=details,
        )

    requested_prompt_id = (resolved_prompt or "").strip()

    if requested_prompt_id and prompt_row is None:
        try:
            fallback_candidates = call_repo.find_prompts(
                project=resolved_project or None,
                agent=None,
                prompt=requested_prompt_id,
                state=None,
                target=None,
            )
        except Exception:
            fallback_candidates = []

        valid_fallbacks: List[Dict[str, Any]] = []
        for row in fallback_candidates or []:
            if not isinstance(row, dict):
                continue
            pj = str(row.get("project") or "").strip()
            ag_name = str(row.get("agent") or "").strip()
            if not (pj and ag_name):
                continue
            valid_fallbacks.append(row)

        if len(valid_fallbacks) > 1:
            return None, _error_payload(
                agent=(resolved_agent or agent or ""),
                input=(input or ""),
                exc="Multiple prompts matched your criteria",
                status=400,
                code="TOO_MANY_ROWS",
                project=resolved_project or project,
                options=valid_fallbacks[:20],
                details={"prompt": requested_prompt_id},
            )

        if len(valid_fallbacks) == 1:
            prompt_row = valid_fallbacks[0]
            resolved_agent = prompt_row.get("agent") or resolved_agent
            resolved_project = prompt_row.get("project") or resolved_project

            if agent_row is None:
                agent_name = prompt_row.get("agent")
                project_name = prompt_row.get("project") or resolved_project
                try:
                    agent_candidates = call_repo.find_agents(
                        project=project_name or None,
                        agent=agent_name or None,
                        target=None,
                    )
                except Exception:
                    agent_candidates = []
                if len(agent_candidates) == 1:
                    agent_row = agent_candidates[0]
            if project_row is None:
                project_name = prompt_row.get("project") or resolved_project
                try:
                    project_candidates = call_repo.find_projects(
                        project=project_name or None,
                        target=None,
                    )
                except Exception:
                    project_candidates = []
                if len(project_candidates) == 1:
                    project_row = project_candidates[0]

        elif fallback_candidates:
            return None, _error_payload(
                agent=(resolved_agent or agent or ""),
                input=(input or ""),
                exc="Prompt metadata could not be parsed",
                status=400,
                code="BAD_CARD_FORMAT",
                project=resolved_project or project,
                details={"prompt": requested_prompt_id},
            )

    resolved_prompts = resolved.get("prompts") if isinstance(resolved, dict) else None
    resolved_prompts_list = []
    if isinstance(resolved_prompts, _bi.list):
        for item in resolved_prompts:
            try:
                text = str(item).strip()
            except Exception:
                continue
            if text:
                resolved_prompts_list.append(text)
    elif isinstance(resolved_prompts, _bi.str) and resolved_prompts.strip():
        resolved_prompts_list = [resolved_prompts.strip()]

    requested_prompt = resolved_prompt

    if resolved_prompt and prompt_row is None:
        if resolved_prompts_list:
            if resolved_prompt not in resolved_prompts_list:
                return None, _error_payload(
                    agent=(resolved_agent or agent or ""),
                    input=(input or ""),
                    exc="No prompt found matching the provided filters",
                    status=404,
                    code="NO_DATA_FOUND",
                    project=resolved_project or project,
                    details={
                        "prompt": resolved_prompt,
                        "agent": resolved_agent or agent or "",
                        "project": resolved_project or project or "",
                    },
                )
        else:
            resolved_prompt = ""

    resolved_has_agent_source = False
    if isinstance(resolved, dict) and resolved_agent:
        resolved_has_agent_source = bool(resolved.get("path") or resolved.get("id") or resolved.get("target"))

    if resolved_agent and agent_row is None and not resolved_has_agent_source:
        return None, _error_payload(
            agent=(resolved_agent or agent or ""),
            input=(input or ""),
            exc="No agent found matching the provided filters",
            status=404,
            code="NO_DATA_FOUND",
            project=resolved_project or project,
            details={"agent": resolved_agent, "project": resolved_project or project or ""},
        )

    if resolved_project and project_row is None and not (agent_row or prompt_row):
        return None, _error_payload(
            agent=(resolved_agent or agent or ""),
            input=(input or ""),
            exc="No project found matching the provided filters",
            status=404,
            code="NO_DATA_FOUND",
            project=resolved_project or project,
            details={"project": resolved_project or project or ""},
        )

    def _load(row: Dict[str, Any] | None) -> _CardBundle:
        if not row:
            return _CardBundle({}, "", "")
        card_id = row.get("id") or row.get("target")
        if not card_id:
            raise missing_card_exc("card id missing")
        meta, body, raw = call_repo.get_card(card_id)
        meta_dict = dict(meta or {}) if isinstance(meta, dict) else {}
        meta_dict.pop("prompt", None)
        return _CardBundle(meta_dict, str(body or ""), str(raw or ""))

    try:
        project_card = _load(project_row)
        agent_card = _load(agent_row)
        prompt_card = _load(prompt_row)
    except malformed_card_exc as exc:
        return None, _error_payload(
            agent=(resolved_agent or agent or ""),
            input=(input or ""),
            exc=str(exc),
            status=400,
            code="BAD_CARD_FORMAT",
            project=resolved_project or project,
        )
    except missing_card_exc as exc:
        return None, _error_payload(
            agent=(resolved_agent or agent or ""),
            input=(input or ""),
            exc=str(exc),
            status=404,
            code="NO_DATA_FOUND",
            project=resolved_project or project,
        )

    if prompt_row:
        selected_kind = "prompt"
    elif agent_row or resolved_agent:
        selected_kind = "agent"
    else:
        selected_kind = "project"

    selected_row = prompt_row or agent_row or project_row or {}
    primary_card = (
        prompt_card
        if selected_kind == "prompt"
        else agent_card
        if selected_kind == "agent"
        else project_card
    )
    fallbacks = [card for card in (prompt_card, agent_card, project_card) if card is not primary_card]

    instructions = primary_card.prompt or ""
    if not instructions.strip():
        for candidate in [c.prompt for c in fallbacks] + [primary_card.raw] + [c.raw for c in fallbacks]:
            if isinstance(candidate, str) and candidate.strip():
                instructions = candidate
                break

    combined_meta: Dict[str, Any] = {}
    for bundle in (project_card, agent_card, prompt_card):
        combined_meta.update(bundle.metadata)

    def _meta_value(key: str) -> Any:
        for bundle in (prompt_card, agent_card, project_card):
            meta = bundle.metadata
            if not isinstance(meta, dict):
                continue
            if key in meta and meta[key] not in (None, "", [], {}):
                return meta[key]
        return None

    role_val = _meta_value("role") or ""
    goal_val = _meta_value("goal") or _meta_value("purpose") or _meta_value("title") or ""
    if not goal_val:
        goal_val = str(selected_row.get("goal") or "")

    mcp_list = _ensure_list(_meta_value("mcp"))
    tools_list = _string_items(_meta_value("tools"))

    def _row_value(row: Dict[str, Any] | None, key: str, fallback: Any) -> Any:
        if isinstance(row, dict):
            value = row.get(key)
            if value not in (None, ""):
                return value
        return fallback

    project_value = _row_value(project_row, "project", resolved_project)
    agent_value = _row_value(agent_row, "agent", resolved_agent)
    prompt_value = _row_value(prompt_row, "prompt", requested_prompt)

    selected_id = selected_row.get("id") or selected_row.get("target") or ""
    path_hint = selected_row.get("rel_path") or selected_row.get("path") or ""
    url_val = selected_row.get("url") or ""

    base_dir = ""
    if selected_row.get("path"):
        try:
            base_dir = str(_Path(selected_row["path"]).parent)
        except Exception:
            base_dir = ""

    if selected_kind == "prompt":
        prompt_field = str(prompt_value or "")
    elif selected_kind == "project":
        prompt_field = ""
    else:
        prompt_field = str(requested_prompt or prompt_value or "")

    cfg = RunnableConfig(
        id=str(selected_id or ""),
        type=selected_kind,
        path=str(path_hint or ""),
        url=str(url_val or ""),
        goal=str(goal_val or ""),
        role=str(role_val or ""),
        project=str(project_value or ""),
        agent=str(agent_value or "") if selected_kind != "project" else "",
        prompt=prompt_field,
        target=target or "",
        input=str(input or ""),
        prompt_text=primary_card.prompt,
        instructions=str(instructions or ""),
        card_text=primary_card.raw,
        model=
        _model_from(prompt_card.metadata)
        or _model_from(agent_card.metadata)
        or _model_from(project_card.metadata)
        or "",
        attributes=combined_meta,
        mcp=mcp_list,
        tools=tools_list,
        base_dir=base_dir,
    )

    if not cfg.model:
        try:
            cfg.model = str(_os.environ.get("LLM_MODEL", "gpt-5"))
        except Exception:
            cfg.model = "gpt-5"

    if attribute_overrides:
        attrs = dict(cfg.attributes or {})
        for key, value in attribute_overrides.items():
            if value is None:
                continue
            if key == "model":
                override_model = str(value).strip() if isinstance(value, str) else str(value)
                if override_model:
                    cfg.model = override_model
                    attrs["model"] = override_model
                continue
            attrs[key] = value
        cfg.attributes = attrs

    try:
        setattr(cfg, "name", cfg.prompt or cfg.agent or cfg.project or cfg.id or "")
    except Exception:
        pass

    return cfg, None

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
        message = msg_attr if isinstance(msg_attr, str) and msg_attr else str(exc) or "Error"
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
        except Exception:
            pass

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
    # Event short-circuit: when event is supplied, acknowledge without invoking the pipeline
    if event is not None:
        return {"ok": True, "event": str(event), "agents": []}

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

    # Lazily import app-layer functions to avoid hard import at module load time
    from call.app import call as app_call

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

    # cfg is ready; dump a normalized snapshot in DEBUG
    try:
        from dataclasses import asdict as _asdict
        snap = _asdict(cfg)
        snap["instructions_len"] = len(cfg.instructions or "")
        snap.pop("instructions", None)
        debug_print("[api]", "[CFG]", __import__('json').dumps(snap, ensure_ascii=False, indent=2))
    except Exception:
        pass

    # Initialize bot: if a project is provided, pass it; otherwise allow app layer
    # to prefer CALL_TELEGRAM_TOKEN or TELEGRAM_TOKEN per its own logic.
    try:
        await app_call.init_bot(project_name=(project if (project or "").strip() else None))
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
    #   3) Else: do not route to Telegram and do not create a session
    sel_chat: Optional[int] = None
    sel_thread: Optional[int] = None
    sid_override = (session_id or "").strip()
    if sid_override:
        c, t = _parse_session_id(sid_override)
        sel_chat, sel_thread = c, t
    else:
        if (chat_id is not None) or (thread_id is not None):
            sel_chat = chat_id if chat_id is not None else app_call.TELEGRAM_CHAT_ID
            sel_thread = thread_id if thread_id is not None else (app_call.TELEGRAM_THREAD_ID or None)
        else:
            sel_chat, sel_thread = None, None

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
            dump_task = asyncio.create_task(_dump_tasks_periodically(dump_period_s, dump_fp))

        try:
            # Use the app layer context manager to build and run the agent once with a ready config.
            async with app_call.build_and_run_agent(cfg=cfg, user_input=((getattr(cfg, "input", None) or input) or "")) as (agent_obj, _cfg, _session):
                final_output = getattr(_cfg, "_last_final_output", None)
                try:
                    actual_sid = getattr(_session, "id", None)
                except Exception:
                    actual_sid = None
        except Exception as e:
            # Convert pipeline errors to structured error; map known tracing 403 to 403
            msg = str(e)
            status = 500
            err_code = "PIPELINE_ERROR"
            details = None
            if (
                ("Tracing client error" in msg) or ("request_forbidden" in msg) or ("unsupported_country_region_territory" in msg)
            ):
                status = 403
                err_code = "REQUEST_FORBIDDEN"
                try:
                    import json as _json
                    brace = msg.find("{")
                    if brace != -1:
                        details = _json.loads(msg[brace:])
                except Exception:
                    details = None
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
            return _error_payload(agent=(cfg.id or ""), input=(input or ""), exc=e, status=status, echo=echo, debug=debug, code=err_code, project=cfg.project, details=details, session_id=(session_id or None))
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
        _reset_override()

    # If the pipeline returned a plain-text error (e.g., "Error: ...\n\nTraceback ..."),
    # convert it to a structured error envelope to avoid printing stack traces to users.
    if isinstance(final_output, str) and final_output.strip().lower().startswith("error:"):
        msg = final_output.strip()
        # Derive a concise description (first line without "Error: ")
        first_line = msg.splitlines()[0]
        desc = first_line[len("Error:"):].strip() if first_line.lower().startswith("error:") else first_line
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
    session_id_out = None
    try:
        if isinstance(session_id, str) and session_id.strip():
            session_id_out = session_id
        elif 'actual_sid' in locals() and actual_sid:
            session_id_out = actual_sid
        elif selected_chat_id is not None:
            session_id_out = (
                f"{selected_chat_id}:{selected_thread_id}" if (selected_thread_id is not None) else f"{selected_chat_id}"
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
    try:
        return asyncio.run(
            call_async(
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
        )
    except Exception as e:
        return _error_payload(agent or "", input or "", e, status=500, echo=echo, debug=debug, code="INTERNAL_ERROR", project=project, session_id=(session_id or None))


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


def list(*, project: Optional[str] = None, agent: Optional[str] = None, prompt: Optional[str] = None, state: Optional[str] = None, target: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return hierarchical structure from the repo DB.

    Delegates to call.lib.repo.list(), which applies wildcard filters and returns:
      [ { name: <project>, agents: [ { name, aliases, prompts, path, ... } ] } ]
    """
    try:
        return call_repo.list(project=project, agent=agent, prompt=prompt, state=state, target=target)
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
            items.append(_serialize_model_item(entry))
        except Exception:
            continue
    return items


def resolve_agent(*, project: Optional[str] = None, agent: Optional[str] = None, prompt: Optional[str] = None, target: Optional[str] = None) -> Dict[str, Any]:
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
                    return _error_payload(agent=agent, input="", exc="Multiple agents matched your criteria", status=400, code="TOO_MANY_ROWS", project=project, options=rows[:20])
                r = rows[0]
                return {"ok": True, "resolved": {"project": r.get("project"), "name": r.get("agent"), "path": r.get("path"), "aliases": [], "prompts": []}}
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
            recs = call_repo.list_prompts(project=(project or None), agent=(agent or None), prompt=prompt)
            if not recs:
                alt_recs: list[dict] = []
                try:
                    alt_recs = call_repo.list_prompts(project=None, agent=None, prompt=prompt)
                except Exception:
                    alt_recs = []
                if alt_recs:
                    valid_alt = [r for r in alt_recs if (str(r.get("project") or "").strip() and str(r.get("agent") or "").strip())]
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
                return _error_payload(
                    agent=(agent or ""),
                    input="",
                    exc="No prompt found matching criteria",
                    status=404,
                    code="NO_DATA_FOUND",
                    project=project,
                    options=[],
                )
            if len(recs) > 1:
                return _error_payload(agent=(agent or ""), input="", exc="Multiple prompts matched your criteria", status=400, code="TOO_MANY_ROWS", project=project, options=recs[:20])
            pr = recs[0]
            pj = pr.get("project") or project
            ag = pr.get("agent") or agent
            # Agent row must exist
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
            return {"ok": True, "resolved": {"project": ar.get("project"), "name": ar.get("agent"), "path": ar.get("path"), "aliases": [], "prompts": []}}

        # 3) Only project provided -> ambiguous
        if isinstance(project, str) and project.strip():
            opts = call_repo.find_agents(project=project, agent=None)
            if len(opts) == 1:
                r = opts[0]
                return {"ok": True, "resolved": {"project": r.get("project"), "name": r.get("agent"), "path": r.get("path"), "aliases": [], "prompts": []}}
            return _error_payload(
                agent=(agent or ""),
                input="",
                exc=("No agent found matching criteria" if not opts else "Multiple agents matched your criteria"),
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
        return _error_payload(agent=(agent or ""), input="", exc=e, status=500, code="INTERNAL_ERROR", project=project)


async def clear_session(name: Optional[str], *, chat_id: Optional[int], thread_id: Optional[int]) -> Dict[str, Any]:
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
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        has_messages = bool(cur.fetchone())
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
        has_sessions = bool(cur.fetchone())

        # Single candidate: new format only
        sids: List[str] = []
        candidate = _sid_new(int(chat_id), thread_id)
        if has_sessions:
            cur.execute("SELECT id FROM sessions WHERE id = ?", (candidate,))
            sids += [row[0] for row in cur.fetchall()]
        if has_messages:
            cur.execute("SELECT DISTINCT session_id FROM messages WHERE session_id = ?", (candidate,))
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


def api_interpret_exec_payload(payload: Dict[str, object]) -> Tuple[Dict[str, object], Optional[Dict[str, object]]]:
    """Validate and normalize a single exec payload into kwargs for call().

    Rules:
    - Exactly one of project|agent|prompt|target must be present (truthy string).
    - Always use the full payload JSON as the input string.

    Returns (kwargs, err) where kwargs can be passed to call(**kwargs) and err is an error envelope on validation error.
    """
    try:
        try:
            import json
            debug_print(
                "[api]", "interpret_exec_payload:|-\n",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
        except Exception:
            pass
        # Determine exactly one among project|agent|prompt|target (allow zero when event present)
        f_project = payload.get("project")
        f_agent = payload.get("agent")
        f_prompt = payload.get("prompt")
        f_target = payload.get("target")
        f_event = payload.get("event")
        fields = [f for f in [f_project, f_agent, f_prompt, f_target] if (str(f or "").strip())]
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
        import json as _json
        # Always use full payload JSON as input
        inp = _json.dumps(payload, ensure_ascii=False)
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

