# Call — AI Agent Runtime & Orchestration

> **A production-grade subsystem for discovering, invoking, and orchestrating AI agents and prompt pipelines across your organization.**

Call is a minimal, extensible runtime that provides unified invocation syntax, consistent logging, and pluggable backends to execute agents and prompts defined in your repositories. It bridges multiple interfaces (CLI, REST API, MCP, Telegram) with a single source of truth.

**Key capabilities:**

- 🎯 **Unified Discovery** — SQLite-based index (`repo.db`) for projects, agents, and prompts with wildcard filtering
- 🔄 **Multiple Interfaces** — CLI, REST API (Actions), MCP server, Telegram bot with consistent behavior
- 📝 **Metadata-Driven** — Markdown-based prompt format with YAML metadata for model settings, orchestration hints
- 🔗 **Smart Routing** — Telegram integration with session tracking, reply context, and inline token resolution
- 🛡️ **Structured Errors** — Consistent error envelopes across all surfaces with detailed diagnostics
- 🧪 **Battle-Tested** — 153+ tests covering API, CLI, bot handlers, payload builders, and model settings precedence

---

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture Overview](#architecture-overview)
- [Installation & Setup](#installation--setup)
- [Core Concepts](#core-concepts)
- [Using Call](#using-call)
  - [Command Line Interface](#command-line-interface)
  - [REST API (Actions)](#rest-api-actions)
  - [MCP Server](#mcp-server)
  - [Telegram Bot](#telegram-bot)
  - [Python Library](#python-library)
- [Configuration](#configuration)
- Features
  - [YAML Formatting](docs/formatting.md) — Readable YAML output for MCP hooks and agent-as-tools
  - [MCP Message Cleanup](docs/mcp-message-cleanup.md) — Auto-delete intermediate debug messages in Telegram
  - [MCP Hook Routing](docs/mcp-hook-routing.md) — Dual-channel message routing for debug and user messages
  - [Specialized Bots](docs/specialized-bots.md) — Natural language interaction with project-specific bots
  - [Project-Level Prompts](docs/project-level-prompts.md) — Prompts attached directly to projects without agents
  - [MCP Config](docs/mcp_config.md) — MCP server configuration and setup
  - [Cards](docs/cards.md) — Agent and prompt card system
  - [DB Diagnostics](docs/DB_DIAGNOSTICS.md) — Database diagnostics tool for troubleshooting
- [Developer Guide](#developer-guide)
- [Reference](#reference)
- [Changelog](#changelog)
- [Security & Best Practices](#security--best-practices)
- [Roadmap](#roadmap)

---

## Quick Start

```bash
# List available agents and prompts
python -m call.cli.main list --project UxFab --format yaml

# Execute an agent with input
python -m call.cli.main call --target DialogPostAnalysis --input "Analyze user feedback"

# Pure GPT call without instructions (uses LLM_MODEL env, default: gpt-5)
python -m call.cli.main call --input "What's the current date and time?"

# Reload repository index
python -m call.cli.main reload --repos agent,prompt

# List prompts with filters
python -m call.cli.main prompts --project * --state ready --format table
```

---

## Architecture Overview

### System Components

```text
┌─────────────────────────────────────────────────────────────┐
│                      Call Subsystem                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐  ┌──────────┐   ┌──────────┐   ┌──────────┐   │
│  │   CLI    │  │   REST   │   │   MCP    │   │ Telegram │   │
│  │          │  │  Actions │   │  Server  │   │   Bot    │   │
│  └────┬─────┘  └────┬─────┘   └────┬─────┘   └────┬─────┘   │
│       │             │              │              │         │
│       └─────────────┴──────────────┴──────────────┘         │
│                          │                                  │
│                   ┌──────▼──────┐                           │
│                   │  call.lib   │                           │
│                   │     API     │                           │
│                   └──────┬──────┘                           │
│                          │                                  │
│       ┌──────────────────┼──────────────────┐               │
│       │                  │                  │               │
│  ┌────▼────┐      ┌──────▼──────┐     ┌─────▼────┐          │
│  │ repo_db │      │  discovery  │     │ app.call │          │
│  │ (index) │      │             │     │ (runtime)│          │
│  └─────────┘      └─────────────┘     └──────────┘          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
         │                    │                   │
    ┌────▼────┐          ┌────▼────┐         ┌────▼────┐
    │ repo.db │          │  agent/ │         │ prompt/ │
    │ (SQLite)│          │  (repo) │         │ (repo)  │
    └─────────┘          └─────────┘         └─────────┘
```

### Module Organization

- **`lib/`** — Core library exposing `api.py` (public interface), `repo_db.py` (SQLite queries), `repo_fs.py` (filesystem scanning), `discovery.py` (agent/prompt resolution), `logging.py`, `utils.py`
- **`app/`** — Application runtime (`call.py`) containing `build_and_run_agent()`, welcome banner logic, MCP integration, agents-as-tools wrappers
- **`cli/`** — Command-line interface (`main.py`) for interactive usage and scripting
- **`actions/`** — FastAPI REST API with bearer auth, OpenAPI schema generation
- **`mcp/`** — Model Context Protocol server (FastMCP SDK) exposing tools: `call`, `exec`, `agents`, `prompts`, `models`, `read`, `write`, `reload`
- **`telegram_bot/`** — Production Telegram bot with message parsing, reply context extraction, inline buttons
- **`docs/`** — Additional documentation (`cards.md`, `mcp_config.md`, integration assessments)
- **`tools/`** — Helper scripts (`repos.sh` for workspace synchronization)

#### Subsystem Quick Reference

- **`actions/`** publishes `actions/openapi.json` and mirrors `call.lib.api` helpers (`call`, `list`, `models`, etc.). Patch the schema whenever endpoints change so client generation stays accurate.
- **`mcp/`** exposes the same surface as REST (`call`, `exec`, `notify`, `reload`, `models`) via presets in `mcp_config.yaml` / `mcp_config.json`. Keep tool signatures aligned with the payload contract.
- **`telegram_bot/`** fronts the runtime with `/agents`, `/prompts`, `/call`, parsed replies, and renders structured envelopes. Preserve HTML-safe output, welcome banners, and debug logging flows when adjusting handlers.
- **`wallet/`** stores deployment-time secrets such as `service-account-key.json`. Only commit placeholders.
- **`windsurf/`** centralizes IDE defaults; update it in lockstep with formatter/linter changes.
- **`requirements.txt`** pins runtime dependencies for CLI, bot, Actions API, and MCP server.
- **Claude Desktop configs**: when editing `claude_desktop_config.json`, include only servers with `enabled: true` in `mcp_config.yaml` and set filesystem catalog roots to `c:/home/strato-space` on Windows.

### Data Flow

1. **Discovery** — Scanner (`repo_fs`) indexes Markdown cards from `agent/` and `prompt/` repos into `repo.db`
2. **Resolution** — Selectors (`project`, `agent`, `prompt`, `target`) resolve to a `RunnableConfig` with instructions, model settings, metadata
3. **Execution** — Runtime (`app.call.build_and_run_agent`) constructs an OpenAI Agents pipeline, applies model settings precedence (prompt > agent > project), invokes tools/MCPs
4. **Routing** — Results delivered via Telegram (with session tracking), returned as structured JSON, or written to filesystem

---

## Session ID — derivation and override

- Format: `chat` or `chat:thread` (agent name is not part of the session id).
- When `session_id` is provided to `call()/call_async()` (or to Actions/MCP/CLI), it takes precedence and is used to derive Telegram routing:
  - The library parses `chat_id`/`thread_id` from the provided `session_id`.
  - Environment defaults are NOT used in this case.
- When `session_id` is not provided:
  - If `chat_id`/`thread_id` args are provided, they are used (with env defaults filling missing pieces).
  - If neither is provided, no session is created and no Telegram messages are sent. The response omits `session_id`.
- On success and on error, when a session is known, responses include `session_id`.

### Error payload schema (Updated)

- All library responses use a consistent envelope:

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

- Field order is stable (`ok`, `error`, `error_code`, `description`, ...). Consumers should read from the `error` object for structured diagnostics and use `description` for the primary message.
- `provider_code` carries the upstream provider identifier (when present). The legacy top-level `code` field has been removed; existing integrations should read numeric statuses from `error.code` instead.
- When no structured payload is available, `error` is omitted and `description` falls back to the raw string. The CLI mirrors this envelope in all formats (`json`, `yaml`, `text`).

### Exec payload contract

- The single JSON payload accepted by Actions and MCP is:
  - `{ project?: string, agent?: string, prompt?: string, target?: string, context?: any, echo?: boolean, session_id?: string }`
  - Exactly one of `project|agent|prompt|target` must be provided.
  - The full payload JSON is used as the input string for the agent pipeline.
  - `echo` defaults to `false`. When omitted the runtime now returns the final text only; set `echo=true` explicitly to receive the full envelope.

---

## Installation & Setup

### Prerequisites

- **Python 3.11+** with virtual environment support
- **Git** for repository management
- **Environment variables** configured in `.env` (see [Configuration](#configuration))

### Installation

```bash
# Clone the repository
git clone https://github.com/strato-space/call.git
cd call

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys and paths
```

### Initial Setup

```bash
# Reload repository index (scans agent/ and prompt/ directories)
python -m call.cli.main reload --repos agent,prompt

# Verify installation
python -m call.cli.main models --format yaml
python -m call.cli.main list --project * --format text
```

### Workspace Sync

Use `tools/repos.sh` to synchronize all Strato repositories:

```bash
# Clone or update all core repos (call, agent, prompt, server, rms, voice)
./tools/repos.sh

# Install Python dependencies
./tools/repos.sh --pip

# Install MCP servers (requires npm/uv)
./tools/repos.sh --mcp

# Codex preset (provisions /workspace/.venv)
./tools/repos.sh --codex
```

> **See also:** `CHANGELOG.md` for recent changes, `AGENTS.md` for contributor guidelines.

#### Flags

- **`--pip`** ensures `.venv` exists, upgrades `pip`, and installs Python requirements for `call`, `voice`, and `server/mcp` using the workspace interpreter.
- **`--mcp`** installs `uv` when missing and provisions JavaScript MCP servers (`@modelcontextprotocol/server-sequential-thinking`, `@modelcontextprotocol/server-filesystem`) via `npm`.
- **`--codex`** clones/fetches sibling repos into `/workspace/` and builds a shared `/workspace/.venv` preset tailored for Codex sandboxes.

---

## Core Concepts

### Projects, Agents, and Prompts

**Projects** are organizational units that contain related agents and prompts:

- Defined by `agent/<Project>/project.md` with METADATA including model defaults, tg routing
- Examples: `UxFab`, `AgentFab`, `FanFab`

**Agents** are executable workflows composed of prompts and tools:

- Located at `agent/<Project>/<Agent>/agent.md`
- METADATA includes: `id`, `name`, `aliases`, `prompts` (list of prompt ids), model settings, orchestration hints
- Example: `agent/UxFab/DialogPostAnalysis/agent.md`
- Discovery honors directory names exactly (KISS). No registry or alias expansion is performed beyond what cards declare, so use the on-disk casing.
- Agents can run with zero prompts (pure YAML instructions) or the first prompt listed in `prompts`; prompt metadata wins over agent/project metadata when merged.

**Prompts** are reusable instruction templates:

- Located in `prompt/ready/` (production) or `prompt/draft/` (development)
- Markdown format with YAML METADATA block and optional PROMPT section
- METADATA includes: `id`, `title`, `project`, `agent`, `model`, `engine`, `orchestration`
- Example: `prompt/ready/33-Questioning.md`

### Target Resolution & Precedence

When you specify a `target`, Call resolves it with this precedence:

1. **Project** — matches top-level project names (most secure, cannot be overridden)
2. **Agent** — searches `agent/<Project>/<Agent>/` directories  
3. **Prompt** — checks `prompt/ready/` and `prompt/draft/` for matching id/name

**Security rationale**: This priority ensures prompts cannot hijack project or agent names, maintaining hierarchy integrity.

**Executable vs Non-Executable Projects**:
- **Executable projects** (with `<!-- PROMPT:START -->` section) run directly
- **Non-executable projects** (metadata only) require an agent to execute

**Wildcards** (`*`) are supported in all selectors for flexible filtering.

**Forms supported:**

- Plain names (`DialogPostAnalysis`) or wildcard fragments (`33-*`).
- Path-like notation (`path:Project/Agent/Prompt`) for precise scoping.
- Tokens are case-sensitive; ambiguity returns a `TOO_MANY_ROWS` error envelope with options to disambiguate.

**Resolution tips:**

- Exact project matches win before agent fuzzy matches when the name equals a project.
- Use unified `target` when unsure about type; explicit `project/agent/prompt` flags keep scope narrow and skip wildcard broadening.
- For debugging resolution issues, use the [DB Diagnostics Tool](docs/DB_DIAGNOSTICS.md)

### Model Settings Precedence

Model configuration cascades with clear precedence:

```text
prompt > agent > project > LLM_MODEL (env)
```

This allows:

- Project-level defaults for all agents
- Agent-level overrides for specific workflows
- Prompt-level overrides for fine-grained control
- Runtime overrides via `--model` flag or `model` in payload

**Keys supported in METADATA:**

- `model`: model identifier (e.g., `gpt-5`, `gpt-4.1`)
- `model-settings`: generic settings for all models
- `model-settings-<model>`: model-specific settings (recommended)

**Settings fields:**

- `temperature`, `top_p`, `frequency_penalty`, `presence_penalty`, `max_tokens`
- `verbosity`: `low|medium|high`
- `reasoning`: `{ effort: minimal|low|medium|high, summary?: auto|concise|detailed }`

### Repo Index (SQLite)

Call maintains a single-source-of-truth index in `call/repo.db`:

**Schema:**

```sql
CREATE TABLE repo (
    target   TEXT PRIMARY KEY,
    project  TEXT,
    agent    TEXT,
    prompt   TEXT,
    path     TEXT,
    state    TEXT,      -- 'draft' or 'ready'
    engine   TEXT,      -- 'openai', 'openai-agents', etc.
    orchestration TEXT, -- 'llm', 'handoff', 'langgraph', etc.
    type     TEXT,      -- 'project', 'agent', 'prompt'
    rel_path TEXT,
    url      TEXT,
    goal     TEXT,
    card     TEXT       -- full card contents
)
```

**Operations:**

- `reload()` — rescans `agent/` and `prompt/` repos, rebuilds index
- `read(card_id)` — returns raw card text from DB
- `write(card_id, text)` — updates DB and filesystem atomically
- `list()` — hierarchical listing (projects → agents → prompts)
- `list_prompts()` — flat prompt listing with filters

**Details:**

- `target` stores the bare identifier (`project`, `agent`, or `prompt`) without prefixes; prompt and agent lookups share the same pool to enable wildcard resolution.
- `state` is inferred from path (`draft` if the file path contains `draft`, else `ready`).
- `engine` and `orchestration` are pulled from card METADATA when present so table queries can highlight runtime hints.
- `call.lib.api.read()` / `write()` operate against the DB first and then propagate to disk, mirroring CLI `call read` / `call write` commands.

### Engineering Principles

> **Follow the engineering principles documented in [`AGENTS.md`](AGENTS.md).** That file remains the canonical source for contributor expectations and elaborates on KISS, SOLID/DI, helper sizing, explicit failure paths, and observability.

---

## Using Call

### Command Line Interface

The CLI provides a complete interface for discovery, execution, and management.

**Common Commands:**

```bash
# Discovery & Listing
call list [--project <name>] [--agent <name>] [--format json|yaml|text]
call agents [--project <name>] [--format text|json|yaml]
call prompts [--project <name>] [--agent <name>] [--state ready|draft] [--format table|json|yaml]
call models [--format yaml]

# Execution
call call --target <name> --input "<text>" [--model <model>] [--session-id <id>]
call exec --project <p> --agent <a> [--content-item <item>] [--model <model>]

# Management
call reload [--repos agent,prompt] [--format yaml]
call read <card_id>
call write <card_id> --card "<content>"

# Debugging
call call --target <name> --input "<text>" --echo         # Preview payload
call call --target <name> --print-instructions             # Show instructions
call call --target <name> --trace 5 --trace-file trace.txt # Stack dumps
```

**Selection Modes:**

1. **Keyword-based (`call`)** — explicit selectors

   ```bash
   python -m call.cli.main call --project UxFab --agent DialogPostAnalysis --input "text"
   ```

2. **Unified target (`--target`)** — resolved via precedence (prompt > agent > project)

   ```bash
   python -m call.cli.main call --target DialogPostAnalysis --input "text"
   python -m call.cli.main call --target "33-*" --input "text"  # Wildcard
   ```

3. **Payload-based (`exec`)** — JSON payload with context items

   ```bash
   python -m call.cli.main exec --project UxFab --agent DialogPostAnalysis \
     --content-item "https://docs.google.com/document/d/FILE_ID/edit" \
     --content-item '{"type":"text","text":"Hello"}'
   ```

4. **Pure GPT (no selectors)** — input-only mode

   ```bash
   python -m call.cli.main call --input "What time is it?"
   ```

**Input Modes:**

- `--input "text"` — raw text, no parsing
- `--parse-input "text @Token"` — Telegram-style parsing with token resolution; tokens (including wildcards like `@31-*`) resolve against the repo index and add context items automatically.
- `--download-context` — inline file contents in payload

**Wildcard tokens:**

- Each `@pattern` with `*` expands against the repo index, adding file references for the first match.
- Multiple tokens are deduplicated; add `--echo` to inspect the payload preview without execution.

**Output Control:**

- `--echo` — preview payload without execution
- `--print-instructions` — show resolved instructions
- `--print-card` — display full card (metadata + prompt)
- `--format json|yaml|text|table` — output format
- `--model <model>` — override effective model

**Examples:**

```bash
# List all ready prompts in UxFab project
python -m call.cli.main prompts --project UxFab --state ready --format table

# Execute with wildcards and echo
python -m call.cli.main call --target "31-*" --parse-input "@50-* review" --echo

# Direct card manipulation
python -m call.cli.main read 33-Questioning
python -m call.cli.main write 33-Questioning --card "# Updated\n\nNew content"

# Session management
python -m call.cli.main clear-session --chat-id -100123 --thread-id 10
```

> **See [Command Line Interface](#command-line-interface) for more examples.**

### REST API (Actions)

Call exposes a FastAPI-based REST API with bearer authentication for external integrations.

**Base URL:** `https://call-actions.stratospace.fun`

**Authentication:** Bearer token in `Authorization` header

**Core Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/call` | GET | Execute agent with query params (`name`, `input`, `model`, `session_id`) |
| `/exec` | POST | Execute with JSON payload (see [Exec payload contract](#exec-payload-contract)) |
| `/agents` | GET | List agents hierarchically |
| `/prompts` | GET | List prompts with filters (`project`, `agent`, `prompt`, `state`) |
| `/models` | GET | List available OpenAI models |
| `/read/{id}` | GET | Read raw card text (returns `text/plain`) |
| `/write/{id}` | POST | Write card text (accepts `text/plain` body) |
| `/reload` | POST | Rebuild repository index |
| `/notify` | POST | Send event notification (requires `event` field) |

**Examples:**

```bash
# List prompts for a project
curl -sS "https://call-actions.stratospace.fun/prompts?project=AgentFab" \
  -H "Authorization: Bearer YOUR_TOKEN" | jq

# Execute an agent
curl -sS "https://call-actions.stratospace.fun/exec" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"agent":"DialogPostAnalysis","context":{"text":"hi"},"model":"gpt-4o-mini"}'

# Read a card
curl -sS "https://call-actions.stratospace.fun/read/33-Questioning" \
  -H "Authorization: Bearer YOUR_TOKEN"

# List available models
curl -sS "https://call-actions.stratospace.fun/models" \
  -H "Authorization: Bearer YOUR_TOKEN" | jq
```

**Model Override:**

Pass `model` as query parameter or in JSON body to override the effective model for a request:

```bash
curl -sS "https://call-actions.stratospace.fun/call?name=DialogPostAnalysis&input=hello&model=gpt-4o-mini" \
  -H "Authorization: Bearer YOUR_TOKEN" | jq
```

> **See [`actions/openapi.json`](actions/openapi.json) for complete OpenAPI specification.**

**`GET /prompts` parameters:** `project`, `agent`, `prompt` (supports `*` wildcard), and `state` (`ready|draft|""`). Use `target` when unsure about identifier type—the API resolves prompts, agents, and projects in one call.

**`POST /notify` contract:** expects only an `event` string (selectors like `project` or `agent` are ignored). Include optional payload fields inside the JSON body as needed.

### MCP Server

Call implements a Model Context Protocol server using the FastMCP SDK.

**Tools exposed:**

- `call(name, input, model?, session_id?)` — execute agent
- `exec(payload)` — execute with structured JSON payload
- `agents(query?, include_aliases?, grouped?)` — list agents
- `prompts(project?, agent?, prompt?, state?)` — list prompts
- `models()` — list available models
- `read(card_id)` — read raw card text
- `write(card_id, card_text)` — write card text
- `reload()` — rebuild repository index
- `notify(event, payload?)` — send event notification

**Configuration:**

MCP server presets are defined in `mcp_config.yaml` / `mcp_config.json` for external MCP servers (filesystem, sequential thinking, Google Sheets, etc.).

**Running:**

```bash
# Standalone (stdio mode)
python -m call.mcp.server

# Via Claude Desktop (configured in claude_desktop_config.json)
# See mcp_config.yaml for server definitions
```

**Integration:**

The runtime automatically loads external MCP servers when agents specify tools. MCP hook logging captures all tool invocations with YAML-formatted arguments and results.

> **See [`docs/mcp_config.md`](docs/mcp_config.md) for detailed MCP configuration guide.**

### Event Log

Runtime events are durably appended to `call/call.db` for observability and future event streaming:

**Operations:**
- `call.lib.repo_db.push_event(event, payload)` — insert event, return sequence id
- `call.lib.repo_db.iter_events(after_id?, limit?)` — read events in batches

**Use cases:** Replay, audit trail, metrics export, Kafka/NATS migration path

### Prompt format (MD-only)

- Prompts and cards are Markdown-only. Each file follows the Strato Prompt Framework with a `METADATA` fenced YAML block and an optional `PROMPT` block.
- The parser now tolerates cards that contain only a fenced `METADATA` block or are pure YAML files: in those cases the remaining body becomes the prompt text. Malformed YAML still raises a `BAD_CARD_FORMAT` error, and `_load_card()` logs the failure through the `call.api` logger.
- The index emits warnings for `.md` cards missing valid `METADATA`. In strict flows (e.g., CLI `--print-instructions`), malformed or missing metadata surfaces a 400 error to the caller.
- The index logs warnings for `.md` prompts missing valid `METADATA`. In strict paths (e.g., CLI `--print-instructions`), malformed or missing `METADATA` surfaces a 400 error.

#### Model settings in METADATA (Updated)

- Keys
  - `model`: the selected model id (e.g., `gpt-5`, `gpt-4.1`).
  - `model-settings`: generic settings applicable across models.
  - `model-settings-<model>`: model-specific settings. This is the recommended, canonical form.
- Runtime precedence: when a prompt, agent, and project each declare a `model`, the runtime now applies `prompt > agent > project > $LLM_MODEL` (environment default). Tests assert this ordering to prevent regressions.
- Runtime helpers: the runtime now exposes `_send_welcome_banner()` and `_embed_files_in_user_input()` so the Telegram banner logic and JSON file embedding can be unit-tested. `test_runtime_helpers.py` covers both the units and how `build_and_run_agent()` wires them up.

- Excluded (do not use in new cards)
  - `model_params`, `modelParams` (generic) and `model_params_<model>`, `modelParams<model>` (model-suffixed) are not part of the documented schema and must be avoided. Use the hyphenated forms `model-settings` and `model-settings-<model>` instead.

- Recognized fields in params
  - `temperature`, `top_p`, `frequency_penalty`, `presence_penalty`, `max_tokens`, `verbosity` (`low|medium|high`)
  - `reasoning: { effort: minimal|low|medium|high, summary?: auto|concise|detailed }`

- Example

```yaml
model: gpt-5

model-settings-gpt-5:
  reasoning:
    effort: low

model-settings-gpt-4.1:
  temperature: 0.2
  top_p: 0.9
```

---

### Telegram Bot

Call provides a production Telegram bot with intelligent message parsing and context extraction.

**Commands:**

- `/start` — welcome message
- `/agents [project]` — list agents
- `/prompts [filters]` — list prompts with filters
- `/prompts_ready`, `/prompts_draft` — state-specific listings
- `/call [@Target] <input>` — execute agent/prompt
- `/reload` — rebuild repository index
- `/clear` — clear conversation session

**Message Parsing:**

Project-specific "project bots" (also called specialized bots) follow the naming pattern `<ProjectName>Bot` (for example, `StratoProjectBot`). When you mention such a bot without providing an explicit `@Target`, it falls back to the project orchestrator (`project.md`).

**Private DMs:**

- Plain text (no `@`) → input-only execution (equivalent to `/call <input>`)
- `@Target <input>` → passed to library for resolution (priority: prompt > agent > project). Target must include the `@` prefix.
- `@ProjectNameBot [@Target] <input>` → bot name is stripped, `@Target` passed to the library. When no second `@` token is present the bot falls back to the project orchestrator.
- `@ <input>` → input-only (no target)

**Group chats:**

- Only messages that mention the bot handle explicitly are handled (either `@ProjectNameBot` for project bots or `@StratoSpaceAiBot` for the universal bot)
- `@Target <input>` → passed to library for resolution (target must start with `@`)
- `@ProjectNameBot <input>` → when no explicit `@Target` follows, the project orchestrator (`project.md`) is invoked (project bots only)
- `@ProjectNameBot @Target <input>` → `@Target` passed to library (same behavior as private chats)
- `@StratoSpaceAiBot ...` → universal bot (no default target, handles any project/agent/prompt)
- Messages without `@` are ignored

**Target Resolution:**

- Bot layer does **NOT** pre-validate targets beyond requiring the explicit `@` prefix
- All target resolution delegated to `call_api.call_async()` (prompt > agent > project hierarchy)
- Unknown targets trigger errors from library (not silently ignored by bot)
- Project scoping derives from bot name (`AgentFabBot` → `AgentFab`). When only the bot is mentioned, project bots run their project orchestrator automatically.
- `StratoSpaceAiBot` is universal: it never injects a default project. Without `@Target` it runs the LLM in "void" mode (user input only, no card instructions). With `@Target` it can execute any prompt/agent/project found in the repository.
- Enable `CALL_DEBUG=1` to trace parsing decisions in logs (`[bot]` prefix).

**Context Extraction:**

When replying to messages, the bot builds structured JSON payloads:

```json
{
 "agent": "UxResearcherReq",
 "input": "optional user message text",
 "replay": "optional replay to message text",
 "context":
  [
      {
          "type": "text",
          "text": "foo headline line.\nbar summary line.\nbaz call-to-action button description.",
          "source": {
              "type": "file",
              "file_id": "13LlOsEr6AGw6n6YX1mzrUIVUdH3xT63-",
              "name": "foo-bar-document.docx"
          }
      },
      {
          "type": "text",
          "text": "foo question about service? bar cloud offer allows foo chain registration. baz on-prem build does not include that.",
          "source": {
              "type": "session",
              "_id": "68afe646ef46aed531a8ecc5",
              "name": "foo bar voicebot session"
          }
      },
      {
          "type": "session",
          "_id": "68c7ab4cab67ffbd365062f1"
      },
      {
          "type": "file",
          "file_id": "13LlOsEr6AGw6n6YX1mzrUIVUdH3xT63-"
      }
  ]
}

```
see repo: prompt/schema/context-array.md


**Inline Token Resolution:**

Tokens like `@3-OnlineChunkSummarization` in message text are resolved to file
context items:

```json
{
  "type": "file",
  "name": "3-OnlineChunkSummarization.md",
  "path": "prompt/draft/3-OnlineChunkSummarization.md",
  "content": "...",
  "mutable": true
}
```

**Input Normalization:**

- Trailing punctuation stripped: `@220-PM-Status!` → `220-PM-Status`
- Newlines preserved in multiline input
- `--echo` flags removed without collapsing whitespace

**MCP Hook Integration:**

- All MCP tool invocations logged with YAML-formatted args/results
- Service messages sent in silent mode with expandable blockquotes
- Automatic cleanup after final result delivery
- Welcome banners use YAML format for readability

**Agents-as-Tools:**

When project cards expose helper agents via `attributes.agents`:

- Each tool invocation logged with `[Agent Tool][name]` prefix
- Input/output captured in YAML format
- Telegram banners sent best-effort when routing is active
- Tests: `test_agents_tool_wrapper.py`

**Session Tracking:**

Sessions use format `chat:thread` (no agent name prefix). Routing precedence:

1. Explicit `session_id` parameter
2. Message chat/thread ids
3. Agent YAML `output.tg.chat_id/thread_id`
4. Environment defaults (`TELEGRAM_CHAT_ID`, `TELEGRAM_THREAD_ID`)

**Configuration:**

```env
# Project-scoped tokens
TELEGRAM_TOKEN.StratoSpaceAi=111111:AAAAAA
TELEGRAM_TOKEN.AgentFab=222222:BBBBBB

# Default routing
TELEGRAM_CHAT_ID=-100123456789
TELEGRAM_THREAD_ID=10  # Optional
```

**Running:**

```bash
# Start with specific bot identity
python -m call.telegram_bot.bot --bot-name StratoSpaceAiBot

# Debug mode
CALL_DEBUG=1 python -m call.telegram_bot.bot --bot-name AgentFabBot
```

> **See [`tg-user-guide.md`](tg-user-guide.md) for detailed user documentation.**

#### Telegram Context Extraction Details

When replying to messages, the bot builds a structured payload with context:

**Context item types:**

- `{"type": "text", "text": "..."}` — plain text content from replied message
- `{"type": "text", "url": "https://api.telegram.org/file/..."}` — document
  URLs resolved via `get_file()`
- `{"type": "file", "name": "...", "path": "...", "content": "..."}` —
  inline file content
- `{"type": "session", "_id": "..."}` — session references for conversation
  threading

**Replay field:**

- Convenience mirror of reply content
- Can be string (single reply) or array (multiple context items)
- Useful for simple reply-based workflows

**Payload order guarantee:**

```json
{
  "target": "...",
  "replay": "...",
  "input": "...",
  "context": [...]
}
```

#### Max Turns Configuration

Control agent conversation length via environment variable:

```env
AGENTS_DEFAULT_MAX_TURNS=150  # Default value
```

**How it works:**

- At import, `call.app.call` sets `agents.run.DEFAULT_MAX_TURNS`
- Runtime uses this value for `Runner.run(..., max_turns=...)`
- Increase for longer conversations: `AGENTS_DEFAULT_MAX_TURNS=300`
- Default SDK value is only 10, Call raises it to 150

**When to adjust:**

- Short tasks: 50-100 turns
- Standard workflows: 150-200 turns
- Complex multi-step: 300+ turns
- Monitor logs for "max turns reached" warnings

#### Digest Notification Helper

Function: `call.app.call.send_digest_notification(**kwargs)` sends rich Telegram messages with Telegraph fallback.

**Parameters:**

- `text` (str | None) — message body; falls back to welcome banner if empty
- `chat_id` (int | None) — explicit routing target
- `message_thread_id` (int | None) — explicit thread target
- `agent_name` (str | None) — for presentation and button macro resolution
- `agent_path` (str | Path | None) — loads `buttons` section from agent YAML
- `input_text` (str | None) — original user input for fallback banner
- `image_path` (str | Path | None) — sends photo with caption instead of text message

**Behavior:**

- Text ≥4000 chars → published to Telegraph, returns link banner
- Empty text → minimal banner with `input_text` in `<code>` block
- Button macros: `{{digest_url}}` replaced with Telegraph link
- Photo mode: truncates caption to 1024 chars, uses `text` as caption
- Returns `telegram.Message | None`

**Example:**

```python
from call.app.call import send_digest_notification

result = send_digest_notification(
    text="Long analysis result...",
    chat_id=-100123,
    message_thread_id=10,
    agent_name="DataAnalyzer",
    agent_path="agent/UxFab/DataAnalyzer/agent.md",
    input_text="Analyze Q3 sales",
    image_path="charts/q3_sales.png"
)
```

### Python Library

Call exposes a clean Python API for programmatic integration.

**Import conventions:**

```python
from call.lib import api as call_api
from call.lib import repo_db as call_repo
from call.lib.logging import configure_logging as call_logging
```

**Core Functions:**

```python
# Execute agent/prompt
result = call_api.call(
    project="UxFab",
    agent="DialogPostAnalysis",
    input="Analyze user feedback",
    chat_id=-100123,
    thread_id=10,
    session_id="chat:thread",  # Optional override
    echo=False
)
# Returns: {"ok": True, "agent": "...", "final_output": "...", "session_id": "..."}

# Async execution
result = await call_api.call_async(
    target="DialogPostAnalysis",
    input="text",
    model="gpt-4o-mini"
)

# List resources
projects = call_api.list(project="UxFab")
prompts = call_api.list_prompts(project="UxFab", state="ready")
models = call_api.models()

# Card operations
card_text = call_api.read("33-Questioning")
call_api.write("33-Questioning", "# Updated\n\nNew content")

# Reload index
result = call_api.reload(repos=["agent", "prompt"])

# Build runnable config
config, error = call_api.build_runnable_instructions_config(
    project="UxFab",
    agent="DialogPostAnalysis",
    prompt="33-Questioning"
)
# Returns: (RunnableConfig, None) or (None, error_dict)
```

**RunnableConfig DTO:**

```python
@dataclass
class RunnableConfig:
    # Identifiers
    id: str                    # Prompt/agent/project id
    name: str                  # Display name
    type: str                  # "project", "agent", "prompt"
    
    # Content
    instructions: str          # Resolved instructions text
    prompt_text: str          # Raw prompt body
    card_text: str            # Full card contents
    
    # Paths
    agent_yaml_path: str      # Path to agent.md
    base_dir: str             # Base directory
    path: str                 # Full path to card
    url: str                  # GitHub URL
    
    # Execution
    model: str                # Effective model (after precedence)
    attributes: dict          # Merged metadata
    
    # Metadata
    project: str
    agent: str
    prompt: str
    goal: str
    state: str                # "ready" or "draft"
    engine: str               # "openai", "openai-agents"
    orchestration: str        # "llm", "handoff", "langgraph"
```

**Error Handling:**

All library functions return structured envelopes:

```python
# Success
{"ok": True, "final_output": "...", "agent": "...", "session_id": "..."}

# Error
{
    "ok": False,
    "error": {
        "code": 404,
        "message": "no data found",
        "type": "invalid_request_error"
    },
    "error_code": 404,
    "description": "no data found",
    "code": "NO_DATA_FOUND"  # Machine-readable code
}
```

**Standard error codes:**

- `NO_DATA_FOUND` — resource not found
- `TOO_MANY_ROWS` — ambiguous selection (includes `options` array)
- `BAD_CARD_FORMAT` — malformed METADATA YAML
- `INTERNAL_ERROR` — unexpected runtime error
- `PIPELINE_ERROR` — execution failure
- `REQUEST_FORBIDDEN` — upstream API rejection (403)

**Session Management:**

```python
# Clear sessions
result = await call_api.clear_session(
    name="DialogPostAnalysis",
    chat_id=-100123,
    thread_id=10
)
```

**Logging:**

```python
from call.lib.logging import configure_logging, debug_print

# Configure at startup
configure_logging()  # DEBUG when CALL_DEBUG=1, else INFO

# Debug printing (gated by CALL_DEBUG)
debug_print("[module]", "[tag]", "message", data)
```

**Repository Queries:**

```python
from call.lib import repo_db

# Direct DB access
projects = repo_db.find_projects(project="UxFab")
agents = repo_db.find_agents(project="UxFab", agent="Dialog*")
prompts = repo_db.find_prompts(state="ready", target="33-*")

# Event log
event_id = repo_db.push_event("session_start", {"agent": "..."})
events = repo_db.iter_events(after_id=100, limit=50)
```

---

## Configuration

Call is configured via environment variables, typically loaded from `.env` in the repository root.

### Required Variables

```env
# OpenAI API
OPENAI_API_KEY=sk-...

# Repository paths
AGENT_REPO=/path/to/agent      # Default: ../agent (sibling dir)
PROMPT_REPO=/path/to/prompt    # Default: ../prompt (sibling dir)

# Model defaults
LLM_MODEL=gpt-5                # Default model for all executions
```

### Telegram Configuration

```env
# Project-scoped bot tokens (dot notation)
TELEGRAM_TOKEN.StratoSpaceAi=111111:AAAAAA
TELEGRAM_TOKEN.AgentFab=222222:BBBBBB
TELEGRAM_TOKEN.FanFab=333333:CCCCCC

# Default routing (used when not specified in agent YAML or session_id)
TELEGRAM_CHAT_ID=-100123456789
TELEGRAM_THREAD_ID=10           # Optional, omit or set to 0 for no thread

# Telegraph integration
TELEGRAPH_TOKEN=your_telegraph_token
```

### Runtime Configuration

```env
# Repository scanning (comma/semicolon-separated)
repos=agent,prompt

# Agent execution
AGENTS_DEFAULT_MAX_TURNS=150   # Max conversation turns (default: 150)

# Google Workspace (for document/sheet integrations)
GOOGLE_SERVICE_ACCOUNT_KEY=call/wallet/service-account-key.json
```

### Logging & Debugging

```env
# Debug mode (verbose logging)
CALL_DEBUG=1

# JSON logging to stderr
CALL_LOG_JSON=1

# File logging (in addition to stderr)
CALL_LOG_FILE=logs/app.log

# Telegram live testing
TELEGRAM_LIVE=1                # Enable live Telegram send tests
TELEGRAM_LIVE_KIND=skip        # Skip Telegram tests in CI
```

### Database Paths

```env
# Override default SQLite paths
DB_PATH=call/repo.db           # Repository index (default)
EVENT_DB_PATH=call/call.db     # Event log (default)
```

### Actions API Configuration

```env
# Bearer authentication token
CALL_ACTIONS_TOKEN=your_bearer_token_here
```

### Environment Resolution

Call uses the following precedence for environment loading:

1. Operating system environment variables
2. `call/.env` (project-level config)
3. `../.env` (workspace root config)

**Startup behavior:**

- If `call/.env` is missing, Call copies `../.env` to `call/.env` on first run
- Use `override=False` to preserve OS environment variables

### Example `.env` File

```env
# Core
OPENAI_API_KEY=sk-proj-...
LLM_MODEL=gpt-5
AGENT_REPO=c:/home/strato-space/agent
PROMPT_REPO=c:/home/strato-space/prompt
repos=agent,prompt

# Telegram
TELEGRAM_TOKEN.StratoSpaceAi=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
TELEGRAM_CHAT_ID=-1001234567890
TELEGRAM_THREAD_ID=42
TELEGRAPH_TOKEN=your_token

# Runtime
AGENTS_DEFAULT_MAX_TURNS=200
CALL_DEBUG=1
CALL_LOG_FILE=logs/call.log

# Google Workspace
GOOGLE_SERVICE_ACCOUNT_KEY=call/wallet/service-account-key.json
```

### Token Resolution (Telegram)

Bot tokens use **project-name-only** notation:

```env
TELEGRAM_TOKEN.<ProjectName>=<token>
```

**How it works:**

- CLI/Library: pass `project_name="AgentFab"` explicitly
- Bot runner: derives project name by stripping suffixes (`Bot`, `_bot`, `-bot`) from `--bot-name`
  - `--bot-name StratoSpaceAiBot` → looks up `TELEGRAM_TOKEN.StratoSpaceAi`
  - `--bot-name AgentFabBot` → looks up `TELEGRAM_TOKEN.AgentFab`

**No fallbacks:** If the token key is missing, an error is raised. There is no fallback to a generic `TELEGRAM_TOKEN`.

---

## Developer Guide

### Testing

Call includes 153+ tests covering API, CLI, bot handlers, model settings precedence, and more.

**Run all tests:**

```bash
pytest
```

**Run specific test file:**

```bash
pytest app/tests/test_builder_config.py -v
```

**Run with coverage:**

```bash
pytest --cov=call --cov-report=html
```

**Environment for testing:**

```env
# Skip live Telegram tests in CI
TELEGRAM_LIVE_KIND=skip
TELEGRAM_BOT_TOKEN=""
TELEGRAM_CHAT_ID=""

# For local testing with real Telegram
TELEGRAM_LIVE=1
```

**Test organization:**

- `app/tests/` — application runtime, bot handlers, payload builders
- `cli/tests/` — CLI commands, card operations
- `actions/tests/` — REST API endpoints
- `lib/tests/` — core library functions, parsing

**Platform-specific testing:**

- **Windows**: Activate virtualenv and run `pytest` directly
- **Linux/CI**: Use `TELEGRAM_LIVE_KIND=skip TELEGRAM_BOT_TOKEN="" TELEGRAM_CHAT_ID="" pytest` to skip live Telegram tests

### Running with Local Virtual Environment

Prefer the project venv interpreter for consistency across environments:

**Windows (PowerShell):**

```bash
cd c:\home\strato-space\call
.venv\Scripts\Activate.ps1
python -m call.cli.main list --project UxFab
python -m call.cli.main call --target Vasil3 --input "рассказывай"
python -m call.cli.main call --target BusinessAnalyticAgent --input "приведи @Vasil3 в соответствие с strato space prompt framework"
```

**Linux/macOS (Bash):**

```bash
cd ~/strato-space/call
source .venv/bin/activate
python -m call.cli.main list --project UxFab
python -m call.cli.main call --target Vasil3 --input "рассказывай"
python -m call.cli.main call --target BusinessAnalyticAgent --input "приведи @Vasil3 в соответствие с strato space prompt framework"
```

**Legacy positional syntax** (still supported via `call.app.call`):

```bash
python -m call.app.call "Vasil3" "рассказывай"
python -m call.app.call "BusinessAnalyticAgent" "приведи @Vasil3 в соответствие с strato space prompt framework"
```

### Contributing

**Before submitting changes:**

1. **Run tests** — Ensure all tests pass

   ```bash
   pytest --maxfail=1 --disable-warnings
   ```

2. **Update documentation** — Update `README.md`, `CHANGELOG.md`, and inline docstrings

3. **Follow conventions** — See `AGENTS.md` for coding standards:
   - KISS principle
   - SOLID design with dependency injection
   - Small, focused helpers
   - Explicit failure paths
   - Observable errors (log everything)

4. **Commit message format** — Imperative, scope-prefixed:

   ```text
   mcp: tighten tool auth
   cli: add --download-context flag
   bot: strip trailing punctuation from agent names
   ```

5. **Update CHANGELOG** — Document user-facing changes with date header

### Project Structure Best Practices

**Adding a new CLI command:**

1. Add handler in `cli/main.py`
2. Delegate to `call.lib.api` for business logic
3. Add tests in `cli/tests/test_<feature>.py`
4. Update CLI examples in README

**Adding a new API endpoint:**

1. Define route in `actions/main.py`
2. Proxy to `call.lib.api` helpers
3. Update `actions/openapi.json` schema
4. Add tests in `actions/tests/test_<endpoint>.py`
5. Document in REST API section

**Adding a new MCP tool:**

1. Define tool in `mcp/server.py` with `@mcp.tool()` decorator
2. Delegate to `call.lib.api`
3. Keep signature aligned with REST/CLI
4. Update MCP Server section in README
5. Reuse the shared warm-up helpers (`preinitialize_mcp_servers_async()` / `preinitialize_mcp_servers_sync()`) from `call/app/call.py` when adding new entrypoints to avoid cold starts.

**Modifying card format:**

1. Update parser in `lib/utils.py`
2. Update `docs/cards.md` specification
3. Add tests for new/changed fields
4. Run full test suite to catch regressions

### Debugging Tips

**Enable verbose logging:**

```bash
$env:CALL_DEBUG=1
python -m call.cli.main call --target MyAgent --input "test"
```

**Inspect payload without execution:**

```bash
python -m call.cli.main call --target MyAgent --input "test" --echo
```

**Trace thread stacks:**

```bash
python -m call.cli.main call --target MyAgent --input "test" --trace 5 --trace-file trace.txt
```

**Check resolved instructions:**

```bash
python -m call.cli.main call --project UxFab --agent MyAgent --print-instructions
```

**View full card:**

```bash
python -m call.cli.main read MyAgent
```

**Monitor Telegram bot updates:**

```bash
# JSON logs
$env:CALL_LOG_JSON=1; $env:CALL_DEBUG=1
python -m call.telegram_bot.bot --bot-name TestBot | jq -r 'select(.logger=="call.bot") | .message'
```

---

## Reference

### CLI Command Reference

**Discovery Commands:**

| Command | Description | Example |
|---------|-------------|---------|
| `list` | Hierarchical listing of projects/agents/prompts | `call list --project UxFab --format yaml` |
| `agents` | List agents (alias for `list`) | `call agents --project * --format text` |
| `prompts` | Flat prompt listing with filters | `call prompts --state ready --format table` |
| `models` | List available OpenAI models | `call models --format yaml` |

**Execution Commands:**

| Command | Description | Example |
|---------|-------------|---------|
| `call` | Execute with keyword selectors | `call call --target Agent --input "text"` |
| `exec` | Execute with payload | `call exec --agent Agent --content-item "url"` |

**Management Commands:**

| Command | Description | Example |
|---------|-------------|---------|
| `reload` | Rebuild repository index | `call reload --repos agent,prompt` |
| `read` | Read raw card text | `call read CardId` |
| `write` | Write card text | `call write CardId --card "content"` |
| `clear-session` | Clear conversation sessions | `call clear-session --chat-id -100123` |

**Selection Flags:**

- `--project <name>` — project filter (supports `*`)
- `--agent <name>` — agent filter (supports `*`)
- `--prompt <name>` — prompt filter (supports `*`)
- `--target <name>` — unified target (resolved via precedence)
- `--state ready|draft` — prompt state filter
- Project-only selections now probe the repo index before execution: if no agents are found you will receive a `NO_DATA_FOUND` envelope, and ambiguous projects surface `TOO_MANY_ROWS` with `options`.

**Input Flags:**

- `--input "<text>"` — raw text input (no parsing)
- `--parse-input "<text>"` — Telegram-style parsing with token resolution
- `--download-context` — inline file contents in payload

**Output Flags:**

- `--echo` — preview payload without execution
- `--print-instructions` — show resolved instructions
- `--print-card` — display full card
- `--format json|yaml|text|table` — output format
- `--model <model>` — override effective model
- `--session-id <id>` — session identifier (`chat:thread`)

**Debugging Flags:**

- `--trace <seconds>` — periodic thread stack dumps
- `--trace-file <path>` — write stack dumps to file
- `--json-logs` — force JSON log output

### API Response Schema

**Success Response:**

```json
{
  "ok": true,
  "agent": "AgentName",
  "agent_path": "/path/to/agent.md",
  "final_output": "...",
  "echo": false,
  "session_id": "-100123:10"
}
```

**Error Response:**

```json
{
  "ok": false,
  "error": {
    "code": 404,
    "message": "no data found",
    "type": "invalid_request_error",
    "param": null,
    "provider_code": null
  },
  "error_code": 404,
  "description": "no data found",
  "code": "NO_DATA_FOUND",
  "agent": null,
  "project": null,
  "final_output": null,
  "echo": false
}
```

**Error Codes:**

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `NO_DATA_FOUND` | 404 | Resource not found |
| `TOO_MANY_ROWS` | 400 | Ambiguous selection (includes `options` array) |
| `BAD_CARD_FORMAT` | 400 | Malformed METADATA YAML |
| `BAD_REQUEST` | 400 | Invalid request parameters |
| `REQUEST_FORBIDDEN` | 403 | Upstream API rejection |
| `INTERNAL_ERROR` | 500 | Unexpected runtime error |
| `PIPELINE_ERROR` | 502 | Execution failure |
| `UPSTREAM_CONNECT_ERROR` | 502 | Connection to upstream service failed |

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success (`ok: true`) |
| `1` | Error envelope (`ok: false`) |

**Check exit code:**

```bash
# PowerShell
python -m call.cli.main call --target Agent --input "test"
echo $LASTEXITCODE

# cmd.exe
python -m call.cli.main call --target Agent --input "test"
echo %ERRORLEVEL%
```

### Prompt Format Reference

**Markdown Structure:**

```markdown
<!-- METADATA:START -->
```yaml
id: PromptId
title: Human Title
project: ProjectName
agent: AgentName
model: gpt-5
engine: openai
orchestration: llm

model-settings-gpt-5:
  temperature: 0.7
  reasoning:
    effort: medium
```
<!-- METADATA:END -->

<!-- PROMPT:START -->
Your instruction text here...
<!-- PROMPT:END -->

**Metadata Keys:**

| Key | Type | Description |
|-----|------|-------------|
| `id` | string | Unique identifier |
| `title` | string | Human-readable title |
| `project` | string | Project name |
| `agent` | string | Agent name |
| `model` | string | Model identifier (`gpt-5`, `gpt-4.1`) |
| `engine` | string | Runtime engine (`openai`, `openai-agents`) |
| `orchestration` | string | Control flow (`llm`, `handoff`, `langgraph`) |
| `model-settings` | object | Generic model settings |
| `model-settings-<model>` | object | Model-specific settings |
| `tags` | array | Classification tags |
| `version` | string | Card version |

**Model Settings Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `temperature` | float | Sampling temperature (0.0-2.0) |
| `top_p` | float | Nucleus sampling threshold |
| `frequency_penalty` | float | Token frequency penalty |
| `presence_penalty` | float | Token presence penalty |
| `max_tokens` | int | Maximum response length |
| `verbosity` | string | Output verbosity (`low`, `medium`, `high`) |
| `reasoning` | object | Reasoning configuration |
| `reasoning.effort` | string | Reasoning effort (`minimal`, `low`, `medium`, `high`) |
| `reasoning.summary` | string | Summary mode (`auto`, `concise`, `detailed`) |

### Call vs Exec Comparison

**`call` (keyword-based):**

- Uses explicit flags: `--project`, `--agent`, `--prompt`, `--target`
- `--input` passes raw text (no parsing)
- `--parse-input` uses Telegram parser to build JSON payload with context resolution
- `--print-instructions` shows runnable instruction body (no execution)
- `--print-card` displays full card with metadata
- `--echo` previews payload and resolved selection snapshot
- `--model` overrides effective model (highest priority)
- Project-only selections report `"agent": null` in resolved payload

**`exec` (payload-based):**

- Merges selectors and content items into single JSON payload
- Best for content buckets, Actions/MCP integrations
- Single selector mode: uses `interpret_exec_payload()` validator
- Multiple selectors: falls back to explicit call path with full payload as input
- `CALL_DEBUG=1`: auto-reloads index before payload building (picks up recent edits)
- `--echo` prints payload and exits (no execution)
- `--model` embeds override into payload for downstream callers
- `--format json|yaml|text` controls output

**When to use:**

- **`call`**: Interactive CLI usage, testing, simple invocations
- **`exec`**: Programmatic integration, content processing, MCP/Actions workflows

**Examples:**

```bash
# call with raw input
python -m call.cli.main call --target AgentFab --input "as is text" --model gpt-4o-mini

# call with parsed input (Telegram-identical payload)
python -m call.cli.main call --target AgentFab --parse-input "@3-OnlineChunkSummarization" --echo

# exec with content items
python -m call.cli.main exec --project UxFab --agent DialogPostAnalysis \
  --content-item "https://docs.google.com/document/d/FILE_ID/edit" \
  --content-item '{"type":"text","text":"Hello"}'

# exec with multiple selectors (backward-compatible fallback)
python -m call.cli.main exec --project UxFab --agent DialogPostAnalysis --target 33-Questioning --echo
```

### Wildcard Support

All selectors support `*` for pattern matching:

```bash
# Match any project
python -m call.cli.main list --project "*"

# Match prompts starting with "33-"
python -m call.cli.main prompts --prompt "33-*" --state ready

# Match agents in UxFab
python -m call.cli.main call --target "Dialog*" --input "test"

# Multiple wildcards in parsed input
python -m call.cli.main call --parse-input "@31-* @50-*" --echo
```

**Wildcard resolution:**

- Prompt lookup: `prompt/ready/` and `prompt/draft/`
- Agent lookup: `agent/<Project>/<Agent>/`
- Project lookup: top-level project directories
- Precedence: prompt > agent > project

### Logging Reference

**Log Levels:**

- `INFO` — standard operational messages
- `DEBUG` — detailed diagnostic information (enabled via `CALL_DEBUG=1`)
- `WARNING` — non-fatal issues
- `ERROR` — execution failures

**Log Formats:**

```bash
# Human-readable (default)
[2025-10-18 11:30:42] [INFO] [call.api] Loading agent: DialogPostAnalysis

# JSON (structured)
{"time":"2025-10-18T11:30:42+03:00","level":"INFO","logger":"call.api","message":"Loading agent: DialogPostAnalysis"}
```

**Module Prefixes:**

- `[app]` — application runtime, welcome banners, notifications
- `[discovery]` — repository scanning, index building
- `[bot]` — Telegram bot message parsing
- `[UPDATE]` — Telegram update summaries (in bot logs)

**Enable JSON logs:**

```bash
$env:CALL_LOG_JSON=1
python -m call.cli.main call --target Agent --input "test"
```

**File logging:**

```bash
$env:CALL_LOG_FILE="logs/app.log"
python -m call.telegram_bot.bot --bot-name TestBot
```

**Filter logs:**

```bash
# PowerShell
Get-Content -Path .\logs\app.log -Wait | Select-String -Pattern "\[app\]"

# Bash with jq
tail -F logs/app.log | jq -r 'select(.level=="INFO") | .message'
```

---

## Ecosystem & Integration

Call is part of the Strato Space AI infrastructure and integrates with:

### Core Repositories

- **[agent](https://github.com/strato-space/agent)** — Agent definitions organized by project (`agent/<Project>/<Agent>/agent.md`)
- **[prompt](https://github.com/strato-space/prompt)** — Reusable prompt templates (`prompt/ready/`, `prompt/draft/`)
- **[call](https://github.com/strato-space/call)** — This repository (runtime & orchestration)
- **[server](https://github.com/strato-space/server)** — MCP starters, nginx configs
- **[voice](https://github.com/strato-space/voice)** — Voice bot backend (lib, MCP, Actions, CLI)
- **[rms](https://github.com/strato-space/rms)** — Sample customer project repository

### External Integrations

- **OpenAI** — LLM inference via official Python SDK
- **Telegram** — Bot API for user interactions and notifications
- **Telegraph** — Long-form content publishing (via `telegraph` package)
- **Google Workspace** — Document/Sheet access via service account
- **MCP Servers** — Filesystem, sequential thinking, time, Google Sheets

### Related Documentation

- [`CHANGELOG.md`](CHANGELOG.md) — Detailed version history
- [`AGENTS.md`](AGENTS.md) — Contributor guidelines and coding standards
- [`docs/cards.md`](docs/cards.md) — Prompt card format specification
- [`docs/mcp_config.md`](docs/mcp_config.md) — MCP configuration guide
- [`tg-user-guide.md`](tg-user-guide.md) — Telegram bot user manual
- [`actions/openapi.json`](actions/openapi.json) — REST API specification

### Use Cases

- **Agent Orchestration** — Execute multi-step AI workflows with tool calling
- **Prompt Management** — Version-controlled prompt library with metadata
- **Telegram Integration** — Natural language interface for agent invocation
- **Content Processing** — Document analysis, summarization, transformation
- **Multi-Project Support** — Isolated workspaces per project/team
- **Session Tracking** — Persistent conversation state across channels
- **Observability** — Structured logging, event streams, audit trails

---

## Security & Best Practices

### Security Considerations

- **MCP Allowlist** — Enforce per-prompt MCP allowlist to restrict tool access
- **API Authentication** — All REST API endpoints require bearer token authentication
- **Credentials Management** — Store sensitive credentials in `wallet/` directory, never commit to version control
- **Input Validation** — Validate payload sources (URLs, sheets) and sanitize outputs
- **Access Control** — Restrict powerful pipelines to authorized organization users
- **Session Security** — Session IDs do not include agent names for privacy
- **Error Sanitization** — Structured errors prevent information leakage via stack traces

### Best Practices

**Prompt Design:**

- Keep METADATA and PROMPT sections separated
- Use model-specific settings (`model-settings-<model>`) for fine-grained control
- Document prompt purpose in `title` and `goal` metadata fields
- Version prompts with `version` field for tracking changes

**Agent Organization:**

- One agent per directory under `agent/<Project>/<Agent>/`
- Use `project.md` for project-wide defaults
- Leverage prompt precedence: prompt > agent > project
- Keep agent directories organized with related prompts

**Development Workflow:**

- Use `draft/` for development, `ready/` for production prompts
- Test with `--echo` flag before execution
- Monitor logs with `CALL_DEBUG=1` during development
- Run full test suite before committing changes

**Production Deployment:**

- Use environment-specific `.env` files
- Configure `CALL_LOG_FILE` for persistent logging
- Set `AGENTS_DEFAULT_MAX_TURNS` appropriately for workload
- Monitor `call.db` and `repo.db` size, implement rotation if needed

---

## Roadmap

### Current Status (v1.0)

✅ SQLite-based repository index  
✅ Multi-interface support (CLI, REST, MCP, Telegram)  
✅ Metadata-driven execution with model precedence  
✅ Session tracking and routing  
✅ Comprehensive test coverage (153+ tests)  

### Planned Features

#### v1.1 — Enhanced Observability

- Metrics endpoint (Prometheus-compatible)
- Trace logging with unique chain IDs
- Event streaming to Kafka/NATS
- Real-time execution dashboard

#### v1.2 — Extended Integrations

- GitHub prompt resolver with version pinning
- Additional output adapters (Slack, Discord, Email)
- Google Workspace enhanced integration (Docs, Slides)
- S3/Azure Blob storage for artifacts

#### v1.3 — Advanced Orchestration

- Human-in-the-loop workflows
- Conditional branching and error handling
- Agent chaining syntax: `Agent1 --> Agent2 --> Output`
- Cost tracking and token usage analytics

#### v1.4 — Multi-Language Support

- Node.js runtime parity (Express + TypeScript)
- Go client library
- Language-agnostic MCP protocol extensions

#### Future Considerations

- Fine-grained authorization model (RBAC)
- Prompt marketplace and sharing
- A/B testing framework for prompts
- Distributed execution across regions

---

## License

Proprietary — Strato-Space. Internal use within the organization unless explicitly permitted.

---

## Summary

Call provides a production-ready runtime for AI agent orchestration with:

✅ **Unified discovery** via SQLite index (`repo.db`)  
✅ **Multiple interfaces** (CLI, REST API, MCP, Telegram)  
✅ **Metadata-driven execution** with model settings precedence  
✅ **Smart routing** with session tracking  
✅ **Structured errors** across all surfaces  
✅ **Battle-tested** with 153+ tests

**Get started:**

```bash
# Clone and setup
git clone https://github.com/strato-space/call.git
cd call
python -m venv .venv && .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys

# Initialize
python -m call.cli.main reload --repos agent,prompt

# Execute
python -m call.cli.main call --target MyAgent --input "Hello world"
```

For questions, issues, or contributions, see [`AGENTS.md`](AGENTS.md) for guidelines.

---

## Changelog

### 2025-10-23: Target Resolution Priority Change

**Breaking Change**: Target resolution priority changed from `prompt → agent → project` to `project → agent → prompt`.

**Key Changes**:
- ✅ Projects now have highest priority (security improvement)
- ✅ Executable projects (with PROMPT section) can run directly
- ✅ Database path fixed in `.env` (`.cache/call/repo.db`)
- ✅ New diagnostic tool: `debug_db.py`
- ✅ 6 new tests for target resolution

**Details**: See [CHANGELOG_TARGET_RESOLUTION.md](CHANGELOG_TARGET_RESOLUTION.md)

**Migration**: Use explicit selectors if you have duplicate target names:
```bash
# Force prompt selection
call exec --prompt MyTarget

# Force project selection (now default)
call exec --project MyTarget
```
