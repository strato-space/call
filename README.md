# Call — Runtime and Sync

## Session ID — derivation and override (New)

- Format: `chat` or `chat:thread` (agent name is not part of the session id).
- When `session_id` is provided to `call()/call_async()` (or to Actions/MCP/CLI), it takes precedence and is used to derive Telegram routing:
  - The library parses `chat_id`/`thread_id` from the provided `session_id`.
  - Environment defaults are NOT used in this case.
- When `session_id` is not provided:
  - If `chat_id`/`thread_id` args are provided, they are used (with env defaults filling missing pieces).
  - If neither is provided, no session is created and no Telegram messages are sent. The response omits `session_id`.
- On success and on error, when a session is known, responses include `session_id`.

### Exec payload contract

- The single JSON payload accepted by Actions and MCP is:
  - `{ project?: string, agent?: string, prompt?: string, target?: string, context?: any, echo?: boolean, session_id?: string }`
  - Exactly one of `project|agent|prompt|target` must be provided.
  - The full payload JSON is used as the input string for the agent pipeline.
## Overview

A minimal, extensible subsystem for invoking AI agents and prompt pipelines by name and routing inputs/outputs across your project ecosystem.

Call provides a unified invocation syntax, consistent logging, and pluggable backends to run agents and prompts defined in Prompt Repository and ai-team. It is designed to be simple first, then grow into a full orchestration layer.

> Recent changes: see `CHANGELOG.md`.

### Repo Index (New)

- Call now maintains a single-source-of-truth SQLite index `call/repo.db` for projects, agents, and prompts.
- Table: `repo(target PRIMARY KEY, project, agent, prompt, path, state, engine, orchestration)`
  - `target` values store only the final name/id (no prefixes):
    - projects: `<project>`
    - agents: `<agent>`
    - prompts: `<prompt>`
  - `state`: `draft` if file path contains the substring `draft`, otherwise `ready`.
  - `engine`: runtime engine hint (e.g., `openai`, `openai-agents`) — pulled from METADATA YAML where present.
  - `orchestration`: control flow (`llm`, `handoff`, `langgraph`, ...) — pulled from METADATA YAML where present.

- Reloading index: `from call.lib.api import reload as call_reload; call_reload()`
  - The repos to include are defined by `repos` in `.env` (comma- or semicolon-separated), for example: `repos=agent,prompt`.
  - CLI provides `reload` to perform the same operation. A legacy alias `scan` is still available.

- Listing:
  - Hierarchical: `call.lib.api.list(project?, agent?, prompt?, state?, target?)`
  - Flat prompts: `call.lib.api.list_prompts(project?, agent?, prompt?, state?, target?)`
  - CLI supports output formats: `json | yaml | text`.

- Find helpers (arrays):
  - `call.lib.repo.find_projects(project?, target?)`
  - `call.lib.repo.find_agents(project?, agent?, target?)`
  - `call.lib.repo.find_prompts(project?, agent?, prompt?, state?, target?)`

- Wildcards and security:
  - All filters support `*` and are merged with AND semantics.
  - `target` also supports `*` and is applied after other filters.
  - This allows scoping access to a single project (e.g., pass `project=MyProject` always).

### Prompt format (MD-only)

- Prompts and cards are Markdown-only. Each file follows the Strato Prompt Framework with a `METADATA` fenced YAML block and an optional `PROMPT` block.
- The index logs warnings for `.md` prompts missing valid `METADATA`. In strict paths (e.g., CLI `--print-instructions`), malformed or missing `METADATA` surfaces a 400 error.

#### Model settings in METADATA (Updated)

- Keys
  - `model`: the selected model id (e.g., `gpt-5`, `gpt-4.1`).
  - `model-params`: generic settings applicable across models.
  - `model-params-<model>`: model-specific settings. This is the recommended, canonical form.

- Excluded (do not use in new cards)
  - `model_params`, `modelParams` (generic) and `model_params_<model>`, `modelParams<model>` (model-suffixed) are not part of the documented schema and must be avoided. Use the hyphenated forms `model-params` and `model-params-<model>` instead.
  - Backward-compatibility: the runtime may still recognize these legacy keys, but they are deprecated and will be removed. Prefer hyphenated keys in all new/updated cards.

- Recognized fields in params
  - `temperature`, `top_p`, `frequency_penalty`, `presence_penalty`, `max_tokens`, `verbosity` (`low|medium|high`)
  - `reasoning: { effort: minimal|low|medium|high, summary?: auto|concise|detailed }`

- Example

```yaml
model: gpt-5

model-params-gpt-5:
  reasoning:
    effort: medium

model-params-gpt-4.1:
  temperature: 0.2
  top_p: 0.9
```

---

## Telegram Bot (Updated)

- New command: `/reload` — rescans configured repositories and rebuilds the SQLite repo index.
- `/prompts`, `/prompts_ready`, and `/prompts_draft` are powered by the repo index with flexible filters and `state` support.
  - Filters: `--project`, `--agent`, `--prompt`, `--target`, `--state ready|draft`, key=value forms, and `@Agent` shorthand. All filters are ANDed.
- The bot passes `target` to the library which supports wildcards: precedence is `prompt > agent > project`.

### Telegram parsing and behavior (Updated)

- Private DMs
  - Plain text (no @) is treated as input-only (equivalent to `/call <input>`).
  - `@Target <input>` executes when Target exists (prompt > agent > project lookup).
  - Leading `@BotName` is allowed and stripped for convenience in private chats.
  - A single `@ <input>` means input-only (no target).

- Group chats
  - Only messages with an explicit @-mention are handled (to avoid reacting to every message).
  - `@Target <input>` executes only if Target exists; otherwise the message is ignored.
  - `@BotName Target <input>` executes when Target exists; if Target is not valid, the text is treated as input-only.
  - `@ <input>` is treated as input-only (no target).

