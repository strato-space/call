# Call

A minimal, extensible subsystem for invoking AI agents and prompt pipelines by name and routing inputs/outputs across your project ecosystem.

Call provides a unified invocation syntax, consistent logging, and pluggable backends to run agents and prompts defined in Prompt Repository and ai-team. It is designed to be simple first, then grow into a full orchestration layer.

## Key Concepts

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

- Agent Fab — a factory of early analytical prompts and agent cards.
- Prompt Repository — canonical storage of prompts, schemas, tests, and metadata.
- ai-team — organization-specific sets of agents with active execution environment.
- RAG and MCP servers — optional data access and tool affordances.
- Voice Bot, AI News Aggregator, Telegram, Google Sheets/Pages — integration touchpoints.

See also the strategy doc: `org/strato/context/01. strato stategy/process-agents.md`.

## Typical Use Cases

- Invoke a named agent pipeline on a payload and route the result to a file/Google Sheet/Telegram.
- Chain multiple agents/prompts by addressing the next destination as `@Agent`.
- Run org-specific ai-team pipelines by prefixing with org name (e.g., `@Ural...`).

## CLI (planned minimal interface)

```
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

```
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
