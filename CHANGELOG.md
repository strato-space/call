# Changelog

All notable changes to this project will be documented in this file.


## 2025-10-17

- **API:** `build_runnable_instructions_config()` now supports pure GPT calls without instructions. When all selectors (`project`, `agent`, `prompt`, `target`) are `None`, returns a minimal config with only the model from `LLM_MODEL` env (default: `gpt-5`) and empty instructions, allowing direct input-only calls. (`lib/api.py`)
- **API:** Instructions now come exclusively from prompt body, never from user input. Removed fallback `instructions_text = prompt_body if prompt_body.strip() else str(input or "")` to enforce separation of concerns. (`lib/api.py`)
- **API:** Moved environment model resolution to function top and renamed `env_model` to `default_env_model` for clarity. Removed try/except wrapper as it's unnecessary. (`lib/api.py`)
- **API:** `project` and `agent` parameters in `build_runnable_instructions_config()` now default to `None` for consistency with optional selectors. (`lib/api.py`)
- **API:** Fixed `None` agent name error in pure GPT mode. `RunnableConfig` now uses `agent="void"` and `id="void"` for input-only calls to prevent downstream crashes. (`lib/api.py`)
- **API:** Added `RunnableConfig.minimal(model, input)` static factory method to simplify creation of minimal configs for pure GPT calls. (`lib/api.py`)
- **API:** Cleaned up `RunnableConfig` field defaults: `prompt_text`, `instructions`, `card_text`, and `base_dir` now default to empty string `""` instead of verbose inline defaults. Moved inline comments to line above for better readability. (`lib/api.py`)
- **MCP Hook:** Simplified debug print formatting for MCP tool results. Multiline strings now render with YAML literal block scalars (`|`) without escape sequence processing, preserving the exact payload structure. (`app/call.py`)
- **CLI:** Added pure GPT usage example: `python -m call.cli.main call --input "text"` runs without any prompt/agent instructions. (`README.md`)
- **Tests:** Added 6 new tests covering pure GPT path, model override, env fallback, instructions-never-use-input guarantee, and `RunnableConfig.minimal()` factory. All 153 tests pass. (`app/tests/test_builder_config.py`)
- **Docs:** Updated CLI usage section with pure GPT call example and clarified that `LLM_MODEL` env var controls the default model. (`README.md`)

## 2025-10-16

- **Telegram Bot:** Agent/prompt name normalization now strips trailing punctuation (`,`, `.`, `;`, `:`, `!`, `?`, etc.) so `@220-PM-Status!` resolves to `220-PM-Status`. (`telegram_bot/bot.py`)
- **Telegram Bot:** `/call` parser preserves newlines in multiline input by using regex-based flag removal and slicing without `.split()`. (`telegram_bot/bot.py`)
- **Telegram Bot:** Echo flag detection simplified to direct string comparison instead of nested closures. (`telegram_bot/bot.py`)
- **MCP Hook:** All MCP server tool arguments and results are now sent to Telegram in silent mode (`disable_notification=True`) and wrapped in expandable blockquotes (`<blockquote expandable>`) to reduce visual clutter. (`app/call.py`)
- **MCP Hook:** Service messages are tracked and deleted automatically after the final agent result is delivered via `cleanup_service_messages()`. (`app/call.py`)
- **MCP Hook:** Preview payloads (welcome banners) now use YAML formatting with literal block scalars (`|`) for multiline strings instead of JSON for improved readability. (`app/call.py`)
- **YAML Dumper:** Fixed `_literal_yaml_str_representer` to always use literal block scalar style (`|`) for strings containing newlines, ensuring proper formatting in Telegram messages. (`app/call.py`)
- **Tests:** Added `test_normalize_token_strips_trailing_punctuation()`, `test_handle_call_with_trailing_punctuation_in_agent_name()`, and `test_handle_call_preserves_newlines_in_input()` to cover new parsing behavior. (`app/tests/test_telegram_bot_handlers.py`)
- **Docs:** Updated README with "Input normalization" and "MCP Hook messages" sections documenting trailing punctuation stripping, newline preservation, silent MCP messages, expandable blockquotes, and automatic service message cleanup. (`README.md`)

## 2025-10-11

- **CLI:** call exec now reloads repositories when CALL_DEBUG is set before payload construction so local prompt/agent edits are picked up without restarting. (call/cli/main.py, call/README.md)
- **MCP:** Tool invocation hooks log formatted results under debug to aid inspection while truncating large payloads. (call/app/call.py)
- **Config:** Added Telegram MCP presets and normalized paths in the Claude Desktop config template. (call/claude_desktop_config.json)

## 2025-09-29