- Validation
  - Targets are validated via DB-only calls before scheduling runs: `resolve_agent()` for agents/prompts and `list(project=...)` for projects. Project scoping is derived from the bot name (e.g., `AgentFabBot` scopes to `AgentFab`), while `StratoSpaceAiBot` lists all projects.
  - Enable rich debug logs with `CALL_DEBUG=1` to see `[bot]` parsing decisions.

### CLI Quickstart

Use the project virtual environment interpreter for consistency.

```powershell
python -m call.cli.main list --project UxFab
python -m call.cli.main call --project UxFab --agent DialogPostAnalysis --prompt 33-Questioning --print-instructions
python -m call.cli.main exec --project UxFab --agent DialogPostAnalysis --content-item "https://docs.google.com/document/d/FILE_ID/edit"
.
# Reload repositories and print result as YAML
python -m call.cli.main reload --repos agent,prompt --format yaml

# List agents/projects as text
python -m call.cli.main agents --project * --format text

# List prompts with engine/orchestration columns
python -m call.cli.main prompts --project * --agent * --state ready --format table
```

## Call vs Exec (Updated)

- call (keyword-based)
  - Selectors are provided as flags: `--project`, `--agent`, `--prompt`, `--target`.
  - `--input` passes raw text; `--parse-input` uses the shared Telegram parser to build a JSON payload (tokens such as `@3-OnlineChunkSummarization` may resolve into `context`).
  - `--print-instructions` prints the merged instructions for the selection and exits (no execution).
  - `--echo` prints the payload preview and resolved selection snapshot.
  - `--format json|yaml|text` controls output format for previews and listings.

- exec (payload-based)
  - Merges selectors and content items into a single JSON payload (best for content buckets and Actions/MCP).
  - If exactly one selector among `project|agent|prompt|target` is provided, the CLI uses the single-source-of-truth validator `interpret_exec_payload()`; otherwise it falls back to a backward-compatible path and calls using explicit selectors with the full payload JSON as input.
  - `--echo` prints the merged payload and exits (no execution).
  - `--format json|yaml|text` controls output format.

Examples (PowerShell):

```powershell
# call with raw input
python -m call.cli.main call --target AgentFab --input "as is text"

# call with parsed input (Telegram-identical payload)
python -m call.cli.main call --target AgentFab --parse-input "@3-OnlineChunkSummarization" --echo --format yaml

# exec with content items
python -m call.cli.main exec --project UxFab --agent DialogPostAnalysis \
  --content-item "https://docs.google.com/document/d/FILE_ID/edit" \
  --content-item '{"type":"text","text":"Hello"}' --output-type html

# exec with multiple selectors (falls back to explicit call path)
python -m call.cli.main exec --project UxFab --agent DialogPostAnalysis --target 33-Questioning --echo
```

### Parsed vs raw input (New)

- `--input` — passes the text as-is, no token parsing and no context building.
- `--parse-input` — uses the shared Telegram parser to build a JSON payload with predictable order (target, replay, input, context). Tokens inside the text (like `@3-OnlineChunkSummarization`) are resolved via the DB and may add `context` items.
- Mutually exclusive: only one of `--input` or `--parse-input` may be provided.

- Echo-only preview (New):
  - When `--echo` is given, the CLI does not execute the pipeline. It prints:
    - `payload`: the JSON payload that would be sent to the agent.
    - `resolved`: a small snapshot of the selection (project/agent/prompt/type/path/url) computed from the current filters/target.
  - Examples:
    ```powershell
    # Raw input, print payload only (no execution)
    python -m call.cli.main call --target AgentFab --input "as is text" --echo

    # Parsed input, print Telegram-identical payload (no execution)
    python -m call.cli.main call --target AgentFab --parse-input "@3-OnlineChunkSummarization" --echo

    # Parsed from JSON object (CLI --target takes precedence over the object's target)
    python -m call.cli.main call --parse-input '{"target":"AgentFab","input":"@3-OnlineChunkSummarization","context":[]}' --echo
    ```

  - Typical `--echo` output (PowerShell + `jq` formatting):

    ```json
    {
      "payload": {
        "target": "AgentFab",
        "input": "@3-OnlineChunkSummarization @11-ExtractUserPain",
        "context": [
          {
            "type": "file",
            "name": "3-OnlineChunkSummarization.md",
            "path": "prompt/draft/3-OnlineChunkSummarization.md"
          },
          {
            "type": "file",
            "name": "11-ExtractUserPain.md",
            "path": "prompt/draft/11-ExtractUserPain.md"
          }
        ]
      },
      "resolved": {
        "project": "AgentFab",
        "agent": null,
        "prompt": null,
        "type": "project",
        "path": "agent/AgentFab/project.md",
        "url": "https://github.com/strato-space/agent/blob/master/AgentFab/project.md"
      }
    }
    ```

  - Add `--download-context` to inline file data in payload (see below).

- Agents/list filters:
  - `--state` — filter prompts nested under agents by state (`ready|draft`, supports `*`)
  - `--target` — unified filter applied last (supports `*`). Matches plain names in the `target` column.
  - Alias `projects` is available for the `agents` command: `python -m call.cli.main projects`.

- Prompts filters:
  - `--target` — unified filter applied last (supports `*`), in addition to `--project`, `--agent`, `--prompt`, and `--state`.
  - Output formats: `--format json|yaml|table|text`.

- Context download (New):
  - `--download-context` — when building the payload (via `--parse-input` or echo-building for `--input`), the CLI will attempt to inline context items by:
    - Reading `content` for text files (from `path` or `url`)
    - Adding `base64` for non-text (binary) files
  - Text detection uses MIME type when available, with a fallback by extension (`.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.csv`, `.tsv`).
  - HTTP fetching uses `httpx` if available; otherwise, it’s skipped.

- Import conventions in examples:
  - `from call.lib import api as call_api`
  - `from call.lib import repo as call_repo`
  - `from call.lib.logging import configure_logging as call_logging`

