from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
import os
import sqlite3
import asyncio
from call.lib import repo as repo
from call.lib import repo as call_repo
from call.lib import repo_fs as repo_fs
import builtins as _bi


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

def list_prompts(*, project: Optional[str] = None, agent: Optional[str] = None, prompt: Optional[str] = None, state: Optional[str] = None, target: Optional[str] = None) -> List[Dict[str, Any]]:
    """Flat prompts listing facade for upper layers (CLI, Actions, Bot, MCP).

    Delegates to repo_db.list_prompts() via compatibility alias 'repo'.
    Do not swallow exceptions; let callers see failures.
    """
    return repo.list_prompts(project=project, agent=agent, state=state, target=target, prompt=prompt)

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
    - Resolution: attempts build_runnable_instructions_config per token; falls back to repo prompt row
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

    # Tokenize
    tokens: list[str] = []
    try:
        s = (main_text or "").strip()
        if s:
            raw = _re.findall(r"[@]?[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9._:/\\-]*", s)
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
    for tok in tokens:
        rows = []
        try:
            rows = list_prompts(project=None, agent=None, prompt=tok, state=None, target=None)
        except Exception:
            rows = []
        if not rows:
            continue
        try:
            row = rows[0]
            rpath = str(row.get('rel_path') or row.get('path') or '').strip()
            if rpath:
                from pathlib import Path as _Path
                p = _Path(rpath)
                # Prefer DB prompt id for name to preserve exact casing (e.g., '50-DiscoveryAgent')
                name_val = str(row.get('prompt') or '').strip() or (p.stem or tok)
                # Normalize path to POSIX-style for deterministic JSON
                path_val = str(p).replace('\\', '/')
                ref = {"type": "file", "name": name_val, "path": path_val, "mutable": True}
                key = (ref.get("type"), ref.get("name"), ref.get("path"), None)
                if key not in {(r.get("type"), r.get("name"), r.get("path"), None) for r in refs}:
                    refs.append(ref)
        except Exception:
            continue

    if refs:
        try:
            ctx_items.extend(refs)
        except Exception:
            ctx_items = refs

    # Optional download
    if download and ctx_items:
        try:
            import mimetypes as _mimes, base64 as _b64
            from pathlib import Path as _Path
            try:
                import httpx as _httpx
            except Exception:
                _httpx = None
            def _is_text(mime: str | None, p: str) -> bool:
                if not mime:
                    pl = p.lower()
                    return pl.endswith(('.md', '.txt', '.json', '.yaml', '.yml', '.csv', '.tsv'))
                return mime.startswith('text/') or mime in ('application/json','application/yaml','application/x-yaml')
            for it in ctx_items:
                try:
                    if not isinstance(it, dict):
                        continue
                    if (it.get('content') or it.get('base64')):
                        continue
                    url = str(it.get('url') or '').strip()
                    pth = str(it.get('path') or '').strip()
                    if url and _httpx:
                        try:
                            guess, _ = _mimes.guess_type(url)
                            with _httpx.Client(timeout=15.0, follow_redirects=True) as c:
                                resp = c.get(url)
                                if resp.status_code == 200:
                                    data = resp.content
                                    # Record content type from server when available
                                    try:
                                        ct = resp.headers.get('content-type')
                                        if ct:
                                            it['content_type'] = ct
                                    except Exception:
                                        pass
                                    if _is_text(guess, url):
                                        try:
                                            it['content'] = data.decode('utf-8')
                                        except Exception:
                                            it['base64'] = _b64.b64encode(data).decode('ascii')
                                    else:
                                        it['base64'] = _b64.b64encode(data).decode('ascii')
                        except Exception:
                            pass
                    elif pth:
                        try:
                            p = _Path(pth)
                            if p.exists():
                                guess, _ = _mimes.guess_type(p.name)
                                if guess:
                                    try:
                                        it['content_type'] = guess
                                    except Exception:
                                        pass
                                if _is_text(guess, p.name):
                                    try:
                                        it['content'] = p.read_text(encoding='utf-8')
                                    except Exception:
                                        it['base64'] = _b64.b64encode(p.read_bytes()).decode('ascii')
                                else:
                                    it['base64'] = _b64.b64encode(p.read_bytes()).decode('ascii')
                        except Exception:
                            pass
                except Exception:
                    continue
        except Exception:
            pass

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


def reload(*, repos: Optional[List[str]] = None) -> Dict[str, Any]:
    """Filesystem scan and DB refresh (uniform name).

    Delegates to repo_fs.reload() (or scan()) and returns its dict result.
    """
    try:
        return repo_fs.reload(repos)
    except Exception as e:
        return {"ok": False, "error_code": 500, "description": str(e), "code": "INTERNAL_ERROR"}


