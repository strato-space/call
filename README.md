# Call

A minimal, extensible subsystem for invoking AI agents and prompt pipelines by name and routing inputs/outputs across your project ecosystem.

Call provides a unified invocation syntax, consistent logging, and pluggable backends to run agents and prompts defined in Prompt Repository and ai-team. It is designed to be simple first, then grow into a full orchestration layer.

> Recent changes: see `CHANGELOG.md`.

### CLI Quickstart

Use the project virtual environment interpreter for consistency.

```powershell
.venv\Scripts\python.exe -m call.cli.main list --project UxFab
.venv\Scripts\python.exe -m call.cli.main call --project UxFab --agent DialogPostAnalysis --prompt 33-Questioning --print-instructions
.venv\Scripts\python.exe -m call.cli.main exec --project UxFab --agent DialogPostAnalysis --content-item "https://docs.google.com/document/d/FILE_ID/edit"
```

- Exit codes: `0` on success (`ok:true`), `1` on error envelopes (`ok:false`).
  - PowerShell: check `$LASTEXITCODE`
  - cmd.exe: check `%ERRORLEVEL%`

## Key Concepts

### Simplified Agent Discovery (Updated Sep 17, 2025)

- **Directory-based lookup**: Agents are discovered by directory name only under `agent/<Project>/`.
- **Case-sensitive matching (KISS)**: No normalization. Use the exact agent names and aliases as defined in YAML and directories.
- **No registry scanning**: Removed complex metadata matching and registry file processing.
- **Simple syntax**: `@AgentName` or just `AgentName`.

### Agent Loading Logic

- **Zero prompts**: Uses `agent.yaml` content directly as instructions
- **With prompts**: Uses first prompt from `prompts` list, merges with agent metadata
- **Prompt loading**: Extracts first word from prompts list, tries `.md` then `.yaml` extensions
- **Recursive file listing**: All agent directory files added to seed history as filenames list

## Library return shape and errors (Updated Sep 17, 2025)

`call.lib.api.call(name: str, input: str, *, chat_id: int | None = None, thread_id: int | None = None, echo: bool = False) -> dict`

- On success returns a dict:

```json
{
  "ok": true,
  "agent": "AgentName",
  "agent_path": ".../agent.yaml",
  "final_output": "...",
  "echo": false
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
    "echo": false
  }
  ```

  Standard codes: `NO_DATA_FOUND`, `TOO_MANY_ROWS`, `INTERNAL_ERROR`, `PIPELINE_ERROR`. Certain upstream errors are mapped, for example Tracing 403 → `error_code: 403`, `code: "REQUEST_FORBIDDEN"`, and `details` with provider payload when available.

Also available (async) — supports empty agent name:

`await call.lib.api.call_async(name: str | None, input_text: str, *, chat_id: int | None = None, thread_id: int | None = None, echo: bool = False) -> dict`

- If `name` is empty/None: discovery is skipped, an Agent with empty instructions is constructed, and only `input_text` is used. In this case, `agent_path` in the response is `null`.
- If `name` is provided and not found: returns a 404-style error envelope.

Notes:
- The Voice integration now imports and uses this library directly (no subprocess). See below.
- If you need an error-envelope style response, wrap the call and convert exceptions into a `{ ok:false, ... }` JSON at your boundary (e.g., HTTP layer).

### Voice integration via library