- Exit codes: `0` on success (`ok:true`), `1` on error envelopes (`ok:false`).
  - PowerShell: check `$LASTEXITCODE`
  - cmd.exe: check `%ERRORLEVEL%`

### Logging

- Toggle verbose debug logs with `CALL_DEBUG=1`.
- Centralized helper: `call.lib.logging.debug_print()`.
- Module prefixes used in debug lines:
  - `[app]` — application layer (agent run, welcome banner, notifications)
  - `[discovery]` — discovery and indices
  - `[bot]` — Telegram bot layer (update summaries appear as `[UPDATE] [bot] ...`)

- Configuration:
  - CLI and Telegram bot call `configure_logging()` once at startup (DEBUG when `CALL_DEBUG=1`, else INFO).
  - Library usage assumes logging is already configured by the host application.
  - Set `CALL_LOG_JSON=1` to emit JSON logs from the stdlib logger (stderr). Example:
    ```powershell
    $env:CALL_DEBUG=1; $env:CALL_LOG_JSON=1; python -m call.cli.main call --project UxFab --agent DialogPostAnalysis --print-instructions
    ```
  - CLI also supports `--json-logs` to force JSON output regardless of env:
    ```powershell
    python -m call.cli.main --json-logs call --project UxFab --agent DialogPostAnalysis --print-instructions
    ```
  - Set `CALL_LOG_FILE=logs/app.log` to write logs to a file in addition to stderr. The directory will be created if needed.

- Notes:
  - The Telegram bot uses `get_logger("bot")` so JSON logs appear under `logger: "call.bot"`.
  - `debug_print()` console lines include the logger name prefix (e.g., `[DEBUG] [call.bot] [bot] [UPDATE] ...`).

JSON log sample (stderr):

```json
{"time":"2025-09-18T01:23:45","level":"INFO","logger":"call.bot","message":"Starting polling..."}
```

Example (PowerShell):

```powershell
$env:CALL_DEBUG=1; python -m call.cli.main call --project UxFab --agent DialogPostAnalysis --print-instructions
```

## Key Concepts

### Simplified Agent Discovery (Updated Sep 17, 2025)

- **Directory-based lookup**: Agents are discovered by directory name only under `agent/<Project>/`.
- **Case-sensitive matching (KISS)**: No normalization. Use the exact agent names and aliases as defined in YAML and directories.
- **No registry scanning**: Removed complex metadata matching and registry file processing.
- **Simple syntax**: `@AgentName` or just `AgentName`.

### Target syntax and precedence (Updated Sep 22, 2025)

- Precedence when `target` is provided: `prompt > exact project > agent > fuzzy/wildcard project`.
  - The first category that yields a unique match sets the corresponding fields if not explicitly provided.
  - Exact project matching takes priority over agent resolution when the token exactly equals a project name (case-insensitive equality).
- Supported forms:
  - Unprefixed name with wildcards: `Ux*` (interprets as prompt name first, then agent, then project)
  - Path-like notation: `path:project/agent/prompt`
    - Examples:
      - `path:UxFab/DialogPostAnalysis/33-*` — prompts under agent/project
      - `path:UxFab/DialogPostAnalysis` — agent
      - `path:UxFab` — project
- All lookups respect wildcards `*` and are backed by the SQLite repo index.
- Ambiguity returns an error envelope with `code: "TOO_MANY_ROWS"` and an `options` array.
- No broadening of scope: if `project` or `agent` are explicitly provided, prompt resolution won’t widen the search.

### Agent Loading Logic

- **Zero prompts**: Uses `agent.yaml` content directly as instructions
- **With prompts**: Uses first prompt from `prompts` list, merges with agent metadata
- **Prompt loading**: Extracts first word from prompts list, tries `.md` then `.yaml` extensions
- **Recursive file listing**: All agent directory files added to seed history as filenames list

## Library return shape and errors (Updated Sep 18, 2025)

`call.lib.api.call(name: str, input: str, *, chat_id: int | None = None, thread_id: int | None = None, session_id: str | None = None, echo: bool = False) -> dict`

- On success returns a dict:

```json
{
  "ok": true,
  "agent": "AgentName",
  "agent_path": ".../agent.md",
  "final_output": "...",
  "echo": false,
  "session_id": "AgentName:-100123:10"
}
```

- On failure returns an error envelope:

  ```json
  {
    "ok": false,
    "error_code": 404,
    "description": "no data found",
    "code": "NO_DATA_FOUND",
    "agent": "...",
    "project": "...",
    "final_output": null,
    "echo": false,
    "session_id": "AgentName:-100123:10"
  }
  ```

  Standard codes: `NO_DATA_FOUND`, `TOO_MANY_ROWS`, `INTERNAL_ERROR`, `PIPELINE_ERROR`. Certain upstream errors are mapped, for example Tracing 403 → `error_code: 403`, `code: "REQUEST_FORBIDDEN"`, and `details` with provider payload when available.

Also available (async) — supports empty agent name:

`await call.lib.api.call_async(name: str | None, input_text: str, *, chat_id: int | None = None, thread_id: int | None = None, session_id: str | None = None, echo: bool = False) -> dict`

- If `name` is empty/None: discovery is skipped, an Agent with empty instructions is constructed, and only `input_text` is used. In this case, `agent_path` in the response is `null`.
- If `name` is provided and not found: returns a 404-style error envelope.

Notes:
- If you need an error-envelope style response, wrap the call and convert exceptions into a `{ ok:false, ... }` JSON at your boundary (e.g., HTTP layer).

### Integration note (Voice decoupling)

The Voice repository no longer integrates with Call (no direct library import or CLI proxy). Use Call directly via:

- Python library: `from call.lib.api import call, list, resolve_agent, build_runnable_instructions_config`
- CLI: `python -m call.cli.main ...`
- Actions API: `https://call-actions.stratospace.fun`
- MCP: `call-mcp.stratospace.fun`

## Listing available agents

You can enumerate available agents discovered in the Agent repository via the library, the Actions API, or the MCP server.

