feat(call): add Actions API and MCP server; migrate endpoints/tests from voice; decouple repos

- Actions API (FastAPI):
  - New module `call/actions/main.py` with endpoints `/agents`, `/prompts`, `/call`, `/exec` (JSON payload). Secured with bearer guard in `call/actions/deps.py`.
  - OpenAPI `servers` set to `https://call-actions.stratospace.fun`.
  - Error envelopes with `error_code` preserved for CLI/403 mappings.

- MCP server (FastMCP):
  - New module `call/mcp/server.py` exposing tools `agents`, `prompts`, and `exec` (JSON payload).
  - No references to `voice` repo.

- Tests:
  - Moved unit tests for actions to `call/app/tests/test_actions_api_unit.py`.
  - Added `test_builder_config.py` to cover `build_runnable_instructions_config()` DTO fields and NO_DATA_FOUND.

- Env/infra:
  - Added `server/mcp/call.env` with PORT=205 (MCP) and PORT2=1205 (Actions).
  - Nginx: mapped `call-mcp.stratospace.fun -> 205` and `call-actions.stratospace.fun -> 1205`; included in TLS server_name.

- Decoupling policy:
  - `voice` now invokes `call` via CLI subprocess (no `call.lib.*` imports).
  - Endpoints `/agents`, `/prompts`, `/call`, `/exec` live only in the `call` repo.

Status:
- Call suite OK after migration (will run in CI along with voice)