- `voice/src/lib/core.py` (`VoicebotClient.call`) now calls `call.lib.api.call` directly.
- The Voice CLI passes through `echo`:
  - `echo=False`: Voice returns plain text to its caller.
  - `echo=True`: Voice returns the full dict payload (suitable for JSON HTTP responses).

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

  - Tool name: `agents`
  - Args: `query?: string`, `include_aliases?: boolean`, `project_name?: string`

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
  # List projects, agents, prompts (hierarchical JSON)
  python -m call.cli.main list --project UxFab [--agent Agent*] [--prompt Draft]

  # Call an agent (keyword-only API). Use exact case-sensitive names.
  python -m call.cli.main call --project UxFab --agent AgentName --input "text" [--prompt PromptName] [--print-instructions] [--echo] [--trace SECONDS] [--trace-file PATH]

  # List prompts (flat). Format: table|json
  python -m call.cli.main prompts --project FanFab --format json

  # Execute with structured context (content items). Extracts Google Docs file id from URLs.
  python -m call.cli.main exec --project UxFab --agent DialogPostAnalysis \
      --content-item "https://docs.google.com/document/d/FILE_ID/edit" \
      --content-item '{"type":"text","text":"Hello"}' \
      [--output-type html] [--print-instructions]

  # Legacy positional (app module still supports):
  python -m call.app.call <AgentName> [<input>]
  ```

- **Listing Output (hierarchical)**:
  - `list()` returns an array of projects; each project has `name`, `type: "project"`, and `agents`.
  - Each agent item has `type: "agent"`, `name`, `aliases` (from agent.yaml), `prompts` (from agent.yaml), and `path`.
  - Wildcards `*` are supported in `--project`, `--agent`, and `--prompt` (case-insensitive).

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
  - PowerShell: `$env:CALL_FAKE_TRACING_403=1; .venv\Scripts\python.exe -m call.cli.main exec --agent DialogPostAnalysis`
  - cmd.exe: `set CALL_FAKE_TRACING_403=1 && .venv\Scripts\python.exe -m call.cli.main exec --agent DialogPostAnalysis`
- Text error conversion: if the pipeline returns `final_output` starting with `"Error:"`, the library converts it to a structured error envelope
  (e.g., `error_code: 502`, `code: UPSTREAM_CONNECT_ERROR|PIPELINE_ERROR`) to avoid printing tracebacks to users.

#### Checking exit codes (Windows)

```powershell
# PowerShell
.venv\Scripts\python.exe -m call.cli.main exec --agent DialogPostAnalysis; echo $LASTEXITCODE

# cmd.exe
cmd /c ".venv\\Scripts\\python.exe -m call.cli.main exec --agent DialogPostAnalysis & echo %ERRORLEVEL%"
```

### Projects-aware discovery (Updated Sep 17, 2025)

- **Project index**
  - The canonical list of projects is defined in `agent/projects.yaml` (Agent repo).
  - Each project corresponds to a subdirectory under the agent repo, for example: `agent/UxFab/`.
  - Loader is centralized: `call.lib.discovery.load_projects_index()` reads `agent/projects.yaml` once and is used by the API and discovery paths.

- **Project manifest: project.yaml**
  - Each project may define a `project.yaml` manifest in `agent/<Project>/project.yaml`.
  - Preferred flattened structure:

    ```yaml
    name: UxFab
    aliases: [UxFab]
    agents:
      AiNewsAggr:
        desc: "..."
        aliases: ["AI News", "ai-news-aggr"]
        prompts: [DailyDigest, ...]
      DialogSummary: "Сгенерировать summary ..."  # short form allowed
    ```
  - Backward compatibility: a nested `project:` block is also supported by the library.

- **Agent location**
  - Agents live under their project directory: `agent/<Project>/<Agent>/agent.yaml`.
  - Example: `agent/UxFab/AiNewsAggr/agent.yaml`.

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
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest -q app/tests/test_cli_prompts_and_exec.py::test_cli_exec_tracing_403_error_json
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

### Telegram formatting and routing (Updated Sep 13, 2025)

- KISS policy: the Telegram bot does not validate agent names. It forwards the name exactly as typed to `call.lib.api.call_async`, which performs discovery/validation and returns a structured error when an agent is unknown.

- Formatting:
  - HTML mode is used for all rich messages. Sanitization is centralized in `call/app/utils/html_sanitizer.py` (via `telegram_prepare_html()`).
  - Only Bot API–supported tags/attrs are emitted (headers mapped to bold + newline; lists flattened; spoilers, `tg-emoji`, code/pre, blockquotes preserved).
  - Welcome banner spacing: one blank line after the header and one blank line between the user input preview and the attributes block (`mcp`, `vs`, `model`).
- Routing precedence:
  - Incoming Telegram update chat/thread id (passed to `call.lib.api.call(chat_id=..., thread_id=...)`) → highest priority.
  - Agent YAML `output.tg.chat_id/thread_id` → used only if no explicit chat/thread was provided.
  - `.env` defaults (`TELEGRAM_CHAT_ID`, `TELEGRAM_THREAD_ID`) → last fallback.
  - The pipeline passes explicit chat/thread through the final notification to avoid races with globals.

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