- Important (KISS): agent names are exact and case-sensitive across CLI and Telegram. No automatic normalization; use the exact names shown by `list`.

- Library (Python):

  ```python
  from call.lib.api import list as list_agents, resolve_agent

  # Hierarchical listing for a project (projects -> agents with aliases/prompts)
  projects = list_agents(project="UxFab")

  # Resolve a single agent selection (exact/case-sensitive names)
  r = resolve_agent(project="UxFab", agent="DialogPostAnalysis")
  # -> { ok: true, resolved: { project, name, path, aliases, prompts } }
  ```

- Voice Actions API (requires bearer):

  - GET `/agents`
  - Query params: `query`, `include_aliases` (bool), `project_name` (string)

- MCP (mcp-voicebot):

  - Tools:
    - `agents(query?: string, include_aliases?: boolean, project_name?: string)`
    - `prompts(project?: string, agent?: string, prompt?: string, state?: string)`
    - `exec(payload: object)` — single JSON payload (see below)
    - `reload()` — no params; rebuilds indices using `.env` repos

### Running with local virtual environment

Prefer the project venv interpreter to run commands:

```powershell
cd D:\home\strato-space 
.venv\Scripts\Activate.ps1
python -m call.app.call "Vasil3" "рассказывай"
python -m call.app.call "BusinessAnalyticAgent" "приведи @Vasil3 в соответсвие с strato space prompt framework"
```
```bash
cd /home/strato-space 
. .venv/bin/activate.sh 
python -m call.app.call "Vasil3" "рассказывай"
python -m call.app.call "BusinessAnalyticAgent" "приведи @Vasil3 в соответсвие с strato space prompt framework"
```

### CLI usage (Updated Sep 17, 2025)

- **Command Reference**:

  ```bash
  # List projects, agents, prompts (hierarchical)
  python -m call.cli.main list --project UxFab [--agent Agent*] [--prompt Draft] --format json|yaml|text

  # Call an agent (keyword-only API). Use exact case-sensitive names.
  python -m call.cli.main call --project UxFab --agent AgentName --input "text" [--prompt PromptName] [--session-id AgentName:-100123[:thread]] [--print-instructions] [--echo] [--trace SECONDS] [--trace-file PATH]

  # List prompts (flat). Filters: --project, --agent, --prompt, --state, --target (all support *). Formats: json|yaml|table|text
  python -m call.cli.main prompts --project FanFab --prompt 13* --format json
  python -m call.cli.main prompts --project * --agent * --prompt 10* --state ready --target r:* --format yaml

  # Reload repos and rebuild index 
  python -m call.cli.main reload --repos agent,prompt --format json
  # Legacy alias (will be removed):
  python -m call.cli.main scan --repos agent,prompt --format json

  # Execute with structured context (content items). Extracts Google Docs file id from URLs.
  python -m call.cli.main exec --project UxFab --agent DialogPostAnalysis \
      --content-item "https://docs.google.com/document/d/FILE_ID/edit" \
      --content-item '{"type":"text","text":"Hello"}' \
      [--output-type html] [--session-id AgentName:-100123[:thread]] [--print-instructions]

  # Clear conversation sessions for a chat/thread
  python -m call.cli.main clear-session --chat-id -100123 --thread-id 10 [--name AgentName]

  # Legacy positional (app module still supports):
  python -m call.app.call <AgentName> [<input>]
  ```

- **Listing Output (hierarchical)**:
  - `list()` returns an array of projects; each project has `name`, `type: "project"`, and `agents`.
  - Each agent item has `type: "agent"`, `name`, `aliases` (from agent.yaml), `prompts` (from agent.yaml), and `path`.
  - Wildcards `*` are supported in `--project`, `--agent`, and `--prompt` (case-insensitive). Agent filter ignores spaces.

- **Selection Behavior**:
  - If multiple agents match: the API returns an error envelope with `code: "TOO_MANY_ROWS"` and an `options` list of candidates.
  - If nothing matches: `code: "NO_DATA_FOUND"`.
  - On success, responses include `resolved` with `{ project, name, path, aliases, prompts }`.

- **Debugging Features**:
  - `--trace SECONDS`: Periodically dumps all thread stacks (default: stderr)
  - `--trace-file PATH`: Writes stack dumps to specified file

- **Windows console**:
  - The CLI uses UTF‑8‑safe printing to avoid encoding errors on CP‑1251 consoles.
  - Exit code is `0` on success (`ok: true`) and `1` on error envelopes (`ok: false`).

### Troubleshooting & Debugging

- Quiet console output (default): normal CLI runs avoid noisy prints. To enable verbose diagnostics, set `CALL_DEBUG=1`.
- Centralized debug logging: all layers (app, discovery, Telegram bot) use a single `debug_print()` in `call.lib.logging` gated by `CALL_DEBUG`.
- Telegram bot: incoming update summaries are logged via `debug_print` (printed only when `CALL_DEBUG=1`).
- Tracing 403 mapping: upstream errors containing `request_forbidden` or `unsupported_country_region_territory` return
  `{ ok:false, error_code:403, code:"REQUEST_FORBIDDEN", details:{...} }`.
  - Test hook for CI/manual checks:
  - PowerShell: `$env:CALL_FAKE_TRACING_403=1; python -m call.cli.main exec --agent DialogPostAnalysis`
  - cmd.exe: `set CALL_FAKE_TRACING_403=1 && python -m call.cli.main exec --agent DialogPostAnalysis`
  - Text error conversion: if the pipeline returns `final_output` starting with `"Error:"`, the library converts it to a structured error envelope
  (e.g., `error_code: 502`, `code: UPSTREAM_CONNECT_ERROR|PIPELINE_ERROR`) to avoid printing tracebacks to users.

#### Checking exit codes (Windows)

