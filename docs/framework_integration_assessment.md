# Framework selection for Call integration

## 1. What Call already provides

- **Unified launch contract.** The public API (REST, MCP, CLI) accepts a single JSON payload with project/agent/prompt and passes it to the runtime without transformations. This simplifies external runner integration as long as they can return the final text and, optionally, a richer envelope. 【F:call/README.md†L41-L116】
- **SQLite index over file repositories.** All projects, agents, and prompts are aggregated into `.cache/call/repo.db`, minimizing filesystem access and providing an extension point for alternative engines. 【F:call/README.md†L97-L132】【F:call/lib/repo_db.py†L1-L186】
- **Configurable `RunnableConfig`.** Call builds a compact DTO from YAML/Markdown cards with instructions, MCP servers, and tools. Any external framework must accept this set and return a response. 【F:call/lib/api.py†L456-L560】
- **Current runner based on the `agents` package.** The main function in `src/call/app/call.py` constructs an `Agent` from instructions and connects MCP/tools. This is the primary swap-out point for another runtime. 【F:src/call/app/call.py†L1-L135】

## 2. What exists in adjacent repositories

- **Agent repo** — a tree of cards with strict naming, aliases, and sections (`goal`, `constraints`, `prompts`) that rely on Markdown `METADATA`. This must be respected when translating to an external framework format. 【F:agent/README.md†L3-L124】
- **Card examples** show agents describing multi-step flows, file contracts, and expected artifacts. The runtime must support sequences of actions, not just single requests. 【F:agent/UxFab/UxCreator/agent.md†L1-L200】
- **Prompt repo** stores individual prompts referenced from agent cards. An external framework must support nested steps/roles. 【F:prompt/ready/33-Questioning.md†L1-L64】

## 3. Integration requirements

1. **Ingest structured agent descriptions.** The framework must parse Markdown/YAML with multi-step chains and a list of auxiliary prompts.
2. **Fine-grained model configuration.** `RunnableConfig` passes `model`, `model_settings`, and MCP tools, so the framework must support arbitrary OpenAI models with parameters and tool control. 【F:call/lib/api.py†L456-L487】
3. **Compatibility with events and logs.** We should preserve the current wrapper (`call.lib.repo_db.push_event`, Telegram bot, Actions) — so the runner must be async and return results in the existing envelope format. 【F:call/lib/repo_db.py†L128-L186】【F:call/README.md†L171-L197】
4. **Minimal card changes.** Agent/Prompt repos evolve independently with strict rules, so translation to another framework should be automatic without manual edits. 【F:agent/README.md†L78-L105】

## 4. MetaGPT vs SuperAGI

### MetaGPT
- Pros: Python-first, small core, easy to wrap around existing `RunnableConfig`. It can build multi-agent flows from role descriptions and supports custom tools, so integration can be an adapter that converts Markdown agents into MetaGPT roles and runs them in order.
- Cons: no built-in MCP support; we would need a bridge for file passing and external services.

### SuperAGI
- Pros: packaged infrastructure with UI, tasks, and long-term memory.
- Cons: rigid expectations around DB/workers, its own data model and lifecycle (triggers, toolkits). Embedding it in Call would require translating the entire SQLite index into SuperAGI’s format and maintaining their API on top of existing CLI/Telegram flows.

**Conclusion:** MetaGPT is easier to integrate into the current Call architecture. It is closer in scope to the existing `agents` runner, avoids rebuilding the index, and enables a quick adapter from `RunnableConfig` to MetaGPT roles. SuperAGI only makes sense if its orchestration/UI is required; for a light runtime replacement it is overkill.

## 5. Additional options

- **LangGraph (LangChain)** — flexible action graph, easy to build from a prompt list, supports tools; a viable alternative if LangChain is already in use.
- **CrewAI** — role/task oriented, JSON contract close to `RunnableConfig`, but we would need MCP support or drop it.

## 6. Practical steps

1. Add an adapter layer (`call/lib/runners/metagpt.py`) that creates MetaGPT roles/tasks from `RunnableConfig` and prompts.
2. Extend `call.lib.api.build_runnable_instructions_config` with a small `engine=metagpt` flag to select the runner without breaking compatibility with the `agents` package. 【F:call/lib/api.py†L456-L560】
3. Keep the existing REST/MCP layer and repository index intact so Telegram and CLI continue to work. 【F:call/README.md†L57-L132】
4. Add module tests under `src/call/app/tests` to verify that the new runner returns the envelope and supports tools/model settings.

This strategy lets us gradually replace the `agents` package with MetaGPT (or another lightweight framework) without breaking adjacent repos or existing client integrations.
