# MCP Initialization — Unified Background Init Plan

## Goal

- Establish a single, predictable MCP initialization lifecycle used across all entry points.
- Avoid blocking startup handshakes while still guaranteeing MCP servers are ready before first tool usage.
- Keep error semantics explicit and observable.

## Scope

- Applies to MCP Server (stdio), Actions API (FastAPI), Telegram Bot (PTB), and CLI.
- Uses shared helpers from `call.app.call` and a singleton AsyncExitStack per process.

## Current Implementation (as of this plan)

- State machine: `_MCPInitState = { NOT_STARTED, IN_PROGRESS, READY, FAILED }` with `asyncio.Event` for waiters.
- Helpers: `preinitialize_mcp_servers_async/sync`, `wait_for_mcp_init`, `_set_mcp_exit_stack`, `_validate_and_cache_mcp_config`, `_prepare_mcp_servers`, `cleanup_mcp_servers`.
- Config: `ENABLE_MCP` must be truthy. `MCP_CONFIG_PATH` optional (defaults to `call/mcp_config.yaml`).
- Servers are created once and cached; lifetime bound to the shared AsyncExitStack entered at startup, closed on shutdown.

### Entry Points

- MCP Server: async lifespan creates AsyncExitStack, calls `_set_mcp_exit_stack`, starts `preinitialize_mcp_servers_async("mcp")` in background, and on tool `call` uses `await wait_for_mcp_init()` before invoking API.
- Actions API: async lifespan mirrors MCP Server pattern; background warm-up via `preinitialize_mcp_servers_async("actions")` and cleanup on shutdown.
- Telegram Bot: uses `create_mcp_lifespan_callbacks()` to wire `post_init/post_shutdown` that enter/exit the AsyncExitStack and warm up MCP in background.
- CLI: lazy init on first use (no pre-init). First call path triggers `_validate_and_cache_mcp_config()` via `_prepare_mcp_servers()`.

## Refactor Plan — call.lib.mcp_manager module

- Create `call.lib.mcp_manager` module with single responsibility: own MCP lifecycle and config (expose a singleton manager object).
- Use this module only from `call.lib` and `call.app`. External entry points (MCP server, Actions API, CLI, Telegram bot) must call exported lib-level helpers (see below) rather than touching the manager directly.

### Module responsibilities

- Manage state machine: `NOT_STARTED | IN_PROGRESS | READY | FAILED`, with readiness and shutdown events.
- Parse and validate config (YAML/JSON) and materialize MCP servers exactly once.
- Own an AsyncExitStack and ensure all servers are created/closed within the same owner context.
- Provide safe accessors for agents runtime to retrieve server instances.

### Concurrency model

- Start an owner task in a dedicated background thread with its own event loop.
- The owner loop creates and enters the AsyncExitStack, builds servers, signals readiness, and listens for shutdown.
- Cross-thread coordination uses `threading.Event` and `loop.call_soon_threadsafe` to schedule async cleanup and to wait for completion deterministically (no cancel-scope cross-task errors).

### Exported lib-level API (only surface for entry points)

- `call.lib.runtime.init_runtime_async(tag: str = "") -> None`
  - Idempotent. Triggers background initialization of the full runtime (including MCP) and returns immediately.
  - Safe to call multiple times from any entry point.

- `call.lib.runtime.ensure_runtime_ready(timeout: float = 120.0) -> Awaitable[None]`
  - Await readiness (used by async handlers like MCP tools and Actions). Raises a descriptive error on failure/timeout.

- `call.lib.runtime.shutdown_runtime() -> None` (sync)
  - Signals shutdown to the owner thread via an event, schedules async cleanup in the owner loop, and blocks until cleanup completes and the thread joins.
  - Idempotent. Safe to call multiple times.

- `call.lib.runtime.get_mcp_servers() -> list`
  - Returns cached server instances for use inside `call.app` (e.g., in `build_and_run_agent`).

### Usage by entry points