```powershell
# PowerShell
python -m call.cli.main exec --agent DialogPostAnalysis; echo $LASTEXITCODE

# cmd.exe
cmd /c "python -m call.cli.main exec --agent DialogPostAnalysis & echo %ERRORLEVEL%"
```

### Projects-aware discovery (Updated Sep 21, 2025)

- **Project index**
  - Projects are read from the SQLite index `call/repo.db` built by the scanner (MD-only cards).
  - Loader is centralized: `call.lib.discovery.load_projects_index()` reads from the DB only (no filesystem access at runtime).

- **Project/Agent cards (MD-only)**
  - Project card (optional): `agent/<Project>/project.md` — METADATA YAML fenced block for project-level attributes.
  - Agent card: `agent/<Project>/<Agent>/agent.md` — METADATA YAML fenced block with optional `prompts` list (ids) and other attributes; optional PROMPT section for inline body.
  - Cards must be Markdown; YAML files are not used at runtime. The scanner indexes MD-only cards.

- **Agent location**
  - Agents live under their project directory: `agent/<Project>/<Agent>/agent.md`.
  - Example: `agent/UxFab/AiNewsAggr/agent.md`.

-- **Per‑project indices**
  - The library can auto-generate `agent/<Project>/agents.yaml` when missing.
  - Generation sources, in order:
    1) Directory scan of `prompt/<Project>/*/agent.yaml`
    2) If empty, enrich from `agent/<Project>/project.yaml` (top‑level `agents` or nested `project.agents`)
    3) For `AgentFab`, fallback enrich from `agent/AgentFab/agent.yaml` when present
  - Compatibility: `agent/AgentFab/` remains supported as the creator area. The legacy `prompt/agents/` layout is no longer supported.

- **Library API behavior**
  - `list(project, agent, prompt)` returns hierarchical projects → agents with fields: `name`, `aliases`, `prompts`, `path`.
  - `resolve_agent(project, agent, prompt)` returns `{ ok: true, resolved: { project, name, path, aliases, prompts } }` on success.
  - `discover_agent_yaml(name)` looks up indices first, then scans all project folders in the Agent repo. It also resolves the root `AgentFab` agent from `agent/AgentFab/project.yaml` or `agent/AgentFab/agent.yaml`.
  - App layer note: `call.app.call.discover_agent_yaml()` is a thin wrapper that delegates to `call.lib.discovery.discover_agent_yaml`. The legacy `_discover_agent_yaml_compat` helper was removed.
  - Strict schema: when `agent/projects.yaml` exists, it must contain a non-empty top-level `projects` mapping. Otherwise a clear error is raised with a fix suggestion. If the file is missing, a fallback scans the agent repo for plausible project directories.
  - Cross-project discovery: when `project=None`, selection functions (`list`, `resolve_agent`, `discover_agent_yaml`) scan all known projects discovered via `projects.yaml` (or repo scan fallback) until a unique match is found.

- **Wildcards & errors**
  - Wildcards `*` are supported for `project`, `agent`, and `prompt` filters.
  - When selection is ambiguous or empty, errors include machine‑readable `code` and `options`:
    - `TOO_MANY_ROWS` with `options: [...]` for candidate agents
    - `NO_DATA_FOUND` when nothing matches

### Legacy Agent Addressing (Deprecated)

- Agent addressing syntax (canonical): `@[OrgName][AgentName][:PipelineName][:PromptName]`
  - All parts are optional; historically, names were normalized to PascalCase here.
  - Current implementation follows the KISS policy (exact, case‑sensitive names). This addressing mode is documented for historical context only and is not recommended.
  - Defaults: `OrgName=Strato`, `AgentName=DiscoveryAgent`, `PromptName=DiscoveryAgentPrompt`.
  - Examples:
    - `@`
    - `@DiscoveryAgent`
    - `@StratoDiscoveryAgent`
    - `@UralAiNewsAggr:DailyDigest` (pipeline)
    - `@UralNewsAggr:FetchHeadlines` (prompt)

- Payload → Output routing:
  - Format: `@agent-name [payload] [--> output-destination]`
  - Payload types: `text | replay-message-id | document | voice-message | voicebot-dialog-id | url | google-sheet-url [sheet-name | sheet-id | range] | google-sheet-id [sheet-name | sheet-id | range]`
  - Output destinations: `[ repo-name | file-path | google-sheet-url | google-sheet-id | range | telegram-chat-id | telegram-thread-id | @agent-name ]`
  - This enables chaining, similar to Unix pipelines: `command1 | command2 | command3` using `-->` (from Mermaid Flowchart Diagram).

- Logging & traceability:
  - Each end-to-end chain receives a unique id (MongoDB ObjectId-like).
  - Full chain logging from start to finish into a dedicated log file.
  - Designed for replay and audit; future: Prometheus-style metrics endpoints.

### Operational Logging

Use these tips to inspect logs locally or in CI.

- PowerShell: tail and filter by module prefix
  ```powershell
  Get-Content -Path .\logs\app.log -Wait | Select-String -Pattern "\[app\]"
  ```

- Bash/Cygwin: follow logs and filter JSON by level with jq
  ```bash
  tail -F logs/app.log | jq -r 'select(.level=="INFO") | .message'
  ```

- Extract bot updates from JSON logs
  ```bash
  tail -F logs/bot.log | jq -r 'select(.logger=="call.bot") | .message'
  ```

- Convert JSON logs to a simple table (time, level, message)
  ```bash
  jq -r '[.time, .level, .message] | @tsv' < logs/app.log
  ```

## Position in the Ecosystem

Call integrates with and executes artifacts produced by:

