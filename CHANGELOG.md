# Changelog

All notable changes to this project will be documented in this file.

## 2025-09-17
- Change: enforce strict case-sensitive agent and prompt names across discovery, API, CLI, and Telegram bot (KISS). Removed `to_pascal_case` normalization and any case-insensitive fallbacks. Files updated: `call/lib/discovery.py`, `call/app/call.py`, `call/telegram_bot/bot.py`.
- Feat (CLI): added `prompts` subcommand to list prompts in flat form (table or JSON) with fields `prompt_id`, `name`, `agent`, `project`, `state`, `url`, `path`. File: `call/cli/main.py`.
- Feat (CLI): added `exec` subcommand to execute with structured context items via repeated `--content-item` flags (text, URL, or JSON). Extracts Google Docs file id when present. Supports `--output-type` and `--print-instructions`. File: `call/cli/main.py`.
- Feat (CLI): `--print-instructions` for `call` and `exec` to print merged instructions and exit.
- UX (CLI): safe UTF‑8 printing on Windows consoles (CP‑1251) via `_safe_print()`; all table/JSON outputs routed through it. Returns exit code `0` on success and `1` when library returns `{ ok:false }` envelopes. File: `call/cli/main.py`.
- Feat (API): robust pipeline invocation — introspects `run_digest_pipeline` and only passes `merge` kwarg when supported (compatibility with monkeypatched tests). File: `call/lib/api.py`.
- Feat (API): map Tracing client errors to structured envelopes — messages containing `request_forbidden`/`unsupported_country_region_territory` are returned with `error_code: 403`, `code: "REQUEST_FORBIDDEN"`, and a parsed `details` object when available. Added an early test hook `CALL_FAKE_TRACING_403=1` to simulate this path. File: `call/lib/api.py`.
- Feat (API): converted plain-text pipeline failures (`final_output` starting with `"Error:"`) into structured error envelopes (`error_code: 502`, `code: UPSTREAM_CONNECT_ERROR|PIPELINE_ERROR`) to avoid leaking stack traces to users. File: `call/lib/api.py`.
- UX (App): reduced noisy console output during normal CLI runs by gating prints behind `CALL_DEBUG`. Welcome banner failures, Telegram send errors, and run-time exceptions are now printed only in debug mode. File: `call/app/call.py`.
- Chore (Logging): centralized `debug_print` in `call.lib.logging`, removed local implementations from `call/app/call.py` and `call/lib/discovery.py`. Removed `CALL_SILENT` flag; logging is controlled solely by `CALL_DEBUG` (KISS).
- Chore (Logging): configure stdlib logging once per entrypoint — CLI (`call/cli/main.py`) and Telegram bot (`call/telegram_bot/bot.py`) call `configure_logging()` at startup. Library code assumes logging is preconfigured.
- Feat (Logging): support `CALL_LOG_JSON=1` to emit JSON logs via stdlib logger. Also added a README note and example usage.
- Feat (CLI): add `--json-logs` flag to force JSON logs regardless of env.
- Refactor (discovery): indices and scans now use exact names and also enrich alias mappings from each agent’s local `agent.yaml`. File: `call/lib/discovery.py`.
- Tests: added CLI integration tests for `prompts` table/JSON, `call/exec --print-instructions`, JSON shape for `list` (aliases/prompts), and error propagation for Tracing 403 via the new test hook. All tests pass under `.venv` — 40 passed.
- Docs: updated `call/README.md` to reflect KISS case-sensitive policy, new CLI subcommands, `--print-instructions`, Windows-safe printing, error envelopes and exit codes; added `prompt/README.md`; inserted a KISS quickstart section at the top of `agent/README.md`. Removed references to `CALL_SILENT`; use `CALL_DEBUG` for diagnostics.

