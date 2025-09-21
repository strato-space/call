# Card formats (MD-only)

This repository enforces a strict Markdown-only format for cards. YAML files are not used at runtime.

- Project card (optional): `agent/<Project>/project.md`
- Agent card (required per agent): `agent/<Project>/<Agent>/agent.md`
- Prompt cards: `prompt/ready/<Prompt>.md` and `prompt/draft/<Prompt>.md`

All cards use fenced sections:

- `<!-- METADATA:START -->` … ```yaml … ``` … `<!-- METADATA:END -->` — required YAML block
- `<!-- PROMPT:START -->` … text … `<!-- PROMPT:END -->` — optional body text for the agent/prompt

## METADATA schema (recommended)

Minimum keys for prompts:

- `version: v1`
- `id: <PromptId>`
- `title: <Human title>`
- `project: <ProjectName>`
- `agent: <AgentName>`
- `tags: []` (optional)
- `engine:` (optional; example: `openai`)
- `orchestration:` (optional; example: `llm`)

Agent cards should include:

- `id:` or `name:` (used as agent name)
- `aliases: []` (optional)
- `prompts:` — list or map of prompt ids used by this agent (e.g., `[DailyDigest, 33-Questioning]`)
- Any runtime knobs (model, temperature, top_p, provider, tg, io, memory, chain, …)

PROMPT section contains only the text sent to the LLM. Keep runtime configuration in METADATA.

## Strictness

- Runtime API only reads paths from `call/repo.db` and accepts Markdown cards.
- If a prompt card is selected but its METADATA block is missing/malformed, CLI `--print-instructions` returns a 400 BAD_CARD_FORMAT envelope.
- Scanner logs warnings for `.md` prompts missing METADATA and still indexes them by filename; runtime paths remain strict.
