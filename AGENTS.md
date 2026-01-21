# Repository Guidelines

## Environment & Tooling Fundamentals

- Activate the shared virtualenv with `source /home/tools/.venv/bin/activate` before running Python or pytest.
- Provision or refresh sibling repos with `tools/repos.sh --codex`; they land in `/workspace/{agent,prompt,voice,rms,server}`.
- Install extras into that env and avoid global pip packages; use `uv pip compile` plus `pip install -r requirements.txt` for updates.
- Stick with built-in linting; do not add formatters unless the project maintainers ask for them.
 - Prompt repo updates: CLI entrypoints may log a compact message like `git pull --rebase failed (…); retrying plain pull (common cause: local uncommitted changes)` when local changes block `git pull --rebase`. This is expected; the code automatically falls back to a plain `git pull` and continues.

## Repository Orientation

- `app/`, `cli/`, `lib/` — core runtime, CLI entrypoints, and shared library code.
- `actions/` — FastAPI REST surface for GPT Actions clients. It re-exports the public `call.lib.api` helpers and enforces bearer auth; keep OpenAPI metadata (`actions/openapi.json`) up to date when adding endpoints.
- `mcp/` — Model Context Protocol (MCP) server implementation built with `fastmcp`. Tools here should stay in sync with the REST surface and reuse `call.lib.api` helpers only (avoid reaching into app internals).
- `docs/`, `README.md`, `tg-user-guide*.md` — user and operator documentation. Fixtures for shared tests live in `conftest.py`.
- `telegram_bot/` — production Telegram bot implementation. It wraps the public API layer, so changes here must preserve the structured envelopes returned by `call.lib.api`.
- `wallet/` — secure credentials (e.g., Google service-account JSON). Treat everything under this folder as sensitive and never check real secrets into commits or logs.
- `windsurf/` — IDE preset (`settings.json`) for the Windsurf editor. Update it in lock-step with repo-wide tooling changes (linters, formatters, etc.).
- `requirements.txt` — pinned Python dependencies for the runtime/CLI/bot surfaces. Update via controlled tooling (e.g., `uv pip compile`) and test under `/workspace/.venv`.
- `mcp_config.yaml` / `mcp_config.json` — declarative configuration for external MCP services (filesystem, sequential thinking, Google Sheets, voice, etc.). Keep comments synchronized with actual presets and adjust CLI/docs when toggling defaults.
- `tools/` — helper scripts (`repos.sh`, etc.) for managing environments and dependencies.

Review the relevant directory documentation before making changes; many subsystems have README notes or inline comments that capture nuanced behaviors.

## Coding Conventions

- Preserve existing logging patterns (`call.lib.logging.debug_print`, structured envelopes, etc.).
- Keep logging helpers simple; avoid parsing or reformatting tool payloads beyond basic newline/quote normalization so debug output stays faithful to source data.
- Lean on `call.lib.logging.debug_print` for diagnostics and return structured envelopes from `call.lib.api`.
- Keep error handling consistent with the standard envelope outlined in `README.md`.
- Maintain Markdown prompt metadata format when editing prompt files (YAML front matter with `METADATA` blocks).
- When copying attributes between repository dataclasses (`RepoCardRow`, `RunnableConfig`, etc.), pass the values through unchanged. Avoid normalising, trimming, or inventing fallbacks when transferring fields from the database into runtime DTOs.
- When constructing `RunnableConfig` instances from a `RepoCardRow`, assign the row's columns (`id`, `type`, `project`, `agent`, `prompt`, `path`, `url`, `goal`, etc.) directly. Do not rewrite or normalise these fields—use the stored SQLite values as-is and layer metadata-derived attributes separately.
- Use 4-space indentation and ASCII unless a touched file already requires Unicode.
- Access config and dataclass fields directly; avoid `getattr` indirection.

