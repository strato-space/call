# Guidance for Codex Contributors

Welcome! This document provides house rules and quick references for agents working in the `call` repository. Keep it handy when planning changes.

## Environment & Tooling

- **Python virtual environment**: The bootstrap script `tools/repos.sh --codex` provisions a shared virtual environment at `/workspace/.venv`. Always activate it before running Python commands:

  ```bash
  source /workspace/.venv/bin/activate
  ```

  Install any additional dependencies into this environment.
- **Sibling repositories**: The same script clones or fast-forwards the companion repositories (`agent`, `prompt`, `voice`, `rms`, `server`) under `/workspace/`. When you need cross-repo context, look for them alongside this checkout.
- **Testing**: Prefer `pytest` for unit/integration tests. The repo root contains `pytest.ini`; invoke tests via `pytest` (optionally with `-k` filters).
- **Linting & formatting**: Follow existing style in touched files. Do not introduce new formatters without prior guidance.

## Repository Orientation

- `app/`, `cli/`, `lib/` — core runtime, CLI entrypoints, and shared library code.
- `actions/` — FastAPI REST surface for GPT Actions clients. It re-exports the public `call.lib.api` helpers and enforces bearer auth; keep OpenAPI metadata (`actions/openapi.json`) up to date when adding endpoints.
- `mcp/` — Model Context Protocol (MCP) server implementation built with `fastmcp`. Tools here should stay in sync with the REST surface and reuse `call.lib.api` helpers only (avoid reaching into app internals).
- `docs/`, `README.md`, `tg-user-guide*.md` — user and operator documentation.
- `telegram_bot/` — production Telegram bot implementation. It wraps the public API layer, so changes here must preserve the structured envelopes returned by `call.lib.api`.
- `wallet/` — secure credentials (e.g., Google service-account JSON). Treat everything under this folder as sensitive and never check real secrets into commits or logs.
- `windsurf/` — IDE preset (`settings.json`) for the Windsurf editor. Update it in lock-step with repo-wide tooling changes (linters, formatters, etc.).
- `requirements.txt` — pinned Python dependencies for the runtime/CLI/bot surfaces. Update via controlled tooling (e.g., `uv pip compile`) and test under `/workspace/.venv`.
- `mcp_config.yaml` / `mcp_config.json` — declarative configuration for external MCP services (filesystem, sequential thinking, Google Sheets, voice, etc.). Keep comments synchronized with actual presets and adjust CLI/docs when toggling defaults.
- `tools/` — helper scripts (`repos.sh`, etc.) for managing environments and dependencies.

Review the relevant directory documentation before making changes; many subsystems have README notes or inline comments that capture nuanced behaviors.

## Coding Conventions

- Preserve existing logging patterns (`call.lib.logging.debug_print`, structured envelopes, etc.).
- Keep error handling consistent with the standard envelope outlined in `README.md`.
- Maintain Markdown prompt metadata format when editing prompt files (YAML front matter with `METADATA` blocks).
- When copying attributes between repository dataclasses (`RepoCardRow`, `RunnableConfig`, etc.), pass the values through unchanged. Avoid normalising, trimming, or inventing fallbacks when transferring fields from the database into runtime DTOs.
- When constructing `RunnableConfig` instances from a `RepoCardRow`, assign the row's columns (`id`, `type`, `project`, `agent`, `prompt`, `path`, `url`, `goal`, etc.) directly. Do not rewrite or normalise these fields—use the stored SQLite values as-is and layer metadata-derived attributes separately.
- **Preferred engineering principles**:
  - Favor the KISS approach — prefer straightforward solutions, avoid unnecessary abstractions, and remove dead fallbacks.
  - Apply SOLID design tenets; compose behavior through Dependency Injection instead of global state so components stay testable.
  - Keep helper functions small, cohesive, and readable. Extract utilities rather than allowing sprawling functions to accrete conditional branches.
  - Avoid fallback pathways that obscure control flow. Make failure explicit and surface structured errors instead of silent recovery.
  - Log every exception and every I/O error, even when execution can continue, so operational issues are always observable.
  - Access dataclass and config attributes directly. Do not use `getattr` for fields like `cfg.id` or `sub_cfg.instructions`; rely on explicit attribute access so static analyzers (and tests) stay accurate.

## Contribution Workflow

1. Plan your change and update docs/tests alongside code.
2. Run targeted or full `pytest` suites inside the activated virtual environment.
3. Update `CHANGELOG.md` when behavior or documentation changes materially.
4. Reflect notable setup or behavior tweaks in `README.md` or subsystem docs.
5. Commit with descriptive messages; open PRs summarizing user-facing impact.

Thanks for keeping the repo tidy and well-documented!