## 2025-09-15
- Fix: eliminate `NameError` by removing last usage of legacy `_discover_agent_yaml_compat`; app now calls the unified `discover_agent_yaml(agent, project)` wrapper that delegates to `call.lib.discovery.discover_agent_yaml`. (file: `call/app/call.py`)
- Refactor: complete discovery consolidation in app layer — removed duplicate internal discovery and legacy `_ensure_indices` path from `call/app/call.py`; kept a single thin wrapper to the library. (files: `call/app/call.py`)
- Fix/UX: Telegram welcome banner spacing — ensured a blank line between user input preview and attributes (`mcp`, `vs`, `model`) and a blank line after the header. (file: `call/app/call.py`)
- Chore: ignore compiled artifacts — added explicit `app/__pycache__/` to `.gitignore` (already ignoring global `__pycache__/` and `*.py[cod]`), and removed tracked `*.pyc` files from index. (file: `call/.gitignore`)
- Docs: updated `README.md` to document discovery wrapper delegation and the Telegram banner spacing behavior.
- Tests: full suite green — 22 passed.
- Refactor: centralized projects index loader — added `call.lib.discovery.load_projects_index()` (strict schema), refactored `call.lib.api.list()` to call it directly, removed local wrappers. (files: `call/lib/discovery.py`, `call/lib/api.py`, tests)

## 2025-09-14
- Feat: Introduced `call/repos.sh` script for managing local clones of primary repositories.
- Feat: Added `repo(url, [dir])` helper that clones when missing and performs `git -C dir pull --ff-only` when the repository exists.
- Change: `ensure_repo` renamed to `repo`; kept a backward-compatible alias `ensure_repo()` that delegates to `repo`.
- Docs: Updated `README.md` with a new section “Repo sync helper (repos.sh)” including usage, behavior, examples for Git Bash/WSL and PowerShell.
- Docs: Enhanced CLI documentation in README.md with:
  - Detailed `--project-name` behavior (Bot suffix stripping)
  - `--trace` debugging features
  - Multi-line command examples
- Feat: Projects-aware discovery across `prompt/projects.yaml` (e.g., `UxFab/`).
  - `_ensure_indices()` now generates `agents.yaml` for all projects + legacy `AgentFab/` and `agents/`.
  - `discover_agent_yaml()` searches per-project indices and falls back to scanning all project folders.
  - Migrated agents under `prompt/UxFab/` (e.g., `AiNewsAggr`, `Stratoslav`) are now properly resolved by name and alias.
  - Support both flattened `project.yaml` (top-level `name`/`agents`) and legacy nested `project:` block.
  - Recognize new project `FanFab` in `prompt/projects.yaml`.
- Fix: Avoid builtins shadowing — use `_builtins.list` in `isinstance` checks inside `call/lib/api.py` where a module-level `list()` function exists.
- Change: Telegram bot `/list` handler no longer falls back to listing all projects when scoped list is empty; it now returns a concise "No agents found".
- UX: Telegram bot now renders project headers as clickable bot handles (e.g., `@AgentFabBot`) in `/list`, and `/projects` prints clickable `t.me/<ProjectName>Bot` links.
- Breaking/API: keyword-only public API
  - `call(*, project, agent, prompt=None, input=None, ...)` (sync wrapper over `call_async`)
  - `list(*, project=None, agent=None, prompt=None)` returns hierarchical projects→agents with aliases/prompts
  - New `resolve_agent()` helper and structured error envelopes with `code` and `options`
- Feat: Prompt override is wired through the pipeline
  - `run_digest_pipeline(..., prompt_override=, project_name=)`
  - `build_agent_config(..., prompt_override=)` selects the named prompt if provided
- CLI: switched to `--project/--agent/--prompt/--input`; prints hierarchical JSON for `list`
  - Bot: derives project from bot name; `@StratoSpaceAiBot` lists all projects and adds `/projects`
- Tests: added selection and prompt-override tests; updated discovery tests for projects-aware indices