## Coding Principles
- **Preferred engineering principles**:
  - Favor the KISS approach — prefer straightforward solutions, avoid unnecessary abstractions, and remove dead fallbacks.
  - Apply SOLID design tenets; compose behavior through Dependency Injection instead of global state so components stays testable.
  - Keep helper functions small, cohesive, and readable. Extract utilities rather than allowing sprawling functions to accrete conditional branches.
  - Avoid fallback pathways that obscure control flow. Make failure explicit and surface structured errors instead of silent recovery.
  - **Never suppress exceptions silently**: Every `except` block must log the exception before suppressing. Use `logging.debug()`, `logging.warning()`, or `logging.error()` depending on severity. Silent `except: pass` is forbidden.
  - Log every I/O error, even when execution can continue, so operational issues are always observable.
  - Access dataclass and config attributes directly. Do not use `getattr` for fields like `cfg.id` or `sub_cfg.instructions`; rely on explicit attribute access so static analyzers (and tests) stay accurate.

## MCP Lifecycle: Singleton Pattern

### Initialization (Fail-Fast)
- **ENABLE_MCP=1 mandatory** — Aborts with `MCPInitializationError` if disabled
- **Servers created once** — `_validate_and_cache_mcp_config()` validates YAML and creates all MCP servers
- **Cached for reuse** — Server instances stored in `_MCP_SERVERS_CACHE` and reused across all calls
- **No fallbacks** — Init failures terminate immediately

### Server Lifecycle (Singleton)
- **Created at init** — All servers created during `_validate_and_cache_mcp_config()` call
- **Reused per call** — `_prepare_mcp_servers()` returns cached singleton instances
- **Cleanup at shutdown** — `cleanup_mcp_servers()` properly closes all servers and clears cache
- **Performance** — Avoids repeated server creation/destruction overhead

### Development Rules
- Create `AsyncExitStack` in main lifespan context BEFORE initialization
- Call `_set_mcp_exit_stack(exit_stack)` before starting background init
- Initialize MCP in **background task** to avoid blocking initialize response (Claude timeout 60s)
- Use `wait_for_mcp_init()` in tool handlers to ensure servers are ready
- Exit stack in same context: `await exit_stack.__aexit__(None, None, None)`
- This prevents "cancel scope in different task" errors
- Let `MCPInitializationError` propagate

### Unified Owner-Task Initialization Pattern

**All entry points delegate lifecycle work to the MCP owner task:**

```
┌───────────────────────┐        start_mcp_owner_task(tag)
│ entry point (server)  │ ────────────────────────────────▶ ┌──────────────────┐
└───────────────────────┘                                    │ owner task       │
                                                             │  AsyncExitStack  │
                                                             │  preinitialize() │
                                                             │  wait shutdown   │
                                                             └────────┬─────────┘
                                                                      │ stop_mcp_owner_task()
                                                                      ▼
                                                             cleanup_mcp_servers()
```

- **MCP Owner Task** (`start_mcp_owner_task(tag)`) spins up a dedicated task that:
  1. Enters a single `AsyncExitStack`
  2. Calls `preinitialize_mcp_servers_async(tag)`
  3. Waits on an internal shutdown event
  4. Cleans up MCP servers and exits the stack in the same task
- **Shutdown** occurs via `stop_mcp_owner_task()`, which signals the shutdown event and awaits task completion.
- **Autostart**: `wait_for_mcp_init()` starts the owner automatically when invoked from CLI contexts that have not started a lifespan.

Updated entry point hooks:

1. **MCP Server** (`mcp/server.py`)
   ```python
   async def lifespan(app):
       await start_mcp_owner_task("mcp")
       yield {}
       await stop_mcp_owner_task()
   ```
2. **Actions API** (`actions/main.py`)
   ```python
   async def lifespan(app):
       await start_mcp_owner_task("actions")
       yield
       await stop_mcp_owner_task()
   ```
3. **Telegram Bot** (`telegram_bot/bot.py`)
   ```python
   post_init, post_shutdown = create_mcp_lifespan_callbacks("bot")
   ApplicationBuilder().post_init(post_init).post_shutdown(post_shutdown)
   ```
