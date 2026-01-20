# SRS: Normalize error handling and centralized logging

**File:** `srs-logging-refactoring.md`  
**Status:** Draft -> Ready (after M3)  
**Owner:** @slava (Strato Space)  
**Impacted packages:** `lib/`, `cli/`, `actions/`, `app/`, `telegram_bot/`, `mcp/`

---

## 1. Scope and goals

We need to unify the **error format** and **logging behavior** across all entry points (CLI, FastAPI Actions, MCP, library, Telegram bot) and internal helpers. The goal is a single error wrapper, predictable fields, no raw `print`, centralized loggers, and reproducible diagnostics in prod and tests. The foundation and current principles are documented in the repo (KISS, explicit failure paths, "log every exception"). 

---

## 2. Terms and references

- **Error envelope** — the standard JSON with `ok=false`, a nested `error` object, and mirrored `description`. The schema and field order are documented in the repo. 
- **Logging facade** — `call.lib.logging` with `configure_logging`, `get_logger`, `debug_print`, toggles `CALL_DEBUG`, `CALL_LOG_JSON`, and CLI flag `--json-logs`. 
- **Design principles** — KISS, SOLID/DI, explicit errors, observability. 

---

## 3. Observed problems (summary)

1) Parts of the code build errors manually (`{ok:false,...}`), bypassing the common constructor.  
2) Entry points (CLI, HTTP middleware) use raw `print`, bypassing log configuration and JSON mode.  
3) Exceptions are often swallowed without logging, making incidents unreproducible.  
4) Extra `try/except` wrappers around `debug_print` create "black holes."  
5) Docs and tests partially define desired behavior, but there is no regression guard (lint/test) preventing `print` or schema drift. 

---

## 4. Requirements

### 4.1 Functional
- Export **public** error helpers and apply them everywhere.
- Replace all "manual" errors and HTTP/CLI responses with the common helper.
- Standardize CLI error handling, with early `configure_logging` for `--debug/--json-logs`. 
- Log the exception/context before every envelope return.
- Remove raw `print` in runtime code; use `get_logger`/`debug_print`. 

### 4.2 Non-functional
- **Observability:** every exception and I/O error goes to logs (including when execution continues). 
- **Schema stability:** error field order and presence match the documentation. 
- **Compatibility:** preserve codes/exit-codes; update consumers that used the deprecated top-level `code` field. 

---

## 5. Design decisions (best parts from 4 plans)

### 5.1 Canonical error constructors
- Promote private `_error_payload` / `_error_payload_event` to public API:  
  `error_response(...)`, `event_error_response(...)` (names are examples).  
- Guarantee inclusion of: `error`, `error.code`, `error.message`, `error_code`, `description`; optional: `type`, `param`, `provider_code`, `agent`, `project`, `details`.  
- Support simplified inputs (string/Exception) and context propagation (ids, options).  
- Docstrings with examples and schema reference. 

### 5.2 Apply everywhere
- `lib/` (e.g., `reload`, `clear_session`, `exec` payload interpretation) should call `error_response` instead of ad-hoc dicts.  
- `actions/` should build all error `JSONResponse` via the helper; HTTP status synced to `error.code`.  
- `mcp/` should use the same helper for early returns and validation (or pass through already-built envelopes from `lib`).  
- `cli/` should introduce `_emit_error(error_response)`; all `except` paths print it via stderr-safe output, preserving `exit 1`. 

### 5.3 Unified logging instead of `print`
- In `actions` middleware and other paths, use `get_logger("<module>")` + `debug_print` for CALL_DEBUG traces.  
- In `app/`, `agent_utils`, `telegraph_utils`, replace `print` with `debug_print` (noise) and `logger.warning/error` (issues).  
- Early initialization: CLI calls `configure_logging` before first output when `--debug/--json-logs` is set. 

### 5.4 Log before returning
- Before returning `error_response`, log a short line: module, reason, key fields (agent/project, counters, etc.); with `CALL_DEBUG=1` include stack. 

### 5.5 Simplify around `debug_print`
- Remove `try/except: pass` wrappers around `debug_print` - it is already safe; keep a single prefix style `[app]`, `[actions]`, `[repo.scan]`, `[bot]`. 

### 5.6 Logging facade expansion (minimal but useful)
- Add helper `log_exception(logger_name, msg, exc)` and a *log-and-suppress* context manager for consistent messages and traces. (Optional; does not violate KISS.) 

### 5.7 Docs and regression protection
- Update sections **Error payload schema**, **Logging**, and **CLI**; document exported helpers and the ban on `print` in runtime code.   
- Add a lint rule/test that fails CI if `print(` appears outside whitelisted scripts. 

---

## 6. Contracts and interfaces

### 6.1 Error Envelope (canonical)
Server/library/CLI must return/print a single envelope (field order is stable):  
`ok=false`, `error{ code, message, type?, param?, provider_code? }`, `error_code`, `description`, `agent?`, `project?`, `final_output`, `echo`, `session_id?`. 