## 2025-09-13
- Feat: project_name-only token routing. Added `get_project_token(project_name)` and made `init_bot(project_name=...)` mandatory. Removed fallbacks and any env mutation of `TELEGRAM_TOKEN`.
- Change: case-sensitive agent names (KISS). Removed `to_pascal_case` normalization from bot/CLI/API; names are used exactly as provided.
- Change: Telegram bot no longer validates agent existence; validation/discovery happens centrally in `call.lib.api.call_async()` which returns structured errors for unknown agents.
- Feat: project-scoped listing. `list(..., project_name=...)` scopes to a single directory under the prompt repo. No default cross-project merge; removed `grouped` output from library and CLI. CLI `list` now supports `--project-name`.
- Docs: updated README to reflect project_name-only tokens, KISS naming, bot validation policy, and project-scoped listing; replaced `prompt/agents` → `prompt/UxFab` in discovery/order examples.
- Config: updated `.env` guidance to use `TELEGRAM_TOKEN.<ProjectName>` keys (e.g., `TELEGRAM_TOKEN.StratoSpaceAi`, `TELEGRAM_TOKEN.AgentFab`).

## 2025-09-12
- Fix: Telegram chat routing — preserve caller-provided `chat_id`/`thread_id` passed via `call.lib.api.call(...)`. The pipeline no longer overwrites targets with Agent YAML or `.env` defaults; explicit values are propagated through final notifications to avoid global-state races. (files: `call/app/call.py`, `call/lib/api.py`)
- Refactor: Centralize Telegram HTML prep per Bot API — introduce `sanitize_telegram_html()`, `truncate_telegram_html_safe()`, and `prepare_telegram_html()` in `call/app/utils/html_sanitizer.py`. Route `telegram_prepare_html()` and HTML truncation through the centralized implementation. (files: `call/app/utils/html_sanitizer.py`, `call/app/utils/telegram_text.py`)
- Change: Use only the documented HTML tags/attrs for Telegram: `a`, `b/strong`, `i/em`, `u/ins`, `s/strike/del`, `code`, `pre`, `blockquote`, `tg-spoiler`, `tg-emoji`; preserve `a[href]`, `blockquote[expandable]`, `tg-emoji[emoji-id]`, and `code[class~=language-*]`. Normalize `h1–h6` to `<b>…</b>` + newline; replace `<hr>` with two newlines; flatten lists to text. (file: `call/app/utils/html_sanitizer.py`)
 - Change: `send_digest_notification()` signature simplified — removed unused `url` parameter. Internal publishing now uses a local `local_url` variable and macro `{{digest_url}}` in `buttons` resolves against it. (file: `call/app/call.py`)
 - Fix: Avoid sending empty Telegram messages — empty/whitespace-only `text` is normalized to `None` so the function falls back to the banner with input echo. (file: `call/app/call.py`)
 - Debug: Add `[DEBUG] send_digest_notification ...` prints of argument summary and resulting publish URL. (file: `call/app/call.py`)
 - Tests: Add `test_send_digest_notification.py` covering empty-text fallback, long-text publish link, and `{{digest_url}}` macro in buttons. (file: `call/app/tests/test_send_digest_notification.py`)

## 2025-09-07
- Change: Voice now uses the Call library directly (no subprocess). `voice/src/lib/core.py` calls `call.lib.api.call(...)` and forwards the `echo` flag. When `echo=True`, Voice returns the full dict; otherwise it returns plain text.
- Feature: Add `echo: bool` to `call.lib.api.call()` and `call_async()`; include `echo` in the success payload.
- Change: Centralize Telegram/HTML sanitization and text preparation via `call/app/utils/` (html_sanitizer, telegram_text, telegraph_utils). Snapshot `call/app/call.78e8440.py` updated to use these utilities.
- Fix: Snapshot script-mode fallback for utils/Telegraph imports to avoid relative-import errors when running `python call/app/call.78e8440.py` directly.

## 2025-09-02
- Fix: eliminate runpy warning by lazy-importing `call.app.call` inside entrypoints (`call/app/__init__.py`).
- Change: `post_run_git_push()` now checks for changes first (`git status --porcelain -uno`) and returns silently if none; no stdout logging from this helper (`call/app/call.py`).
