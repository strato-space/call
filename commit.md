feat(call): refine target interpretation; introduce RunnableConfig DTO; wire builder into pipeline

- interpret_target():
  - Precedence is prompt > project > agent.
  - Removed the “direct directory scan” fallback (no filesystem probing for project names).
  - Added a conservative fallback: simple token (without ‘*’) is treated as project only if prompts/agents don’t match.
- New DTO:
  - dataclass RunnableConfig(name, project, prompt_override, merge, agent_yaml_path, base_dir, instructions, model, vs_list, attributes)
- New builder:
  - build_runnable_instructions_config(project, agent, prompt, merge) -> (cfg, err)
  - Uses resolve_agent() and best-effort YAML parsing to populate DTO fields.
- Pipeline wiring:
  - call_async() constructs RunnableConfig and forwards DTO fields to app.call.build_and_run_agent
  - No behavior change for callers; DTO is a non-breaking step.
- Tests:
  - Target interpretation tests ensure exact project resolution without directory fallback.
  - Prepared to add builder tests (RunnableConfig shape and error conditions).

Files:
- call/lib/api.py
  - interpret_target(): removed repo directory fallback; ensure precedence prompt > project > agent
  - + dataclass RunnableConfig
  - + build_runnable_instructions_config()
  - call_async(): now builds DTO and forwards fields to build_and_run_agent

Status:
- All call tests green locally (54 passed)

---

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