### 6.2 Public error helpers
```python
def error_response(
    message: str | Exception,
    *,
    code: int | None = None,
    type: str | None = None,
    param: str | None = None,
    provider_code: str | None = None,
    agent: str | None = None,
    project: str | None = None,
    details: dict | None = None,
    echo: bool | None = None,
    session_id: str | None = None,
) -> dict: ...
```
- **Guarantees:** `error` and mirrored `description` are present; when `message` is an exception, `code` and `type` are mapped to reasonable values, and the stack is logged under `CALL_DEBUG=1` (not included in the response).

Similarly: `event_error_response(...)` for event channels.

### 6.3 CLI
- Helper `_emit_error(payload: dict) -> None` prints exactly the envelope; `exit 1`. Logs via `get_logger("cli")`. Options `--debug`, `--json-logs` enable early `configure_logging`. 

### 6.4 Logging
- Toggles: `CALL_DEBUG`, `CALL_LOG_JSON`, CLI `--json-logs`, optional file via `CALL_LOG_FILE`. 
- Requirement: **no `print`** for diagnostics in `app/`, `actions/`, `lib/`, `telegram_bot/`, `mcp/`. User-facing normal CLI output is allowed; errors only via envelope + stderr. 

---

## 7. Compatibility and migration

- Docs already state that the deprecated top-level field `code` was removed; status is read from `error.code`. Verify external consumers and update them. 
- Preserve `exit 1` in CLI on `ok:false`. 
- Do not change JSON field order in the envelope. 

---

## 8. Implementation plan (Milestones)

- **M1 - API**
  - Export `error_response`, `event_error_response`; docstrings; basic tests.
- **M2 - Adoption**
  - Move `lib/` and `actions/` to helpers; MCP early returns; CLI `_emit_error`.
- **M3 - Logging**
  - Remove `print`; migrate middleware/utils to `get_logger`/`debug_print`; early `configure_logging` in CLI.
- **M4 - Exceptions**
  - Guarantee "log before return"; focused messages with context.
- **M5 - Docs & Tests & Lint**
  - Update README/AGENTS; add lint rule for `print(`; regression tests (CLI/Actions/Bot).
- **M6 - (Optional) Logging Helpers**
  - `log_exception` and context manager; test coverage.

---

## 9. Acceptance criteria

- All changed failure paths return an envelope **strictly by schema** (presence of `error`, mirrored `description`, valid `error.code`/`error_code`).   
- No "manual" error dicts in `actions` and `cli`; covered by tests.  
- No raw `print` in `app/`, `actions/`, `lib/`, `telegram_bot/`, `mcp/` (lint fails if present).   
- With `CALL_DEBUG=1`, exceptions are logged with stack before returning the envelope; the response contains no stack.  
- CLI with `--json-logs` emits JSON logs; errors are printed consistently. 

---

## 10. Test plan (minimal regression)

- **Unit (lib):** error helpers - required fields; string/Exception; options (`provider_code`, `param`).  
- **Actions (FastAPI):** invalid requests to `/prompts`, `/exec` -> envelope; status = `error.code`; log hook fires.   
- **CLI:** commands `list/models/call/reload/clear-session` - simulate exceptions and verify `_emit_error` + `exit 1` + early logging.   
- **Bot:** targeted tests for logging paths (extend existing tests).  
- **Lint:** rule "no `print(`" for `app/`, `actions/`, `lib/` (whitelist utility scripts). 

---

## 11. Coding policies (excerpt)

- KISS, explicit failure paths, "log every exception and I/O error".   
- Use `call.lib.logging.debug_print` and structured envelopes from `call.lib.api`. 

---

## 12. Risks and rollback

- **Risk:** missed manual error construction -> **Mitigate** by searching for `{\"ok\": False` and `error_code` plus tests.  
- **Risk:** field order violations -> **Mitigate** with snapshot tests and static structure checks.  
- **Rollback:** no feature flags required; changes are reversible at the API helper level (stable signatures).

---

## 13. Success metrics

- % of paths covered by the common error helper (target: 100%).  
- Count of raw `print` in runtime (target: 0).  
- Time to diagnose incidents (p50/p95) - reduced after M3.  
- No regressions in CLI/HTTP formats (all contracts green).

---

## 14. Appendix A - Error envelope example

```json
{
  "ok": false,
  "error": {
    "code": 400,
    "message": "Your input exceeds the context window of this model. Please adjust your input and try again.",
    "type": "invalid_request_error",
    "param": "input",
    "provider_code": "context_length_exceeded"
  },
  "error_code": 400,
  "description": "Your input exceeds the context window of this model. Please adjust your input and try again.",
  "agent": "2-SplitByTopics",
  "project": "UxFab",
  "final_output": null,
  "echo": false
}
```
(See schema docs - field order is stable, `error.message` is mirrored in `description`.) 

```

# End of SRS
