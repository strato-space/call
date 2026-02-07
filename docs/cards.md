# Card Formats (Markdown)

Cards are Markdown files discovered/indexed into `.cache/call/repo.db` and then executed by the runtime.

- Project card (optional): `agent/<Project>/project.md`
- Agent card (required per agent): `agent/<Project>/<Agent>/agent.md`
- Prompt cards: `prompt/ready/<Prompt>.md` and `prompt/draft/<Prompt>.md`

## Supported syntaxes

1. AgentCard-style YAML frontmatter (preferred for portability)

```md
---
type: agent
name: Demo
engine: fast-agent
agents:
  - HelperAgent
servers:
  - time
instruction: "Do the thing."
---
```

Notes:
- If the body after frontmatter is non-empty, it becomes the prompt/instructions.
- If the body is empty, `instruction` (or `instructions`) is used as the prompt/instructions.

2. Legacy fenced blocks (kept for backward compatibility)

````md
<!-- METADATA:START -->
```yaml
id: Demo
engine: openai-agents
```
<!-- METADATA:END -->

<!-- PROMPT:START -->
Hello from PROMPT block
<!-- PROMPT:END -->
````

3. YAML-only cards (metadata only; no prompt)

```yaml
name: PlainYAML
model: gpt-5
```

## METADATA schema (recommended)

Prompts may include the following keys as needed (all optional but highly recommended for discoverability):

- `version: v1`
- `id: <PromptId>`
- `title: <Human title>`
- `project: <ProjectName>`
- `agent: <AgentName>`
- `tags: []`
- `engine:` (examples: `fast-agent`, `openai-agents`)
- `orchestration:` (example: `llm`, `workflow`)
- `agents:` (list of sub-agent names/ids)

Agent cards should include:

- `id:` or `name:` (used as agent name)
- `aliases: []` (optional)
- `prompts:` — list or map of prompt ids used by this agent (e.g., `[DailyDigest, 33-Questioning]`)
- Any runtime knobs (model, temperature, top_p, provider, tg, io, memory, chain, …)

PROMPT section contains only the text sent to the LLM. Keep runtime configuration in METADATA.

## Strictness

- Runtime API only reads paths from `.cache/call/repo.db` and accepts Markdown cards.
- If a prompt card is selected but its METADATA block is missing/malformed, CLI `--print-instructions` returns a 400 BAD_CARD_FORMAT envelope.
- Scanner logs warnings for `.md` prompts missing METADATA and still indexes them by filename; runtime paths remain strict.

## Engines

- Default engine: `fast-agent` (override via `CALL_DEFAULT_ENGINE`).
- Per-card override: set `engine:` in frontmatter or legacy METADATA.
- History is owned by `call` and stored in SQLite (`CALL_DB` override), scoped by `(conversation_id, agent_name)`.

`fast-agent` runs via stateless `spawn_detached_instance` clones. For execution, `call` generates temporary fast-agent AgentCards under `.cache/call/agentcards/` and injects a `call_agent` tool for mixed-mode composition.

## Direct access helpers

- `call.lib.api.read(card_id)` returns the stored Markdown exactly as written to the `card` column in `repo.db`.
- `call.lib.api.write(card_id, card_text)` updates the database record first and then rewrites the filesystem path recorded in the row.
- CLI (`call read`, `call write`) emit or consume plain text on success (JSON envelopes only appear on stderr when something fails).
- The Actions API exposes `/read/{id}` (GET, `text/plain`) and `/write/{id}` (POST, `text/plain` body) so that updates propagate immediately without running the filesystem scanner.
- MCP tools `read` and `write` reuse the same helpers and return plain text when successful, falling back to JSON error envelopes for exceptional cases.
