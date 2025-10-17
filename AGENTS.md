# Repository Guidelines

## Environment & Tooling Fundamentals

- Activate the shared virtualenv with `source /workspace/.venv/bin/activate` before running Python or pytest.
- Provision or refresh sibling repos with `tools/repos.sh --codex`; they land in `/workspace/{agent,prompt,voice,rms,server}`.
- Install extras into that env and avoid global pip packages; use `uv pip compile` plus `pip install -r requirements.txt` for updates.
- Stick with built-in linting; do not add formatters unless the project maintainers ask for them.

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
  - Apply SOLID design tenets; compose behavior through Dependency Injection instead of global state so components stay testable.
  - Keep helper functions small, cohesive, and readable. Extract utilities rather than allowing sprawling functions to accrete conditional branches.
  - Avoid fallback pathways that obscure control flow. Make failure explicit and surface structured errors instead of silent recovery.
  - Log every exception and every I/O error, even when execution can continue, so operational issues are always observable.
  - Access dataclass and config attributes directly. Do not use `getattr` for fields like `cfg.id` or `sub_cfg.instructions`; rely on explicit attribute access so static analyzers (and tests) stay accurate.

## Contribution Workflow

- Plan changes and update code, docs, and tests together.
- Run targeted or full `pytest` suites within the shared virtualenv.
- Update `CHANGELOG.md` plus relevant docs when behavior shifts.
- Record setup or behavior tweaks in `README.md` or subsystem docs.
- Write descriptive commits and PRs summarizing user-facing impact.

Thanks for keeping the repo tidy and well-documented!

## Build, Test & Development Commands

- `pytest` runs all tests; add `-k fragment` to focus.
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