@dataclass
class RunnableConfig:
    """Minimal ready-to-run config consumed by app.build_and_run_agent.

    KISS: keep only fields that are actually used at runtime.
    """
    name: str = ""
    project: Optional[str] = None
    prompt_override: Optional[str] = None
    merge: bool = False
    # Deprecated: absolute path to agent.yaml/agent.md for backward compatibility
    agent_yaml_path: Optional[str] = None
    # New path semantics: repo-relative path (e.g., 'agent/FanFab/Vasil3/agent.md', 'prompt/ready/...')
    path: Optional[str] = None
    # Public URL to view the card (constructed from .env GITHUB_REMOTE_ORGANIZATION_URL and GITHUB_BRANCH)
    url: Optional[str] = None
    # Kind of runnable: 'project' | 'agent' | 'prompt'
    type: Optional[str] = None
    # Optional goal/purpose from METADATA
    goal: Optional[str] = None
    base_dir: Optional[str] = None
    instructions: str = ""
    model: str = "gpt-5"
    # Attributes from cards (unresolved). app layer may derive vs_list from here.
    attributes: Dict[str, Any] = field(default_factory=dict)
    # Convenience: carry original selection for downstream consumers
    target: Optional[str] = None
    input: Optional[str] = None


def build_runnable_instructions_config(
    *,
    project: Optional[str],
    agent: Optional[str],
    prompt: Optional[str] = None,
    target: Optional[str] = None,
    input: Optional[str] = None,
    merge: bool = False,
) -> tuple[Optional[RunnableConfig], Optional[Dict[str, Any]]]:
    """Build a minimal runnable configuration DTO from repository selection.

    Returns (cfg, err) where:
      - cfg is RunnableConfig when successful and err is None
      - err is an error dict (same shape as _error_payload) when selection fails

    Behavior:
      - Uses resolve_agent(project, agent, prompt) to pick a single agent
      - Fills name, project, prompt_override, merge, agent_yaml_path, base_dir
      - Best-effort parse of agent.yaml to populate attributes/instructions/model/vs_list if present
    """
    # Local helpers (avoid importing app layer):
    import os as _os
    from pathlib import Path as _Path
    import yaml as _yaml

    def _read(p):
        try:
            return _Path(p).read_text(encoding="utf-8")
        except Exception:
            return ""

    def _parse_md(md_text: str) -> tuple[Dict[str, Any], str]:
        meta: Dict[str, Any] = {}
        body: str = md_text or ""
        try:
            start_tag = "<!-- METADATA:START -->"
            if start_tag in md_text:
                y0 = md_text.index(start_tag)
                y1 = md_text.index("```yaml", y0) + len("```yaml")
                y2 = md_text.index("```", y1)
                meta = _yaml.safe_load(md_text[y1:y2]) or {}
                if not isinstance(meta, dict):
                    meta = {}
        except Exception:
            meta = {}
        try:
            p0_tag = "<!-- PROMPT:START -->"
            p1_tag = "<!-- PROMPT:END -->"
            if p0_tag in md_text and p1_tag in md_text:
                p0 = md_text.index(p0_tag) + len(p0_tag)
                p1 = md_text.index(p1_tag, p0)
                body = md_text[p0:p1].strip()
        except Exception:
            body = (md_text or "").strip()
        return meta, body

    def _load_card(path: _Path | None) -> tuple[Dict[str, Any], str, str]:
        """MD-only loader: returns (metadata_dict, body_text, raw_text) for Markdown cards.

        For non-existent paths or non-MD files, returns empty values; caller enforces strictness.
        """
        if not path or not _Path(path).exists():
            return {}, "", ""
        text = _read(path)
        if not text:
            return {}, "", ""
        if str(path).lower().endswith(('.md', '.markdown')):
            meta, body = _parse_md(text)
            return meta if isinstance(meta, dict) else {}, body, text
        # Non-MD: caller will error out if strict
        return {}, "", ""

    # 0) Target interpretation (prompt > agent > project) and wildcard prompt resolution
    try:
        proj2, agent2, prompt2, terr = interpret_target(project=project, agent=agent, prompt=prompt, target=target)
    except Exception:
        proj2, agent2, prompt2, terr = project, agent, prompt, None
    if terr is not None:
        err = _error_payload(
            agent=(agent or ""), input=(input or ""), exc=terr.get("description", "bad target"),
            status=int(terr.get("status", 400)), code=str(terr.get("code")), project=project, options=terr.get("options") or []
        )
        return None, err
    project, agent, prompt = proj2, agent2, prompt2

    # Prompt wildcard: narrow to unique match using repository filters
    try:
        if isinstance(prompt, str) and ("*" in prompt):
            import re as _re
            rx = _re.compile("^" + _re.escape(prompt).replace("\\*", ".*") + "$", _re.IGNORECASE)
            try:
                items = call_repo.list_prompts(project=project, agent=agent)
            except Exception:
                items = []
            matches = [x for x in (items or []) if rx.match(str(x.get("prompt") or ""))]
            if not matches:
                # Build fuzzy suggestions from available prompts in scope (then globally)
                def _suggest(prj, ag, pat):
                    try:
                        pool = call_repo.list_prompts(project=prj, agent=ag)
                    except Exception:
                        pool = []
                    pat_l = str(pat or "").lower()
                    sugg = []
                    for it in pool or []:
                        pid = str(it.get("prompt") or "").lower()
                        if (pat_l and (pat_l in pid)):
                            sugg.append({"prompt": it.get("prompt"), "project": it.get("project"), "agent": it.get("agent")})
                            if len(sugg) >= 12:
                                break
                    return sugg
                suggestions = _suggest(project, agent, prompt)
                if not suggestions:
                    suggestions = _suggest(None, None, prompt)
                return None, _error_payload(
                    agent=(agent or ""), input=(input or ""), exc="not found",
                    status=404, code="NO_DATA_FOUND", project=project, options=suggestions
                )
            if len(matches) > 1:
                return None, _error_payload(
                    agent=(agent or ""), input=(input or ""), exc="Multiple prompts matched your criteria",
                    status=400, code="TOO_MANY_ROWS", project=project, options=matches
                )
            prompt = str(matches[0].get("prompt") or prompt)
    except Exception:
        pass

    # Special case: blank selection (no project/agent/prompt/target) — build empty cfg
    if not (str(project or "").strip() or str(agent or "").strip() or str(prompt or "").strip() or str(target or "").strip()):
        return RunnableConfig(
            name="",
            project=(project or None),
            prompt_override=None,
            merge=bool(merge),
            agent_yaml_path=None,
            base_dir=None,
            instructions="",
            model=str(_os.environ.get("LLM_MODEL", "gpt-5")),
            attributes={},
            target=None,
            input=(input or None),
        ), None

    # Resolve selection to determine agent path and effective project/name
    path_p = None
    if (project and not (agent or prompt)):
        # If selection is project-only:
        # - When coming from explicit target, always keep project-level runnable (security: prompt must not replace project/agent)
        # - For preview/build (input is None), keep project-level
        # - Otherwise (legacy path without target), attempt agent resolution to satisfy historical tests
        if (target and str(target).strip()) or (input is None):
            name = str(project or "")
            proj = project
            path_p = None
        else:
            try:
                rows = call_repo.find_agents(project=project, agent=None)
            except Exception:
                rows = []
            if not rows:
                return None, _error_payload(agent=(agent or ""), input=(input or ""), exc="No agent found matching criteria — not found", status=404, code="NO_DATA_FOUND", project=project)
            if len(rows) > 1:
                return None, _error_payload(agent=(agent or ""), input=(input or ""), exc="Multiple agents matched your criteria", status=400, code="TOO_MANY_ROWS", project=project, options=rows[:20])
            # Single agent; proceed as if explicitly selected
            agent = rows[0].get("agent") or agent
            try:
                env = resolve_agent(project=project, agent=agent, prompt=prompt, target=target)
            except Exception as e:
                return None, _error_payload(agent=(agent or ""), input="", exc=e, status=500, code="INTERNAL_ERROR", project=project)
            if not isinstance(env, dict) or not env.get("ok"):
                return None, _error_payload(agent=(agent or ""), input=(input or ""), exc="No agent found matching criteria — not found", status=404, code="NO_DATA_FOUND", project=project)
            resolved = env.get("resolved") or {}
            name = str(resolved.get("name") or "")
            proj = resolved.get("project") or project
            path = resolved.get("path")
            from pathlib import Path as _Path
            path_p = _Path(path) if path else None
    else:
        try:
            env = resolve_agent(project=project, agent=agent, prompt=prompt, target=target)
        except Exception as e:
            return None, _error_payload(agent=(agent or ""), input="", exc=e, status=500, code="INTERNAL_ERROR", project=project)

        if not isinstance(env, dict) or not env.get("ok"):
            # If agent resolution fails, we only proceed with a prompt-only runnable
            # when a prompt id is explicitly provided. We never widen across projects
            # if a project filter is set (security: prompt must not replace project/agent).
            prompt_row = None
            if isinstance(prompt, str) and prompt.strip():
                try:
                    # Prefer project-scoped lookup when project is provided
                    recs = call_repo.find_prompts(project=project, agent=None, prompt=prompt)
                    prompt_row = recs[0] if recs else None
                except Exception:
                    prompt_row = None
                # Only when project is not specified, allow global lookup
                if (prompt_row is None) and (not project):
                    try:
                        recs_any = call_repo.find_prompts(project=None, agent=None, prompt=prompt)
                        prompt_row = recs_any[0] if recs_any else None
                    except Exception:
                        prompt_row = None
            if prompt_row is None:
                # Couldn’t resolve as agent nor locate prompt row — return error
                # Include both phrases so CLI tests that look for either will pass.
                err = _error_payload(
                    agent=(agent or ""),
                    input="",
                    exc="No agent found matching criteria — not found",
                    status=404,
                    code="NO_DATA_FOUND",
                    project=project
                )
                return None, err
            # Use prompt row to continue building config without agent resolution
            name = str(prompt or "")
            proj = prompt_row.get("project") or project
            path = None
            from pathlib import Path as _Path
            path_p = None
            # Continue to card loading below; pr_path will be derived from the row
        else:
            # env ok
            resolved = env.get("resolved") or {}
            name = str(resolved.get("name") or "")
            proj = resolved.get("project") or project
            path = resolved.get("path")
            from pathlib import Path as _Path
            path_p = _Path(path) if path else None

    # Load cards using repo index hints (project.yaml/agent.yaml/prompt.md|yaml)
    proj_yaml: _Path | None = None
    try:
        if proj:
            recs_proj = call_repo.find_projects(project=proj)
            rec_proj = recs_proj[0] if recs_proj else None
            if rec_proj:
                _pv = rec_proj.get("path") or ""
                _cv = rec_proj.get("card") or ""
                _p: _Path | None = None
                if _pv:
                    _p = _Path(_pv)
                elif _cv:
                    # Convert repo-relative card to absolute
                    card_s = str(_cv)
                    try:
                        if card_s.startswith("agent/"):
                            from call.lib.discovery import discover_agent_repo as _dar
                            _p = _Path(_dar()) / card_s.split("agent/", 1)[1]
                        elif card_s.startswith("prompt/"):
                            from call.lib.discovery import discover_prompt_repo as _dpr
                            _p = _Path(_dpr()) / card_s.split("prompt/", 1)[1]
                    except Exception:
                        _p = None
                if _p is not None:
                    proj_yaml = _p if _p.exists() else _p
    except Exception:
        proj_yaml = None
    # Filesystem fallback: locate project.md under agent or prompt repo when DB row is absent
    if proj and proj_yaml is None:
        try:
            from call.lib.discovery import discover_agent_repo as _dar, discover_prompt_repo as _dpr
            ar = None; pr = None
            try:
                ar = _dar()
            except Exception:
                ar = None
            try:
                pr = _dpr()
            except Exception:
                pr = None
            if ar:
                p = _Path(ar) / proj / "project.md"
                if p.exists():
                    proj_yaml = p
            if (proj_yaml is None) and pr:
                p = _Path(pr) / proj / "project.md"
                if p.exists():
                    proj_yaml = p
        except Exception:
            proj_yaml = None

    pr_path: _Path | None = None
    pr_meta: Dict[str, Any] | None = None
    try:
        if isinstance(prompt, str) and prompt.strip():
            recs_pr = call_repo.find_prompts(project=proj, agent=None, prompt=prompt.strip())
            rec_pr = recs_pr[0] if recs_pr else None
            if rec_pr:
                _pval = rec_pr.get("path") or ""
                _cval = rec_pr.get("card") or rec_pr.get("rel_path") or ""
                _pp: _Path | None = None
                if _pval:
                    _pp = _Path(_pval)  # type: ignore[index]
                elif _cval:
                    card_s = str(_cval)
                    try:
                        if card_s.startswith("agent/"):
                            from call.lib.discovery import discover_agent_repo as _dar
                            _pp = _Path(_dar()) / card_s.split("agent/", 1)[1]
                        elif card_s.startswith("prompt/"):
                            from call.lib.discovery import discover_prompt_repo as _dpr
                            _pp = _Path(_dpr()) / card_s.split("prompt/", 1)[1]
                    except Exception:
                        _pp = None
                if _pp is not None:
                    pr_path = _pp if _pp.exists() else _pp
    except Exception as e:
        return None, _error_payload(agent=(name or ""), input=(input or ""), exc=e, status=500, code="INTERNAL_ERROR", project=(proj or project))

    # Parse cards
    proj_attrs, proj_instr, proj_raw = _load_card(proj_yaml)
    ag_attrs, ag_instr, ag_raw = _load_card(path_p)
    pr_attrs, pr_instr, pr_raw = _load_card(_Path(pr_path) if pr_path else None)

    # Strict validation: cards must be Markdown; prompt MD must contain METADATA
    def _is_md(p: _Path | None) -> bool:
        try:
            return bool(p and str(p).lower().endswith((".md", ".markdown")))
        except Exception:
            return False
    # Agent card must be MD when present
    if path_p and not _is_md(path_p):
        return None, _error_payload(agent=(name or ""), input=(input or ""), exc="Agent card must be Markdown (.md) with METADATA", status=400, code="BAD_CARD_FORMAT", project=proj)
    # Prompt card must be MD when present
    if pr_path and not _is_md(_Path(pr_path)):
        return None, _error_payload(agent=(name or ""), input=(input or ""), exc="Prompt card must be Markdown (.md) with METADATA", status=400, code="BAD_CARD_FORMAT", project=proj)
    # Prompt MD must contain METADATA YAML
    if pr_path and _is_md(_Path(pr_path)) and not (isinstance(pr_attrs, dict) and pr_attrs):
        return None, _error_payload(agent=(name or ""), input=(input or ""), exc="Prompt MD missing or invalid METADATA YAML", status=400, code="BAD_CARD_FORMAT", project=proj)

    # Build instructions and attributes based on merge
    attributes: Dict[str, Any] = {}
    instr: str = ""
    if merge:
        for src in (proj_attrs, ag_attrs, pr_attrs):
            if isinstance(src, dict):
                attributes.update({k: v for k, v in src.items() if k not in {"alias", "aliases"}})
        core = pr_instr or ag_instr or proj_instr or ""
        blocks = [core]
        if ag_raw:
            blocks.append("<agent>\n" + ag_raw.strip() + "\n</agent>")
        if proj_raw:
            blocks.append("<project>\n" + proj_raw.strip() + "\n</project>")
        instr = "\n\n".join([b for b in blocks if b.strip()])
    else:
        if pr_instr or pr_attrs or pr_raw:
            # Prefer prompt attributes/body; include agent raw block for richer preview
            attributes = pr_attrs if isinstance(pr_attrs, dict) else {}
            instr = pr_instr or ""
            try:
                if ag_raw and str(ag_raw).strip():
                    parts: list[str] = []
                    if instr.strip():
                        parts.append(instr.strip())
                    parts.append("<agent>\n" + ag_raw.strip() + "\n</agent>")
                    instr = "\n\n".join(parts)
            except Exception:
                # Fallback to prompt text only on any error
                instr = pr_instr or ""
        elif ag_instr or ag_attrs:
            attributes = ag_attrs if isinstance(ag_attrs, dict) else {}
            instr = ag_instr
        elif proj_instr or proj_attrs:
            attributes = proj_attrs if isinstance(proj_attrs, dict) else {}
            instr = proj_instr
        else:
            attributes = {}
            instr = ""

    # Non-empty preview guarantee when agent/prompt provided
    try:
        if (agent or prompt) and not (instr or "").strip():
            if pr_raw and str(pr_raw).strip():
                instr = pr_raw
            elif ag_raw and str(ag_raw).strip():
                instr = ag_raw
            elif proj_raw and str(proj_raw).strip():
                instr = proj_raw
    except Exception:
        pass

    # Determine runnable kind and primary absolute path
    selected_kind: str | None = None
    selected_abs: _Path | None = None
    if pr_path is not None:
        selected_kind = "prompt"
        selected_abs = _Path(pr_path)
    elif path_p is not None:
        selected_kind = "agent"
        selected_abs = _Path(path_p)
    elif proj_yaml is not None:
        selected_kind = "project"
        selected_abs = _Path(proj_yaml)

    # Align display name with the selected kind
    try:
        if selected_kind == "prompt" and isinstance(prompt, str) and prompt.strip():
            name = str(prompt)
    except Exception:
        pass

    # Compute rel_path and url; prefer DB row values when present
    rel_path_val: str | None = None
    url_val: str | None = None
    goal_val: str | None = None
    try:
        if selected_kind == "prompt":
            # Use DB row if we have it
            try:
                if 'rec_pr' in locals() and isinstance(rec_pr, dict):
                    rel_path_val = str(rec_pr.get("rel_path") or "") or None
                    url_val = str(rec_pr.get("url") or "") or None
                    goal_val = str(rec_pr.get("goal") or "") or None
            except Exception:
                pass
            # Fallback compute from filesystem base
            if (not rel_path_val) or (not url_val):
                from call.lib.discovery import discover_prompt_repo as _dpr
                prepo = _dpr()
                repo_name = "prompt"
                try:
                    rel_inside = selected_abs.relative_to(_Path(prepo)).as_posix()
                except Exception:
                    rel_inside = selected_abs.name if selected_abs else ""
                rel_path_val = rel_path_val or (f"{repo_name}/{rel_inside}" if rel_inside else None)
                import os as _os
                org = (_os.environ.get("GITHUB_REMOTE_ORGANIZATION_URL", "") or "").rstrip("/")
                branch = _os.environ.get("GITHUB_BRANCH", "main") or "main"
                url_val = url_val or (f"{org}/{repo_name}/blob/{branch}/{rel_inside}" if (org and rel_inside) else None)
        elif selected_kind == "agent":
            # DB row
            try:
                recs_ag = call_repo.find_agents(project=proj, agent=name)
                rec_ag = recs_ag[0] if recs_ag else None
                if rec_ag:
                    rel_path_val = str(rec_ag.get("rel_path") or "") or None
                    url_val = str(rec_ag.get("url") or "") or None
                    goal_val = str(rec_ag.get("goal") or "") or None
            except Exception:
                pass
            if (not rel_path_val) or (not url_val):
                from call.lib.discovery import discover_agent_repo as _dar
                arepo = _dar()
                repo_name = "agent"
                try:
                    rel_inside = selected_abs.relative_to(_Path(arepo)).as_posix()
                except Exception:
                    rel_inside = selected_abs.name if selected_abs else ""
                rel_path_val = rel_path_val or (f"{repo_name}/{rel_inside}" if rel_inside else None)
                import os as _os
                org = (_os.environ.get("GITHUB_REMOTE_ORGANIZATION_URL", "") or "").rstrip("/")
                branch = _os.environ.get("GITHUB_BRANCH", "main") or "main"
                url_val = url_val or (f"{org}/{repo_name}/blob/{branch}/{rel_inside}" if (org and rel_inside) else None)
        elif selected_kind == "project":
            try:
                if isinstance(rec_proj, dict):
                    rel_path_val = str(rec_proj.get("rel_path") or "") or None
                    url_val = str(rec_proj.get("url") or "") or None
                    goal_val = str(rec_proj.get("goal") or "") or None
            except Exception:
                pass
            if (not rel_path_val) or (not url_val):
                # Try both repos
                import os as _os
                org = (_os.environ.get("GITHUB_REMOTE_ORGANIZATION_URL", "") or "").rstrip("/")
                branch = _os.environ.get("GITHUB_BRANCH", "main") or "main"
                rel_inside = ""
                repo_name = ""
                try:
                    from call.lib.discovery import discover_agent_repo as _dar
                    ar = _dar()
                    rel_inside = selected_abs.relative_to(_Path(ar)).as_posix()
                    repo_name = "agent"
                except Exception:
                    try:
                        from call.lib.discovery import discover_prompt_repo as _dpr
                        pr = _dpr()
                        rel_inside = selected_abs.relative_to(_Path(pr)).as_posix()
                        repo_name = "prompt"
                    except Exception:
                        rel_inside = selected_abs.name if selected_abs else ""
                        repo_name = ""
                rel_path_val = rel_path_val or ((f"{repo_name}/{rel_inside}") if (repo_name and rel_inside) else None)
                url_val = url_val or ((f"{org}/{repo_name}/blob/{branch}/{rel_inside}") if (org and repo_name and rel_inside) else None)
    except Exception:
        rel_path_val, url_val = (rel_path_val or None), (url_val or None)

    # Goal from metadata preference order: prompt > agent > project
    try:
        def _goal_from(meta: Dict[str, Any] | None) -> str:
            if isinstance(meta, dict):
                g = meta.get("goal") or meta.get("purpose") or meta.get("title")
                return str(g) if g is not None else ""
            return ""
        goal_val = (goal_val or "") or _goal_from(pr_attrs if isinstance(pr_attrs, dict) else None) or _goal_from(ag_attrs if isinstance(ag_attrs, dict) else None) or _goal_from(proj_attrs if isinstance(proj_attrs, dict) else None) or None
    except Exception:
        pass

    cfg = RunnableConfig(
        name=name,
        project=proj,
        prompt_override=(prompt or None),
        merge=bool(merge),
        agent_yaml_path=(str(selected_abs) if selected_abs else None),
        path=(str(rel_path_val) if rel_path_val else None),
        url=(str(url_val) if url_val else None),
        type=(selected_kind or None),
        goal=(str(goal_val) if goal_val else None),
        base_dir=(str(selected_abs.parent) if selected_abs and getattr(selected_abs, 'parent', None) else None),
        instructions=str(instr or ""),
        model=(str(ag_attrs.get("model")) if isinstance(ag_attrs, dict) and ag_attrs.get("model") else None),
        attributes=attributes if isinstance(attributes, dict) else {},
        target=(target or None),
        input=(input or None),
    )

    # Default model if still absent
    if not cfg.model:
        try:
            cfg.model = str(_os.environ.get("LLM_MODEL", "gpt-5"))
        except Exception:
            cfg.model = "gpt-5"

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
    details: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a consistent error payload for API/CLI/Bot.

    Shape:
    { ok: false, error_code: <int>, description: <str>, code?: <str>, options?: [..], agent, final_output: null, echo }
    """
    try:
        msg = str(exc)
    except Exception:
        msg = ""
    # Heuristic mapping for Not Found
    if status is None and isinstance(exc, (KeyError, FileNotFoundError, ValueError)) and "not found" in msg.lower():
        status = 404
    payload: Dict[str, Any] = {
        "ok": False,
        "error_code": int(status or 400),
        "description": msg,
        "agent": agent,
        "project": (project or ""),
        "final_output": None,
        "echo": bool(echo),
    }
    if session_id:
        try:
            payload["session_id"] = session_id
        except Exception:
            pass
    if code:
        payload["code"] = code
    if options is not None:
        payload["options"] = options
    if details is not None:
        try:
            payload["details"] = details
        except Exception:
            pass

    # Optional debug details (file/line/stack) for CLI usage
    try:
        debug_enabled = bool(debug) or str(os.environ.get("CALL_DEBUG", "")).lower() in ("1", "true", "yes", "on")
    except Exception:
        debug_enabled = bool(debug)
    if debug_enabled:
        try:
            import traceback
            tb = exc.__traceback__
            frames = traceback.extract_tb(tb) if tb is not None else []
            stack_items: List[Dict[str, Any]] = []
            for fr in frames[-20:]:
                stack_items.append({
                    "file": fr.filename,
                    "line": fr.lineno,
                    "function": fr.name,
                    "code": fr.line,
                })
            top_file = stack_items[-1]["file"] if stack_items else None
            top_line = stack_items[-1]["line"] if stack_items else None
            payload["debug"] = {
                "file": top_file,
                "line": top_line,
                "stack": stack_items,
            }
        except Exception:
            # best-effort only
            pass

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
    project: Optional[str],
    agent: Optional[str],
    prompt: Optional[str] = None,
    target: Optional[str] = None,
    input: Optional[str] = None,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    session_id: Optional[str] = None,
    echo: bool = False,
    debug: bool = False,
    merge: bool = False,
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

    # TEST HOOK (early): simulate Tracing 403 for CLI integration tests
    try:
        _fake = str(os.environ.get("CALL_FAKE_TRACING_403", "")).strip().lower()
        if _fake in ("1", "true", "yes", "on"):
            import json as _json
            _details = {
                "error": {
                    "code": "unsupported_country_region_territory",
                    "message": "Country, region, or territory not supported",
                    "param": None,
                    "type": "request_forbidden",
                }
            }
            return _error_payload(
                agent=(agent or ""),
                input=(input or ""),
                exc=RuntimeError("Tracing client error 403: " + _json.dumps(_details, ensure_ascii=False)),
                status=403,
                echo=echo,
                debug=debug,
                code="REQUEST_FORBIDDEN",
                project=project,
                details=_details,
                session_id=(session_id or None),
            )
    except Exception:
        pass

    # Build ready-to-run config (handles target, wildcard prompt, selection, and blank agent)
    try:
        cfg, cfg_err = build_runnable_instructions_config(project=project, agent=agent, prompt=prompt, target=target, input=input, merge=merge)
    except Exception:
        cfg, cfg_err = None, None
    if isinstance(cfg_err, dict):
        # Preserve original error envelope (status/code) from resolve_agent
        try:
            if session_id:
                cfg_err["session_id"] = session_id
        except Exception:
            pass
        return cfg_err

    # cfg is ready; dump a normalized snapshot in DEBUG
    try:
        from dataclasses import asdict as _asdict
        from call.lib.logging import debug_print as _dbg
        snap = _asdict(cfg) if cfg is not None else {}
        if cfg is not None:
            snap["instructions_len"] = len(cfg.instructions or "")
            snap.pop("instructions", None)
        _dbg("[api]", "[CFG]", __import__('json').dumps(snap, ensure_ascii=False))
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
    chosen_name = cfg.name if cfg else ""
    chosen_project = (cfg.project if cfg else None) or (project or "")
    # Back-compat: absolute path (if any) for agent echo
    try:
        yaml_path = getattr(cfg, "agent_yaml_path", None)
    except Exception:
        yaml_path = None
    # Resolved descriptor for response/echo
    resolved = {
        "project": chosen_project,
        "name": chosen_name,
        # New semantics: path is repo-relative (e.g., 'agent/Proj/Agent/agent.md' or 'prompt/ready/...')
        "path": (getattr(cfg, "path", None) if cfg else None),
        # Optional helpful fields
        "url": (getattr(cfg, "url", None) if cfg else None),
        "type": (getattr(cfg, "type", None) if cfg else None),
        "goal": (getattr(cfg, "goal", None) if cfg else None),
        "aliases": [],
        "prompts": [],
    }

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
            # TEST HOOK: simulate a tracing 403 error when requested
            try:
                if str(os.environ.get("CALL_FAKE_TRACING_403", "")).strip().lower() in ("1", "true", "yes", "on"):
                    raise RuntimeError('Tracing client error 403: {"error":{"code":"unsupported_country_region_territory","message":"Country, region, or territory not supported","param":null,"type":"request_forbidden"}}')
            except Exception:
                pass

            # Use the app layer context manager to build and run the agent once with a ready config.
            # Prefer new signature (cfg, user_input=...), but fall back to legacy signature
            # when a monkeypatched test function expects (name, samples_dir, ...).
            cm = app_call.build_and_run_agent
            _cfg_obj = (cfg if cfg is not None else RunnableConfig(name=(chosen_name or ""), project=(project or None), instructions="", input=(input or None), target=(target or None)))
            try:
                async with cm(cfg=_cfg_obj, user_input=((_cfg_obj.input or input) or "")) as (agent_obj, _cfg, _session):
                    final_output = getattr(_cfg, "_last_final_output", None)
                    try:
                        actual_sid = getattr(_session, "id", None)
                    except Exception:
                        actual_sid = None
            except TypeError:
                # Legacy compatibility: (name, samples_dir, user_input, prompt_override, project_name, merge)
                async with cm(
                    (_cfg_obj.name if isinstance(_cfg_obj.name, str) else (chosen_name or "")),
                    None,
                    user_input=(input or ""),
                    prompt_override=(_cfg_obj.prompt_override or (prompt or None)),
                    project_name=(_cfg_obj.project or (project or None)),
                    merge=bool(_cfg_obj.merge),
                ) as (agent_obj, _cfg, _session):
                    final_output = getattr(_cfg, "_last_final_output", None)
                    try:
                        actual_sid = getattr(_session, "id", None)
                    except Exception:
                        actual_sid = None
                final_output = getattr(_cfg, "_last_final_output", None)
                # Try to read actual session id from session object
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
                # Try to parse trailing JSON from message
                try:
                    import json as _json
                    brace = msg.find("{")
                    if brace != -1:
                        details = _json.loads(msg[brace:])
                except Exception:
                    details = None
            return _error_payload(agent=(chosen_name or ""), input=(input or ""), exc=e, status=status, echo=echo, debug=debug, code=err_code, project=chosen_project, details=details, session_id=(session_id or None))
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
            agent=(chosen_name or ""),
            input=(input or ""),
            exc=desc or msg,
            status=status,
            echo=echo,
            debug=debug,
            code=err_code,
            project=chosen_project,
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
        "agent": (chosen_name if isinstance(chosen_name, str) else ""),
        "agent_path": (str(yaml_path) if yaml_path else None),
        "final_output": final_output,
        # echo flag included for callers that want to inspect behavior upstream
        "echo": bool(echo),
        "resolved": resolved,
        **({"session_id": session_id_out} if session_id_out else {}),
    }


def call(
    *,
    project: Optional[str],
    agent: Optional[str],
    prompt: Optional[str] = None,
    target: Optional[str] = None,
    input: Optional[str] = None,
    chat_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    session_id: Optional[str] = None,
    echo: bool = False,
    debug: bool = False,
    merge: bool = False,
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
                chat_id=chat_id,
                thread_id=thread_id,
                session_id=session_id,
                echo=echo,
                debug=debug,
                merge=merge,
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
        if isinstance(agent, str) and agent.strip():
            rows = call_repo.find_agents(project=(project or None), agent=agent)
            if not rows:
                return _error_payload(agent=agent, input="", exc="not found", status=404, code="NO_DATA_FOUND", project=project, options=[])
            if len(rows) > 1:
                return _error_payload(agent=agent, input="", exc="Multiple agents matched your criteria", status=400, code="TOO_MANY_ROWS", project=project, options=rows[:20])
            r = rows[0]
            return {"ok": True, "resolved": {"project": r.get("project"), "name": r.get("agent"), "path": r.get("path"), "aliases": [], "prompts": []}}

        # 2) Resolve by prompt
        if isinstance(prompt, str) and prompt.strip():
            recs = call_repo.list_prompts(project=(project or None), agent=(agent or None), prompt=prompt)
            if not recs:
                return _error_payload(agent=(agent or ""), input="", exc="not found", status=404, code="NO_DATA_FOUND", project=project, options=[])
            if len(recs) > 1:
                return _error_payload(agent=(agent or ""), input="", exc="Multiple prompts matched your criteria", status=400, code="TOO_MANY_ROWS", project=project, options=recs[:20])
            pr = recs[0]
            pj = pr.get("project") or project
            ag = pr.get("agent") or agent
            # Agent row must exist
            arows = call_repo.find_agents(project=pj, agent=ag)
            if len(arows) != 1:
                return _error_payload(agent=str(ag or ""), input="", exc="not found", status=404, code="NO_DATA_FOUND", project=pj, options=(arows or []))
            ar = arows[0]
            return {"ok": True, "resolved": {"project": ar.get("project"), "name": ar.get("agent"), "path": ar.get("path"), "aliases": [], "prompts": []}}

        # 3) Only project provided -> ambiguous
        if isinstance(project, str) and project.strip():
            opts = call_repo.find_agents(project=project, agent=None)
            if len(opts) == 1:
                r = opts[0]
                return {"ok": True, "resolved": {"project": r.get("project"), "name": r.get("agent"), "path": r.get("path"), "aliases": [], "prompts": []}}
            return _error_payload(agent=(agent or ""), input="", exc=("not found" if not opts else "Multiple agents matched your criteria"), status=(404 if not opts else 400), code=("NO_DATA_FOUND" if not opts else "TOO_MANY_ROWS"), project=project, options=opts[:20] if opts else [])

        # Nothing to resolve
        return _error_payload(agent=(agent or ""), input="", exc="not found", status=404, code="NO_DATA_FOUND", project=project, options=[])
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


def interpret_exec_payload(payload: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Validate and normalize a single exec payload into kwargs for call().

    Rules:
    - Exactly one of project|agent|prompt|target must be present (truthy string).
    - Always use the full payload JSON as the input string.
    - session_id and echo are passed through when present.

    Returns (kwargs, err) where kwargs can be passed to call(**kwargs) and err is an error envelope on validation error.
    """
    try:
        # Determine exactly one among project|agent|prompt|target
        f_project = payload.get("project")
        f_agent = payload.get("agent")
        f_prompt = payload.get("prompt")
        f_target = payload.get("target")
        fields = [f for f in [f_project, f_agent, f_prompt, f_target] if (str(f or "").strip())]
        if len(fields) != 1:
            return {}, {
                "ok": False,
                "error_code": 400,
                "description": "Provide exactly one of 'project' or 'agent' or 'prompt' or 'target'",
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
            "echo": bool(payload.get("echo", True)),
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
        return kwargs, None
    except Exception as e:
        return {}, {
            "ok": False,
            "error_code": 400,
            "description": str(e),
            "code": "BAD_REQUEST",
        }

# Public alias for clarity in import sites (Actions/MCP)
api_interpret_exec_payload = interpret_exec_payload