- **API/CLI/Actions/MCP:** Added `call.lib.api.read(card_id)` and `call.lib.api.write(card_id, card_text)` helpers that keep repo.db and the filesystem in sync (DB first). Exposed them through new CLI commands (`call read`, `call write`), Actions endpoints (plain-text `/read/{id}` + `/write/{id}`), and MCP tools (`read`, `write`) so card edits take effect immediately without running `reload`. (`call/lib/api.py`, `call/cli/main.py`, `call/actions/main.py`, `call/actions/openapi.json`, `call/mcp/server.py`)
- **Tests:** Added regression coverage for the new helpers across the library, CLI commands, and Actions API to verify DB/FS writes. (`call/lib/tests/test_cards_read_write.py`, `call/cli/tests/test_cards_commands.py`, `call/actions/tests/test_cards_endpoints.py`)
- **Docs:** Documented the direct card access helpers and CLI examples. (`call/README.md`, `call/docs/cards.md`)

## 2025-09-28

- **Docs:** Added `AGENTS.md` with environment, repo layout, and workflow guidance for Codex-based automation.
- **README:** Documented the `tools/repos.sh --codex` preset that provisions `/workspace/.venv` and ensures sibling repositories are available in `/workspace/`.
- **Docs:** Expanded the contributor orientation to cover the Actions REST API, MCP server, Telegram bot, Windsurf settings, credentials wallet, pinned requirements, and MCP config presets.
- **Docs:** Documented preferred engineering principles (KISS, SOLID with Dependency Injection, focused helpers, explicit failures) in `AGENTS.md` and summarized them in the README.
- **Docs:** Clarified that every exception and I/O error must be logged even when execution continues, reinforcing observability expectations.

## 2025-09-27

- **MCP config loader:** Renamed `_load_yaml` to `_load_mcp_yaml_config()` in `call/app/call.py` to scope its usage to MCP configuration parsing and updated callers/tests accordingly.
- **Digest buttons:** `send_digest_notification()` now accepts precomputed `buttons` metadata and renders inline buttons without reading agent files during runtime. (`call/app/call.py`)
- **Actions API:** Removed the legacy `call_lib`, `list_lib`, and `interpret_exec_payload` aliases from `call/actions/main.py`; tests now patch the exported `api_call`/`api_list` symbols directly.
- **CLI/runtime cleanup:** Removed the deprecated `scan` CLI alias (use `reload`) and the `clean_html_for_telegram()` wrapper; runtime now calls `sanitize_telegram_html()` directly. Updated CLI documentation and tests accordingly. (`call/cli/main.py`, `call/app/call.py`, `call/app/tests/test_cli_prompts_and_exec.py`, docs)
- **Tooling:** Dropped the `ensure_repo()` alias from `call/tools/repos.sh`; scripts should call `repo` directly.
- **Logging:** Disabled per-server skip spam by consolidating disabled MCP server names into a single debug line in `_build_mcp_servers_from_yaml()`. (`call/app/call.py`)
- **Tests:** Refreshed `test_send_digest_notification.py` to pass button metadata directly and added coverage for multiple buttons; updated `test_mcp_config_yaml.py` to reflect the renamed MCP loader.
- **Builder simplification:** Removed the legacy `merge` flag from `build_runnable_instructions_config()` and all runtime surfaces (CLI, Telegram bot, tests). Instructions now always inherit prompt → agent → project metadata without separate merge modes. (`call/lib/api.py`, `call/cli/main.py`, `call/telegram_bot/bot.py`, tests, docs)

## 2025-09-26

- **Parser:** `parse_metadata_and_prompt()` now loads pure YAML cards and Markdown files with only a `METADATA` block, falling back to the remaining body as the prompt text. Malformed YAML raises a `BAD_CARD_FORMAT` envelope and `_load_card()` logs the failure through the `call.api` logger so callers receive structured errors instead of stack traces. (`call/lib/utils.py`, `call/lib/api.py`)
- **Prompt IDs:** `build_runnable_instructions_config()` preserves prompt IDs that come from `target` selectors (e.g., `50-DiscoveryAgent`) and keeps project-level selections from rewriting the identifier. (`call/lib/api.py`, tests)
- **CLI echo defaults:** `/call` echo responses now default to `false`; project-only `--resolved` snapshots report `"agent": null` for clarity. (`call/cli/main.py`)
- **Logging:** Added debug logging when metadata parsing fails so API callers can correlate BAD_CARD_FORMAT responses with server-side errors. (`call/lib/api.py`)
- **Tests:** Added `call/lib/tests/test_parse_metadata.py` to cover Markdown, YAML-only, and malformed card cases. Updated builder/CLI tests to assert prompt ID and echo behavior. (`call/lib/tests/test_parse_metadata.py`, `call/app/tests/test_builder_config.py`, `call/app/tests/test_cli_prompts_and_exec.py`, `call/app/tests/test_target_resolution_via_target.py`)

## 2025-09-19