- MCP Server (stdio):
  - Startup: call `init_runtime_async("mcp")` and return from lifespan quickly.
  - Handlers: `await ensure_runtime_ready()` before invoking `call.lib.api.call_async`.
  - Shutdown: call `shutdown_runtime()` synchronously.

- Actions API (FastAPI):
  - Startup lifespan: `init_runtime_async("actions")`.
  - Request handlers: `await ensure_runtime_ready()` as needed.
  - Shutdown lifespan: `shutdown_runtime()`.

- Telegram Bot (PTB):
  - `post_init`: `init_runtime_async("bot")`.
  - `post_shutdown`: `shutdown_runtime()`.

- CLI:
  - Process start (main): optionally `init_runtime_async("cli")` (non-blocking);
  - `call` and `call_async`: always wait using `ensure_runtime_ready()` before pipeline execution.
  - Program exit: call `shutdown_runtime()`.

### Migration steps

- Move MCP-specific functions from `call.app.call` into `call.lib.mcp_manager` module functions:
  - `_load_mcp_yaml_config`, state/event handling, config validation, server creation, readiness wait, cleanup.
  - Replace `preinitialize_mcp_servers_async/sync`, `_prepare_mcp_servers`, `wait_for_mcp_init`, `cleanup_mcp_servers`, `_set_mcp_exit_stack` with `call.lib.mcp_manager` module functions.

- Implement `call.lib.runtime` facade exposing only `init_runtime_async`, `ensure_runtime_ready`, `shutdown_runtime`, `get_mcp_servers`.
- Update `call.app.call.build_and_run_agent()` to call `get_mcp_servers()` from lib rather than internal globals.
- Update `mcp/server.py`, `actions/main.py`, `telegram_bot/bot.py`, and `cli/main.py` to use the new lib functions.

### Guarantees

- Async init returns immediately; readiness can be awaited where needed.
- `call` (sync API) and `call_async` wait for runtime readiness before executing.
- Sync destroy always happens in the proper owner thread and blocks until finished.

## Behavior and Error Semantics

- When `ENABLE_MCP` is disabled, initialization raises `MCPInitializationError` and callers surface a structured error; this is intentional to avoid silent partial functionality in MCP-enabled flows.
- When config file is missing or has zero enabled servers, initialization fails with `MCPInitializationError`.
- `wait_for_mcp_init(timeout=120.0)` allows tool handlers to wait deterministically for background warm-up to complete (or fail fast).

## Usage Patterns

- Entry point startup:
  - Create `AsyncExitStack` in the main lifespan/task and call `_set_mcp_exit_stack(exit_stack)`.
  - Start `asyncio.create_task(preinitialize_mcp_servers_async("<tag>"))` to warm up without blocking handshake.
  - On shutdown, await the init task (best-effort), then `await cleanup_mcp_servers()` and `await exit_stack.__aexit__(...)`.

- Tool handler before first use of MCP:
  - `await wait_for_mcp_init(timeout=120.0)`

- Inside the pipeline (build_and_run):
  - `mcp_servers, _ = await _prepare_mcp_servers()` to use the cached singleton instances.

## Configuration

- `ENABLE_MCP=1` — enable MCP integration. Must be set for platforms relying on MCP tools.
- `MCP_CONFIG_PATH` — path to YAML/JSON config (defaults to `call/mcp_config.yaml`).
- Optional: `DEBUG_MODE=1` to disable truncation of large tool outputs in logs during debugging.

## Verification Checklist

- MCP Server
  - `python -m call.mcp.server` starts instantly; first `call` tool waits if warm-up is still running, or proceeds immediately when ready.
- Actions API
  - `uvicorn call.actions.main:app` returns quickly; on first request, MCP is ready or a clear 5xx error is returned with details when init failed.
- Telegram Bot
  - `python -m call.telegram_bot.bot --bot-name <Project>Bot` starts polling immediately; MCP warm-up runs in background and is awaited by the first tool call.
