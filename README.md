# Call

A minimal, extensible subsystem for invoking AI agents and prompt pipelines by name and routing inputs/outputs across your project ecosystem.

Call provides a unified invocation syntax, consistent logging, and pluggable backends to run agents and prompts defined in Prompt Repository and ai-team. It is designed to be simple first, then grow into a full orchestration layer.

## Key Concepts

### Simplified Agent Discovery (Updated Aug 31, 2025)

- **Directory-based lookup**: Agents are discovered by directory name only under `prompt/AgentFab/` and `prompt/agents/` (AgentFab takes precedence)
- **Case-insensitive matching**: Agent names are normalized to PascalCase but matched case-insensitively
- **No registry scanning**: Removed complex metadata matching and registry file processing
- **Simple syntax**: `@AgentName` or just `AgentName`

### Agent Loading Logic

- **Zero prompts**: Uses `agent.yaml` content directly as instructions
- **With prompts**: Uses first prompt from `prompts` list, merges with agent metadata
- **Prompt loading**: Extracts first word from prompts list, tries `.md` then `.yaml` extensions
- **Recursive file listing**: All agent directory files added to seed history as filenames list

## Error Reporting (Updated Sep 6, 2025)

The library now follows a Telegram Bot API–style error envelope for operational failures. Instead of raising, `call.lib.api.call()` returns:

```json
{
  "ok": false,
  "error_code": 404,
  "description": "Agent 'AgetnFab' not found",
  "error_type": "ValueError",
  "agent": "AgetnFab",
  "final_output": null,
  "echo": false
}
```

- Missing/unknown agent → `error_code: 404`.
- Internal pipeline error → `error_code: 500` with a descriptive message.
- Additional context fields (`error_type`, `agent`, `echo`) are included for clients.

Debug details (optional): when running from CLI or for troubleshooting, you can include file/line/stack in the error body.

- Enable one of the following:
  - Pass `debug=True` to `call.lib.api.call(name, input, debug=True)`
  - Or set env var `CALL_DEBUG=1` (accepted values: 1/true/yes/on)

Example with debug:

```json
{
  "ok": false,
  "error_code": 500,
  "description": "...",
  "error_type": "RuntimeError",
  "agent": "AgentName",
  "final_output": null,
  "echo": false,
  "debug": {
    "file": "d:/home/strato-space/call/app/call.py",
    "line": 1103,
    "stack": [
      {"file": "d:/home/strato-space/call/app/call.py", "line": 1103, "function": "run_digest_pipeline", "code": "dto = AgentDTO.from_yaml_file(path_obj)"},
      {"file": "d:/home/strato-space/call/app/call.py", "line": 1539, "function": "<module>", "code": "asyncio.run(main(...))"}
    ]
  }
}
```

When the library runs successfully, it returns:

```json
{
  "ok": true,
  "agent": "AgentName",
  "agent_path": ".../agent.yaml",
  "final_output": "...",
  "echo": false
}
```

### FastAPI voice/actions integration

`voice/src/actions/main.py` inspects the returned dict from the call library. If `{ ok: false }`, it maps `error_code` to the HTTP status and returns the body as-is, so clients receive a proper HTTP status (e.g., 404/500) with a Telegram-style error JSON.

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

### Legacy Agent Addressing (Deprecated)

- Agent addressing syntax (canonical): `@[OrgName][AgentName][:PipelineName][:PromptName]`
  - All parts are optional; inside Call, names are normalized to PascalCase.
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

- Agent Fab — a factory of early analytical agnets cards and there prompts stored in repo `prompt` directory `AgentFab`.
- Prompt Repository — canonical storage of prompts, schemas, tests, and metadata stored in repo `prompt` directory `agents`.
- RAG and MCP servers — optional data access and tool affordances:
  - Filesystem: root /home/strato-space, main repos: prompt [prompt repository], call [this repo], server [mcp's starter, nginx cofings], rms [sample of project repo], voice []   
- Voice Bot, AI News Aggregator, Telegram, Google Sheets/Pages — integration touchpoints.

- Repos list:
  - prompt - prompt repository;
  - call - this repo;
  - server mcp's starter, nginx cofings 
  - rms sample custromer's of project repo, 
  - voice - voicebot backed lib, mcp, actions, cli interfaces   

See also the strategy doc: 
 - `org/strato/context/01. strato stategy/process-agents.md`;
 - `prompt/plan/`

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
  - `prompt/AgentFab/<AgentName>.yaml`
  - `prompt/AgentFab/<AgentName>/agent.yaml`
  - `prompt/agents/<AgentName>/agent.yaml`
- Output artifacts (runnable): `prompt/agents/<AgentName>/agent.yaml` (+ `prompts/*.yaml`, optional `tests/*`).
- Reference: `prompt/AgentFab/agent.yaml` and .

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
- Chain multiple agents/prompts by addressing the next destination as `@Agent`.
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

- Prompt Repo location: local FS path (required), optional GitHub resolver.
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

- `TELEGRAM_TOKEN` — Telegram Bot token
- `TELEGRAM_CHAT_ID` — primary chat id (int). If a 10‑digit id is provided, it will be normalized to `-100XXXXXXXXXX` internally.
- `TELEGRAM_SECOND_CHAT_ID` — secondary chat id (int)
- `TELEGRAM_THREAD_ID` — optional thread id (int)
- `TELEGRAPH_TOKEN` — Telegra.ph access token
- `OPENAI_API_KEY` — API key for the LLM
- `PROMPT_REPO` — absolute path to the Prompt repository; if not set, discovery tries sibling `../prompt` (see `discover_prompt_repo()` in `call/app/call.py`).

Notes:
- On startup, `call.app.call` will copy `../.env` into local `.env` if `.env` is missing.
- All mandatory envs are sanitized by `ensure_env()`; missing required ones will raise.

### Python dependencies

Install Python deps from `app/requirements.txt`:

```bash
uv venv && source .venv/bin/activate
uv pip install -r app/requirements.txt
# or: pip install -r app/requirements.txt
```

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
