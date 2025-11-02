# Instructions Mode Rollout Plan

## Scope Overview
- Enable executing ad-hoc instructions without naming an existing prompt/agent/project.
- Accept new syntaxes starting with `@` that wrap inline or fenced code blocks and treat the block contents as the instruction payload.
- Introduce an `instructions` execution mode (replacing the current "void" fallback) that pipes the captured instructions through the runtime without resolving repository cards.
- Extend MCP tooling to allow persisting the iterated instructions into the prompt repository (draft) after validation.

## Phase 1 – Current-State Analysis
1. **Audit call surfaces**
   - Telegram bot (`telegram_bot/bot.py`): review `_resolve_agent_and_input()`, `_call_task()`, and payload builders for how `target`/`input` are transported.
   - CLI (`cli/main.py`): inspect `call` command parsing and how target vs input is forwarded.
   - Actions API (`actions/main.py`) and FastAPI routes: document current request schema for `target` vs `input`.
   - MCP server (`mcp/server.py` and tools under `mcp/`): identify entry points that synthesize `call_api.call_async()` payloads.
2. **Map runtime behavior**
   - `call.lib.api.call_async()` and `build_runnable_instructions_config()` – understand "void" path, config defaults, and where instructions currently come from.
   - `app/call.py` execution pipeline – ensure responses/logging assume a card-backed run.
   - Existing "void" tests in `app/tests/test_builder_config.py` & friends – catalogue expectations that must migrate to "instructions" terminology.
3. **Document data contracts**
   - Capture current schemas for `exec` payloads (CLI/Actions/MCP) to plan backwards-compatible changes.
   - Inventory docs mentioning "void" mode (README, tg guides, docs/specialized-bots, changelog entries).

## Phase 2 – Input Parsing & Payload Construction
4. **Syntax detection helpers**
   - Implement shared utility (e.g., `lib/text_parsing.py`) to recognize the new `@` + code/block patterns and return normalized instructions text.
   - Unit-test the helper covering:
     - `@` + inline backticks (`@` `code`)
     - `@
       ```
       code
       ```
       ``, ``@````, and multi-backtick variants
     - Leading/trailing whitespace, mixed content, rejection cases when no fenced block is present.
5. **Telegram bot integration**
   - Update `_resolve_agent_and_input()` to detect instructions-form messages and emit `instructions` instead of `target`.
   - Ensure `_call_task()` passes `instructions` argument to the async call and sets mode flag (instructions vs legacy).
   - Update payload logging (`[bot] [PAYLOAD]`) to reflect new field.
6. **CLI / Actions / MCP**
   - Extend CLI parsing to accept `--instructions` and auto-detect `@` code blocks when no `--target` is supplied.
   - Actions API: allow `instructions` field in payloads and ensure FastAPI schema updates remain backwards compatible.
   - MCP tools (`exec`): accept an `instructions` key; ensure builder only sends one of `target` or `instructions`.

## Phase 3 – Runtime & Persistence Changes
7. **call_async / builder adjustments**
   - Introduce `instructions` parameter across `call_async`, `build_runnable_instructions_config`, and `RunnableConfig`.
   - When `instructions` is supplied, skip target interpretation, set `cfg.instructions` accordingly, and mark mode="instructions".
   - Replace existing "void" terminology with "instructions" while keeping backwards compatibility aliases if required.
8. **Execution pipeline updates**
   - Ensure downstream consumers (logging, result formatting, telemetry) recognize the new mode and avoid referencing `target`.
   - Review fallback behaviors (e.g., auto-select agent for project-only) to ensure they do not trigger during instructions mode.
9. **REPL save flow via MCP**
   - Enhance the appropriate MCP write tool to:
     - Accept proposed prompt name + body.
     - Validate uniqueness via repo DB before writing.
     - Persist markdown card under `prompt/draft/` and update SQLite index (trigger `reload` or direct DB update).
   - Log success/failure clearly for interactive sessions.

## Phase 4 – Documentation & Developer Experience
10. **Docs overhaul**
    - README, tg guides (EN/RU), specialized bot docs: describe instructions mode, syntax, and universal-bot behavior.
    - Actions/CLI/MCP docs: add examples of REPL workflow and saving prompts.
    - Update changelog entries to capture the feature.
11. **Telemetry/logging**
    - Add concise logging to indicate entry into instructions mode (with sanitized preview of instructions).
    - Ensure debug tracing for MCP save operations.

## Phase 5 – Testing & Validation
12. **Add automated tests**
    - Unit tests for parsing helper and builder changes.
    - Integration tests for Telegram handler (async) using AnyIO fixtures to cover instructions payload.
    - CLI/MCP end-to-end tests validating instructions execution and save-to-draft flow.
13. **Regression sweep**
    - Re-run existing suites (`pytest`, lint, mypy if applicable) ensuring no regressions.
    - Manual validation recipe documenting REPL workflow from Telegram and CLI.

## Phase 6 – Rollout Checklist
14. **Backward compatibility review**
    - Confirm legacy payloads (target-based) continue to work unchanged.
    - Provide migration notes for clients relying on "void" mode.
15. **Deployment readiness**
    - Update plan with implementation subtasks as progress is made.
    - Coordinate release notes and communication for team adoption.