4. **CLI** (`lib/api.py`) — imports `call.app.call` lazily and waits:
   ```python
   from call.app import call as app_call
   await app_call.wait_for_mcp_init(120.0)
   ```

`wait_for_mcp_init()` reuses the existing owner task when available and starts a "waiter" owner otherwise. This keeps CLI invocations safe while avoiding duplicate startup work.

### Tool Handler Pattern
```python
# In mcp_call or any tool/endpoint that needs MCP servers
async def mcp_call(...):
    # Wait for servers to be ready (lazy wait, fast if already ready)
    await wait_for_mcp_init(timeout=120.0)
    
    # Now safe to call API
    result = await api_call_async(...)
    return result
```

### Benefits of Unified Approach
- **Fast startup**: All entry points return control quickly (<1s)
- **Predictable debugging**: Same control flow everywhere
- **Clean shutdown**: AsyncExitStack ensures proper cleanup
- **Zero race conditions**: All cancel scopes in same task
- **Lazy waiting**: First call waits, subsequent calls instant

## MediaGen Runtime Services
- Systemd service names to use:
  - `bot@MediaGenBlenderBot`
  - `mcp@media-gen`
- Auto-restart is handled by systemd (`Restart=on-failure`); keep services enabled:
  - `systemctl enable bot@MediaGenBlenderBot mcp@media-gen`
- After MCP or prompt config changes, restart both:
  - `systemctl restart bot@MediaGenBlenderBot mcp@media-gen`

## Logs & Debugging (Bots + MCP)
- Bot services (journald): `journalctl -u bot@MediaGenBlenderBot -f`, `journalctl -u bot@MediaGenMemeBot -f`
- MCP services (journald): `journalctl -u mcp@media-gen -f`, `journalctl -u mcp@gsh -f`
- MediaGen app logs (`/home/tools/mediagen`):
  - `agents/logs/fastagent-execution.jsonl` (tool call traces)
  - `agents/logs/agent-media-gen-services.log`
  - `agents/logs/fastagent-orchestrator.log`
  - `backend/logs/backend-out.log`, `backend/logs/backend-error.log`
  - `tail -n 100 agents/logs/fastagent-execution.jsonl | rg "fetch-images"`
- Host rollups (if present): `/home/tools/logs/StratoSpace-*.log`
- Local call log (if enabled): `call/logs/0x.log`

## MediaGen Runtime Checklist
- [ ] MCP config paths updated for renamed apps (see `call/mcp_config.yaml`)
- [ ] Systemd services restarted after MCP/env changes
- [ ] Services enabled for auto-restart (`systemctl enable ...`)

## Contribution Workflow

- Plan changes and update code, docs, and tests together.
- Run targeted or full `pytest` suites within the shared virtualenv.
- Update `CHANGELOG.md` plus relevant docs when behavior shifts.
- Record setup or behavior tweaks in `README.md` or subsystem docs.
- Write descriptive commits and PRs summarizing user-facing impact.

Thanks for keeping the repo tidy and well-documented!

## Build, Test & Development Commands

- `pytest` runs all tests; add `-k fragment` to focus.
- For a full run in this repo: `uv sync --active --extra dev` then `PYTHONPATH=/home/tools uv run --active pytest`.
- `python -m cli.main --help` explores CLI workflows; `uvicorn actions.main:app --reload` serves REST locally.
- Use `tools/` scripts to capture integration traces.

## Testing Guidelines

- Co-locate `test_*.py` with code; share setup in `conftest.py`.
- Add integration coverage when altering `actions/` or `mcp/`; mirror documentation-driven smoke checks when prompts change.
- Run `pytest --maxfail=1 --disable-warnings` before pushing and ensure new features ship with meaningful assertions.

## Commit & Pull Request Guidelines

- Write imperative, scope-prefixed subjects (for example, `mcp: tighten tool auth`) with bodies describing motivation and follow-up work.
- Link tickets, include screenshots or sample payloads when behavior shifts, and record manual verification steps.
- Update `CHANGELOG.md` and relevant docs whenever user-facing behavior or configuration toggles.