- CLI
  - `python -m call.cli.main call --target <Name> --input "hi"` performs lazy init on first run; subsequent runs are instant.

## Tests and Invariants

- Unit coverage exists for config validation and init flows (e.g., `app/tests/test_mcp_config_yaml.py`).
- Invariants to keep:
  - State transitions are monotonic: NOT_STARTED → IN_PROGRESS → READY|FAILED.
  - Zero enabled servers with `ENABLE_MCP=1` is a hard failure.
  - Tool handlers use `wait_for_mcp_init()` before invoking API when running under MCP server.

## Future Option: Hard-Stop Variant (deferred)

- If desired, promote a “hard-stop” mode (see `plan/mcp-init-hard-stop.md`) where all entry points block startup until MCP reaches READY (or abort on FAILED), including the CLI.
- This plan keeps the current user experience: non-blocking startup for long init (MCP/Actions/Bot) and lazy init for CLI.

## Status

- Patterns described here are implemented in the codebase and aligned with `README.md` and `AGENTS.md`.

## Commit verification and checklist

- **Owner task in app layer**
  - [v] `_mcp_owner_main(tag)` implemented in `call.app.call`
  - [v] `start_mcp_owner_task(tag)` and `stop_mcp_owner_task()` implemented
  - [v] `wait_for_mcp_init()` autostarts owner as `waiter` when needed (CLI-safe)
  - [v] `create_mcp_lifespan_callbacks(tag?)` provided for PTB-style apps

- **Entry points**
  - [v] MCP server (`call/mcp/server.py`) uses `start_mcp_owner_task("mcp")` on startup and `stop_mcp_owner_task()` on shutdown
  - [v] Actions API (`call/actions/main.py`) uses `start_mcp_owner_task("actions")` and `stop_mcp_owner_task()` in lifespan
  - [ ] Telegram bot (`call/telegram_bot/bot.py`) calls `create_mcp_lifespan_callbacks()` without tag — update to `create_mcp_lifespan_callbacks("bot")`
  - [v] CLI (`call/lib/api.py`) waits for readiness in `call_async()` via `await app_call.wait_for_mcp_init(120)`; `call()` routes through `call_async()`

- **Lib refactor (managerization)**
  - [ ] Create `call.lib.mcp_manager` module (with singleton manager) and move MCP lifecycle functions from `call.app.call`
  - [ ] Provide `call.lib.runtime` facade: `init_runtime_async`, `ensure_runtime_ready`, `shutdown_runtime` (sync), `get_mcp_servers`
  - [ ] Update `call.app.call.build_and_run_agent()` to fetch servers via `get_mcp_servers()`
  - [ ] Update entry points to use `call.lib.runtime` facade instead of `call.app.call`

- **Shutdown and sync destroy**
  - [ ] Add a sync `shutdown_runtime()` (lib) for non-async contexts (e.g., CLI teardown, process exit)
  - [ ] Ensure tests and tools call the sync shutdown in teardown when owner was autostarted

- **Docs & tests**
  - [v] AGENTS.md updated to describe owner-task pattern and entrypoint hooks
  - [ ] Add tests for owner autostart (CLI), readiness wait, and orderly shutdown without hangs

## Shutdown observation (pytest logs)

- Observed sequence:
  - **Digest completed**, then service-message cleanup logs
  - Owner task: "cancelled while waiting for shutdown; forcing cleanup", then "shutdown signaled", "cleanup complete"
- Interpretation:
  - `stop_mcp_owner_task()` likely timed out and cancelled the owner while waiting; owner handled `CancelledError`, set the shutdown event, and finished cleanup.
  - No explicit timeout warning was logged, suggesting the task finished promptly after cancellation.
- Follow-ups (plan):
  - [ ] Provide a sync `shutdown_runtime()` in lib and ensure all entry points/tests use it to avoid dangling owner in CLI/pytest contexts
  - [ ] Verify that no other background tasks remain (e.g., service cleanup, git push) and add timeouts if needed
