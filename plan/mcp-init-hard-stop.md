# MCP Initialization Hard-Stop Plan

## Goal
Ensure MCP-dependent workflows never proceed unless `_initialize_mcp_servers_once()` succeeds. Remove silent fallbacks, block first-run execution until servers are available, and surface fatal errors early across all entry points (CLI, Telegram bot, Actions API, MCP server).

## Phase 0 – Current Constraints
- `_initialize_mcp_servers_once()` returns `(servers_by_name, cfg_yaml)` and unconditionally sets `_MCP_SERVERS_INITIALIZED = True` even when no servers load or an exception occurs.
- `_prepare_mcp_servers()` checks the `_MCP_SERVERS_INITIALIZED` flag; if set but cache is empty it silently proceeds, leading to zero-tool execution.
- Entry points call `preinitialize_mcp_servers_async/sync` best-effort; failures are logged but do not abort startup.
- Tool resolution happens on demand during prompt execution; missing servers manifest as runtime errors.

## Phase 1 – API Contract Changes
1. **Boolean state tracking**
   - Introduce explicit states for MCP initialization: `NOT_STARTED`, `IN_PROGRESS`, `READY`, `FAILED`.
   - Guard accessors (`_prepare_mcp_servers`, `preinitialize_mcp_servers_async/sync`) must consult state and block or raise accordingly.
2. **Initialization semantics**
   - `_initialize_mcp_servers_once()` should:
     - Set state to `IN_PROGRESS` before starting.
     - On success with at least one server: populate cache, set state `READY`.
     - On success with zero servers but MCP enabled: treat as failure (`FAILED`) and raise.
     - On exception: set state `FAILED`, re-raise a descriptive error.
   - Ensure `ENABLE_MCP=0` short-circuits to `READY` with empty cache (platform irrelevant scenario) but emit clear log.

## Phase 2 – Entry Point Enforcement
3. **Blocking startup**
   - CLI (`cli/main.py`): promote `preinitialize_mcp_servers_sync("cli")` to hard requirement. If it raises, exit with non-zero status, explaining MCP init failure.
   - Telegram bot (`telegram_bot/bot.py`): wrap `preinitialize_mcp_servers_sync("bot")` so startup aborts/report fatal message when MCP unavailable.
   - Actions API (`actions/main.py`): during lifespan startup, await `preinitialize_mcp_servers_async("actions")` and fail the application if it raises.
   - MCP server (`mcp/server.py`): ensure initial handshake also blocks until init state is `READY` or `ENABLE_MCP=0`.
4. **Runtime guard**
   - `_prepare_mcp_servers()` must:
     - If state `IN_PROGRESS`: await completion (e.g., by awaiting a shared event/future) before returning.
     - If state `FAILED`: raise a deterministic `MCPInitializationError` to callers.

## Phase 3 – Error Handling & Observability
5. **Custom exception type**
   - Create `MCPInitializationError` in `call.app.call` (or dedicated module) carrying context (`module_tag`, message, root cause).
   - Ensure all call sites catch it and translate into user-facing errors (Telegram banner, CLI stderr, HTTP 503 for Actions).
6. **Logging**
   - Add structured logging for state transitions and failure modes.
   - Emit WARN-level logs when MCP disabled to clarify behavior.

## Phase 4 – Documentation Updates
7. **Architecture docs**
   - README & tg guides: note that MCP-enabled configurations must complete initialization before any prompt runs; failures abort startup.
   - Developer docs (`docs/*`, MCP sections) describing the new failure semantics.
   - Add troubleshooting guide referencing new error messages and config checks.

## Phase 5 – Test Coverage
8. **Unit tests**
   - Extend `app/tests/test_mcp_config_yaml.py` or add new tests to validate state transitions, cache population, and failure modes.
   - Simulate: success, missing config, ENABLE_MCP=0, failure raising exception, zero-server scenario with ENABLE_MCP=1.
9. **Integration tests**
   - CLI test invoking main() with patched `_initialize_mcp_servers_once()` raising; assert process exits with error message.
   - Async test for `_prepare_mcp_servers()` awaiting `IN_PROGRESS` future to ensure blocking behavior.

## Phase 6 – Rollout Checklist
10. **Backwards compatibility**
    - Confirm deployments with ENABLE_MCP=0 remain unaffected.
    - Communicate breaking change to scripts relying on previous best-effort startup.
11. **Release notes**
    - Document requirement for MCP config correctness and how to recover from init failures.