- Agent Fab — a factory of early analytical agents; cards live in the Agent repo under `agent/AgentFab`.
- Prompt Repository — canonical storage of prompts under flat folders: `prompt/ready/`, `prompt/draft/`.
- RAG and MCP servers — optional data access and tool affordances:
  - Filesystem: root /home/strato-space, main repos: prompt [prompt repository], call [this repo], server [mcp's starter, nginx cofings], rms [sample of project repo], voice []   
- Voice Bot, AI News Aggregator, Telegram, Google Sheets/Pages — integration touchpoints.

-- Repos list:
  - agent - agent repository (projects, agents index);
  - prompt - prompt repository (draft/ready);
  - call - this repo;
  - server mcp's starter, nginx cofings 
  - rms sample custromer's of project repo, 
  - voice - voicebot backed lib, mcp, actions, cli interfaces   

### Repo sync helper (repos.sh)

Use the `call/repos.sh` script to keep local clones of our primary repositories up to date. It works in Git Bash, WSL, and Linux.

- Behavior:
  - `repo <git-url> [dir]` clones into `dir` if it doesn’t exist.
  - If `dir` exists and is a Git repository, it runs `git -C dir pull --ff-only`.
  - If `dir` exists but is not a Git repository, the script skips to avoid overwriting.
  - When `dir` is omitted, it’s derived from the last path component of the URL (with optional `.git` removed), e.g., `.../voice.git` → `voice`.

- Default working directory resolution:
  - Prefers `/home/strato-space`, then `/d/home/strato-space`, otherwise uses the current directory.

- Examples:

  ```bash
  # Git Bash or WSL
  bash d:/home/strato-space/call/repos.sh

  # Or run specific repos
  bash d:/home/strato-space/call/repos.sh && \
    repo https://github.com/strato-space/prompt && \
    repo https://github.com/strato-space/server custom-server-dir
  ```

  ```powershell
  # From PowerShell using Git Bash
  "C:\Program Files\Git\bin\bash.exe" d:/home/strato-space/call/repos.sh
  ```

The script defines a backward-compatible alias `ensure_repo()` which calls `repo "$@"`.

See also the strategy doc: 
  - `org/strato/context/01. strato stategy/process-agents.md`;
  - `prompt/plan/`

## Contributing / Testing

- Use the project virtual environment for tests:

```powershell
python -m pytest -q
python -m pytest -q app/tests/test_cli_prompts_and_exec.py::test_cli_exec_tracing_403_error_json
```

- Useful env flags:
  - `CALL_DEBUG=1` — verbose debug logs to console
  - `CALL_FAKE_TRACING_403=1` — simulate a 403 error envelope for `exec`/`call` integration tests

## AgentFab (Group Agent) — Quick Start

- Invocation (chat/shell):

  ```text
  @AgentName "user-input"

  @AiNewsAggr "Собери новости про Уральские Авиалинии, темы три блока: ценообразование, бортпроводники, ИТ"
  ```
  ```text
  # TODO 
  @AgentFab "input-sample: create|update agnet @AgentName with goai: goal-text" 
  ```

  [ ] @AgentFab shoud inpockes all agents in @AgentFab on-by one as described in `prompt/AgentFab/agents.yaml` `agents` attribute

  - `<input>` may be a free-form string (treated as goal or task), contains file/URL/voice-bot-url to an agent scaffold, or a YAML-like or json-like block.
- MCP method (planned/minimal): `call(agentName, input)`
- Resolvers used by Call:
  - `agentName` → `AgentNameObj`
  - `input` (string) → `inputObj`
  - `output` (string) → `outputObj`
- Discovery order (creator → execution):
  - `agent/AgentFab/<AgentName>/agent.yaml`
  - `agent/UxFab/<AgentName>/agent.yaml`
- Output artifacts (runnable): `agent/UxFab/<AgentName>/agent.yaml` (+ `prompts/*.yaml`, optional `tests/*`).
- Reference: `agent/AgentFab/project.yaml`.

- AgentFab card fields:
  - `agents`: map of `AgentName: goal` (names only, without `@`). This replaces the old list format.
  - Note: `agent_goals` is deprecated; goals are now embedded in the `agents` mapping.

- Agent card fields:
  - `prompts`: map of `PromptName: goal` (names only, without `@`). This replaces the old table/list format.
  - Example:

    ```yaml
    id: DialogSummary
    name: "🕶 A / DialogSummary"
    prompts:
      InterviewSummary: "Сгенерировать summary: цели, задачи, ограничения, критерии успеха"
    ```

## Typical Use Cases

- Invoke a named agent pipeline on a payload and route the result to a file/Google Sheet/Telegram.
- Chain multiple agents and prompts by addressing the next destination as `@Agent`.
- Run org-specific ai-team pipelines by prefixing with org name (e.g., `@Ural...`).

## CLI (planned minimal interface)

```bash
call "@UralAiNewsAggr:DailyDigest" --payload "text: ..." --out "file-path:reports/daily.md"
```

- `--payload`: one of the supported payload descriptors.
- `--out`: output destination descriptor.
- `--trace`: print and persist the chain id and step-level logs.

Reference implementation targets Python first, Node.js second.

## HTTP API (MVP)

- POST `/invoke`: { address, payload, out, options }
- GET `/status/{traceId}`: state, step logs, errors
- GET `/logs/{traceId}`: full chain log

Planned parity in Python (FastAPI) and Node.js (Express/Koa). File-system resolvers first (local Prompt Repo clone), optional GitHub resolver next.

## Configuration

- Agent/Prompt Repo locations: local FS paths (required), optional GitHub resolvers.
- Org registry (YAML): `orgs/{org}.yaml` for per-org defaults, secrets resolvers, allowlists.
- MCP allowlist per prompt: enforce which MCPs a prompt is allowed to call.
- Cost hints & token stats: optional; emitted as metrics when configured.

Example `config.yaml` (illustrative):

```yaml
promptRepo:
  path: ../prompt
  resolver: fs

orgs:
  default: Strato
  registry: ./orgs

logging:
  dir: ./logs
  level: info

security:
  mcpAllowlist: [fs, gsh, rag, seq]
```

### Environment variables (current Python runtime)

The module `call.app.call` expects the following environment variables (can be provided via `.env`):

- `TELEGRAM_TOKEN` — default Telegram Bot token (used when no bot name is selected)
- `TELEGRAM_CHAT_ID` — primary chat id (int). If a 10‑digit id is provided, it will be normalized to `-100XXXXXXXXXX` internally.
- `TELEGRAM_SECOND_CHAT_ID` — secondary chat id (int)
- `TELEGRAM_THREAD_ID` — optional thread id (int)
- `TELEGRAPH_TOKEN` — Telegra.ph access token
- `OPENAI_API_KEY` — API key for the LLM
- `PROMPT_REPO` — absolute path to the Prompt repository (holds `draft/` and `ready/` prompts); if not set, discovery tries sibling `../prompt` (see `discover_prompt_repo()` in `call/lib/discovery.py`).
- `AGENT_REPO` — absolute path to the Agent repository (holds `projects.yaml` and `agent/<Project>/<Agent>/agent.yaml`); if not set, discovery tries sibling `../agent` (see `discover_agent_repo()` in `call/lib/discovery.py`).

Notes:
- On startup, `call.app.call` will copy `../.env` into local `.env` if `.env` is missing.
- All mandatory envs are sanitized by `ensure_env()`; missing required ones will raise.

### Telegram tokens (project_name only)

> 2025-09-13: Token resolution was simplified. We now use a single function `get_project_token(project_name)` in `call/app/call.py` and a single bot factory `init_bot(project_name=...)`. There are no fallbacks or env mutations.

Configure `.env` with project-scoped keys (no `Bot` suffix):

```
# Project-scoped tokens
TELEGRAM_TOKEN.StratoSpaceAi=111111:AAAAAA
TELEGRAM_TOKEN.AgentFab=222222:BBBBBB
```

How selection works:

- Telegram runner: `python -m call.telegram_bot.bot --bot-name StratoSpaceAiBot`
  - The runner derives `project_name = StratoSpaceAi` by stripping common suffixes (`Bot`, `_bot`, `-bot`).
  - It retrieves the token via `get_project_token(project_name)` and starts polling with that token.
- Library/CLI: pass `project_name` explicitly.
  - CLI: `python -m call.cli.main call --project-name AgentFab @AgentFab "hello"`
  - Library: `call.lib.api.call(name, input, project_name="AgentFab")`

There is no fallback to `TELEGRAM_TOKEN` and no suffix guessing during token lookup. If `TELEGRAM_TOKEN.<project_name>` is missing, an error will be raised.

Start the bot with a specific identity:

```
python -m call.telegram_bot.bot --bot-name StratoSpaceAiBot
```

Notes:
- Only the dot notation `TELEGRAM_TOKEN.Name` is supported for named bots.

### Telegram formatting and routing (Updated Sep 19, 2025)

- KISS policy: the Telegram bot does not validate agent names. It forwards the name exactly as typed to `call.lib.api.call_async`, which performs discovery/validation and returns a structured error when an agent is unknown.
  - The bot now passes the parsed token as `target` so the library decides whether it is a prompt, agent, or project (precedence: prompt > agent > project; supports `*`).

- Formatting:
  - HTML mode is used for all rich messages. Sanitization is centralized in `call/app/utils/html_sanitizer.py` (via `telegram_prepare_html()`).
  - Only Bot API–supported tags/attrs are emitted (headers mapped to bold + newline; lists flattened; spoilers, `tg-emoji`, code/pre, blockquotes preserved).
  - Welcome banner spacing: one blank line after the header and one blank line between the user input preview and the attributes block (`mcp`, `vs`, `model`).
- Routing precedence:
  - Incoming Telegram update chat/thread id (passed to `call.lib.api.call(chat_id=..., thread_id=...)`) → highest priority.
  - Agent YAML `output.tg.chat_id/thread_id` → used only if no explicit chat/thread was provided.
  - `.env` defaults (`TELEGRAM_CHAT_ID`, `TELEGRAM_THREAD_ID`) → last fallback.
  - The pipeline passes explicit chat/thread through the final notification to avoid races with globals.

- Optional thread id:
  - `TELEGRAM_THREAD_ID` is optional. If not set or set to `0`, it is treated as `None` and no thread id is sent to Telegram.
  - The library will also automatically retry without a thread id when Telegram returns `BadRequest: Message thread not found` (see `safe_send_message()` wrapper).

#### Context extraction from message text (New Sep 22, 2025)

- The bot builds a structured JSON payload when a `/call` command (or a plain message handled as a call) is sent as a reply, and also parses inline tokens from the main text:
  - Tokens beginning with `@` are normalized by stripping the leading `@` and punctuation.
  - For each token, the bot attempts to resolve it as a prompt/agent/project via `call.lib.api.build_runnable_instructions_config()`.
  - When a token resolves to a prompt with a known file path, the bot adds a context item of the form:
    - `{ "type": "file", "name": "<PromptId>.md", "path": ".../prompt/<state>/<PromptId>.md", "content": "...", "mutable": true }`
- Example:
  - `/call @AgentFab @3-OnlineChunkSummarization` → payload includes a `file` context item for `3-OnlineChunkSummarization.md` so downstream pipelines can consume it directly.

Payload JSON field order (predictable):
- `target`
- `replay` (optional)
- `input` (optional)
- `context` (optional)

### Prompt discovery and target resolution (Updated Sep 19, 2025)

- Target precedence: `prompt > agent > project`.
- Prompt discovery is live and file-system based — new files in `prompt/draft/` or `prompt/ready/` are discoverable immediately without rebuilding indices.
- Prompt resolution is strictly scoped to the specified project for security reasons.
- Ambiguity and not found:
  - Multiple prompts matched your criteria → `code: "TOO_MANY_ROWS"` with `options`.
  - Not found (including wildcard) → `code: "NO_DATA_FOUND"` with a descriptive error message.

### Max turns configuration (Updated Sep 19, 2025)

- The agents runner library defaults to `DEFAULT_MAX_TURNS=10`. Call increases the defaults to avoid premature termination of long runs:
  - Environment: `AGENTS_DEFAULT_MAX_TURNS` (default `150`).
  - At import time, `call.app.call` sets `agents.run.DEFAULT_MAX_TURNS` from this env variable.
  - The main run in `build_and_run_agent` uses `max_turns=_agents_run.DEFAULT_MAX_TURNS` (was a fixed 150 earlier).
  - Set `AGENTS_DEFAULT_MAX_TURNS=300` in `.env` if you need longer sessions.

### Digest notification helper (Updated Sep 12, 2025)

Function: `call.app.call.send_digest_notification(*, text: str | None = None, chat_id: int | None = None, message_thread_id: int | None = None, agent_name: str | None = None, agent_path: str | Path | None = None, input_text: str | None = None, image_path: str | Path | None = None) -> telegram.Message | None`

Behavior and arguments:

- `text` — optional message body. If empty/whitespace, falls back to a minimal banner and echoes `input_text` in a `<code>` block. If `text` is ≥4000 chars, content is published to Telegraph and a link banner is sent instead.
- `chat_id`, `message_thread_id` — explicit routing targets. If omitted, fall back to module-level `selected_chat_id/selected_thread_id` which are initialized from `.env` and can be overridden by the Telegram bot/lib.
- `agent_name` — used for presentation (e.g., Telegraph title) and for resolving optional button macros.
- `agent_path` — path to the agent’s `agent.yaml`. If present, the helper loads its `buttons` section and builds inline buttons. Macro `{{digest_url}}` is substituted with the Telegraph link when one was generated.
- `input_text` — original user input. Used in the fallback banner to provide context.
- `image_path` — if provided, a photo is sent with `text` as the caption (sanitized and truncated to 1024 chars) instead of a plain text message.

Notes:

- The helper prints concise debug lines: `[DEBUG] send_digest_notification args: ...` and `[DEBUG] send_digest_notification publish_url=...`.
- Empty/whitespace-only `text` is normalized to `None` to avoid Telegram errors.

### Bot reply payload (New Sep 19, 2025)

When the `/call` command or a plain text message is sent as a reply, the bot constructs a structured payload and passes it as the input string to the pipeline. The payload shape is:

```json
{
  "target": "@Name or PromptId",   // only when the first token in the message starts with '@'
  "input": "main text",            // falls back to reply text when main text is empty
  "context": [                       // optional, present when replying to a message
    { "type": "text", "text": "<reply text>" },
    { "type": "text", "url": "https://api.telegram.org/file/bot<token>/<path>" }
  ],
  "replay": "<reply text>" | [ ... ] // convenience field mirroring reply content
}
```

Notes:
- Replied documents are resolved to direct Telegram file URLs via `get_file(file_id)`.
- The bot logs the built payload with `debug_print` under the tag `[bot] [PAYLOAD]` (gated by `CALL_DEBUG`).

### Python dependencies

Install Python deps from `app/requirements.txt`:

```bash
uv venv && source .venv/bin/activate
uv pip install -r app/requirements.txt
# or: pip install -r app/requirements.txt
```

### Testing

- Run unit tests (requires `pytest` in your environment):

```bash
pytest -q call/app/tests/test_send_digest_notification.py
```

- What’s covered:
  - Empty `text` fallback to banner with input echo
  - Long `text` publishing to Telegraph and link injection
  - `{{digest_url}}` macro substitution in `buttons` loaded from `agent.yaml`

## Execution Model (MVP)

1. Parse address: `@[Org][Agent][:Pipeline][:Prompt]` → normalized identifiers.
2. Resolve agent/prompt from Prompt Repo / ai-team.
3. Build execution plan (may be implicit for simple invocations).
4. Execute with human-in-the-loop affordances (optional) and step logging.
5. Route output to destination and return `traceId`.

## Developer Setup

Prerequisites:
- Python 3.11+ (Phase I) or Node.js 20+ (Phase II)
- Git, GitHub CLI (`gh`) for repo ops

Suggested Python skeleton:

```bash
uv venv && source .venv/bin/activate
uv add fastapi uvicorn pydantic pyyaml
uv add loguru
```

Run (dev):

```bash
uvicorn call.app:app --reload --port 8088
```

Directory layout (proposed):

```text
call/
  README.md
  config.yaml           # optional local config
  orgs/                 # YAML registry per org (optional)
  logs/                 # trace logs
  app/
    __init__.py
    api.py              # FastAPI endpoints (/invoke, /status, /logs)
    service.py          # core execution engine
    resolvers/
      prompt_repo.py    # FS/GitHub resolvers
      output.py         # file, gsheet, telegram, @agent
    models.py           # pydantic models (Address, Payload, Destination)
    mcp/
      ...               # (optional) MCP clients / adapters
```

## Examples

- Minimal invocation (defaults to `Strato`):
  - Address: `@DiscoveryAgent`
  - Payload: `text: "Summarize the requirements doc"`
  - Out: `file-path: outputs/summary.md`

- Org-specific pipeline to Telegram thread:
  - Address: `@UralAiNewsAggr:DailyDigest`
  - Payload: `url: https://news.example.com/feed`
  - Out: `telegram-chat-id: 123456 | telegram-thread-id: 42`

## Roadmap

- v0.1
  - Python FastAPI MVP: /invoke, /status, /logs; FS resolvers
  - Trace logging with unique id; replay convenience
  - Basic address parser and normalizer
- v0.2
  - GitHub prompt resolver; Git-based version pinning
  - Output adapters: Google Sheets, Telegram
  - Metrics endpoint (Prometheus)
- v0.3
  - Node.js parity (Express + TypeScript)
  - MCP server: call (invoke/status/logs)
  - AuthZ model for powerful agent invocation

## Security Notes

- Enforce per-prompt MCP allowlist.
- Restrict powerful pipelines to authorized org users.
- Validate payload sources (URLs, sheets) and sanitize outputs.

## License

Proprietary — Strato-Space. Internal use within the organization unless explicitly permitted.