- Refactor (API): Introduced `RunnableConfig` DTO and `build_runnable_instructions_config(project, agent, prompt, merge=False)` that returns a ready-to-run config with string `instructions`, `model`, `attributes`, `agent_yaml_path`, and `base_dir`. Defaults are dot-safe so callers can use direct attribute access (e.g., `cfg.name`).
- Change (Builder): When `merge=False` and a prompt is selected, CLI print-instructions previews now include the prompt body plus an `<agent>...</agent>` block. With `merge=True`, the preview includes `<agent>` and `<project>` blocks.
- Change (CLI): `--print-instructions` honors the `--merge` flag (default off). After building the config, the CLI validates once and prints a normalized snapshot to debug logs (gated by `CALL_DEBUG`).
- Feat (API): Strict parsing of prompt METADATA YAML; malformed blocks return a `{ ok:false, error_code:400, code:"BAD_REQUEST" }` envelope.
- Feat (API): Blank-agent path — when no `project|agent|prompt|target` is provided, the pipeline runs with empty instructions and passes the input through. This enables pure text uses in Telegram when no `@Target` is specified.
- Feat (Bot): Bot mention handling — the bot strips its own `@BotName` at the start. It only treats a target when the first token after stripping starts with `@`. Otherwise, no target is passed and only the input is used.
- Feat (Bot): Reply payload — when a `/call` command or a plain message is a reply, the bot constructs a JSON payload: `{ target?, input?, context?, replay? }`. `context` includes items like `{type:"text", text:"..."}` and `{type:"text", url:"https://api.telegram.org/file/bot<token>/..."}` for replied documents. If `input` is empty, it falls back to the reply text. `replay` is a convenience field (string or array) mirroring the reply content.
- Change (Errors): Standardized "not found" wording in error envelopes where applicable (e.g., `NO_DATA_FOUND`).
- Removal (App): Deprecated `call.app.call.build_agent_config` removed. Tests were updated to use the new DTO builder from the library.
- Tests: Full suite green — 66 passed.

 - Feat (API): Prompt-only fallback & global prompt discovery. When an agent cannot be resolved but a prompt id/name is provided (or in `target`), the API resolves the prompt path directly (first in the project, then globally across all projects), loads METADATA (project/agent/id), and builds a runnable config. This allows `/call @PromptId` to work immediately after adding a file to `prompt/draft` or `prompt/ready`.
 - Feat (API): Suggestions in `NO_DATA_FOUND` for prompts. When wildcard or exact prompt lookup returns no results, the error envelope includes a best-effort `options` list of suggestions (substring match in-scope, then globally).
 - Feat (Logging): When a prompt is resolved globally (outside the current project scope), a warning is emitted via the standard logger `call.api` (level WARNING) and a debug line is printed via `debug_print` (gated by `CALL_DEBUG`).
 - Change (App): `build_and_run_agent()` now uses `agents.run.DEFAULT_MAX_TURNS` for `Runner.run(..., max_turns=...)`. At import, Call sets `DEFAULT_MAX_TURNS` from the `AGENTS_DEFAULT_MAX_TURNS` env (default 150). Set `AGENTS_DEFAULT_MAX_TURNS=300` in `.env` for longer runs.

## 2025-09-17

- Change: enforce strict case-sensitive agent and prompt names across discovery, API, CLI, and Telegram bot (KISS). Removed `to_pascal_case` normalization and any case-insensitive fallbacks. Files updated: `call/lib/discovery.py`, `call/app/call.py`, `call/telegram_bot/bot.py`.
- Feat (CLI): added `prompts` subcommand to list prompts in flat form (table or JSON) with fields `prompt_id`, `name`, `agent`, `project`, `state`, `url`, `path`. File: `call/cli/main.py`.
- Feat (CLI): added `exec` subcommand to execute with structured context items via repeated `--content-item` flags (text, URL, or JSON). Extracts Google Docs file id when present. Supports `--output-type` and `--print-instructions`. File: `call/cli/main.py`.
- Feat (CLI): `--print-instructions` for `call` and `exec` to print merged instructions and exit.
- UX (CLI): safe UTF‑8 printing on Windows consoles (CP‑1251) via `_safe_print()`; all table/JSON outputs routed through it. Returns exit code `0` on success and `1` when library returns `{ ok:false }` envelopes. File: `call/cli/main.py`.
- Feat (API): robust pipeline invocation — introspects `run_digest_pipeline` and only passes `merge` kwarg when supported (compatibility with monkeypatched tests). File: `call/lib/api.py`.
- Feat (API): map Tracing client errors to structured envelopes — messages containing `request_forbidden`/`unsupported_country_region_territory` are returned with `error_code: 403`, `code: "REQUEST_FORBIDDEN"`, and a parsed `details` object when available. Tests now trigger this path by forcing the runtime to raise inside `build_and_run_agent`. File: `call/lib/api.py`.
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
