# Call vs Fast-Agent - Deep Comparison for Strato Goals

This document compares two repositories against the goals stated in:
- /home/strato-space/ai/org/strato/context/01. strato stategy/process-agents.md

Scope and evidence:
- call: README.md, docs/cards.md, docs/project-level-prompts.md, docs/mcp_config.md, docs/mcp_sse_timeouts.md, app/call.py
- fast-agent: README.md, docs/ACP_TOOL_CALLS.md, fastagent.config.yaml, src/fast_agent/agents/agent_types.py, src/fast_agent/core/agent_card_loader.py, src/fast_agent/core/direct_decorators.py, src/fast_agent/core/logging/logger.py, src/fast_agent/llm/model_factory.py, src/fast_agent/llm/provider/openai/llm_openai.py
- I did not run code or review every implementation detail. This is a doc-and-code comparison for the items flagged as Unclear.

## Executive summary

call is the best fit as the primary repository for the project's stated goals (org-level runtime, prompt repo, multi-surface invocation, MCP/Telegram/REST). fast-agent is a strong agent engine and MCP/ACP toolkit but does not cover the org-level orchestration layer without significant additional infrastructure. call relies on OpenAI Agents SDK/Responses API (vendor lock tradeoff), while fast-agent supports both OpenAI Chat Completions and Responses via its OpenAI provider. Recommended decision: adopt call as the core runtime; use fast-agent selectively as a backend engine where its MCP/ACP capabilities add value.

## Project goals (from process-agents.md)

Summarized target capabilities:
- Unified invocation syntax and routing for agents/pipelines
- Prompt repository with versioning and metadata
- Multi-org / multi-project support
- Multiple surfaces: CLI, REST, MCP, Telegram
- Logging and traceability for runs
- MCP integration and allowlisting
- Human-in-the-loop and repeatable pipelines

## High-level positioning

- call: An organization-level runtime and orchestrator that discovers prompts/agents in repos, exposes a unified call interface across CLI/REST/MCP/Telegram, and standardizes logging and delivery. This matches the call subsystem described in the strategy document.
- fast-agent: A general agent framework for composing and running agents and workflows, with strong MCP/ACP features and multi-provider support. It is closer to an agent engine than an org-level orchestration layer.

## Comparison by axis

### 1) Scope and boundary
- call: Orchestration layer for org-level prompt/agent repositories. Centralizes discovery, invocation, and delivery.
- fast-agent: Agent application framework. Focused on composing agents, tools, and MCP servers.

### 2) Prompt repository model
- call: Strict markdown card format, ready/draft states, and SQLite indexing (repo.db). Project/agent/prompt hierarchy with metadata blocks. Supports project-level prompts.
  - Evidence: call/docs/cards.md, call/docs/project-level-prompts.md
- fast-agent: Supports AgentCards in Markdown/YAML via an agent card loader, but no org-level repository index or project/agent/prompt hierarchy.
  - Evidence: fast-agent/src/fast_agent/core/agent_card_loader.py, fastagent.config.yaml

### 3) Discovery and selection
- call: Discovery and filtering by project/agent/prompt and a single resolution path to runnable config.
- fast-agent: No org-level discovery; selection is by config or code for an application.

### 4) Invocation surfaces
- call: CLI, REST (Actions), MCP server, Telegram bot, and consistent payload semantics.
  - Evidence: call/README.md
- fast-agent: CLI + MCP usage through its runtime. No built-in Telegram or REST service surfaces.
  - Evidence: fast-agent/README.md

### 5) Multi-project / multi-org support
- call: Project cards and project-level prompts, plus repo index. Designed to scale across projects.
  - Evidence: call/docs/project-level-prompts.md
- fast-agent: Focused on a single agent app or workflow per config. Multi-project scaling is external.

### 6) Governance and access control
- call: Per-prompt MCP allowlist is documented for tool access control.
  - Evidence: call/README.md (Security section)
- fast-agent: Per-agent server/tool/resource lists in AgentConfig + ACP tool permission handlers (not prompt-level allowlists).
  - Evidence: fast-agent/src/fast_agent/core/direct_decorators.py, fast-agent/docs/ACP_TOOL_CALLS.md

### 7) Observability and traceability
- call: Standard error envelopes, session handling, Telegram debug formatting, and logs. Emphasis on unified output across channels.
  - Evidence: call/README.md, call/docs/formatting.md
- fast-agent: Structured logging subsystem (event bus + listeners) plus ACP tool-call progress notifications.
  - Evidence: fast-agent/src/fast_agent/core/logging/logger.py, fast-agent/docs/ACP_TOOL_CALLS.md

### 8) Agents-as-tools and orchestration
- call: Supports agents-as-tools with additional debug formatting and Telegram routing.
- fast-agent: Native agents-as-tools pattern and parallelization features.

### 9) Workflow types
- call: No built-in workflow types; orchestration is expressed via prompts or agents-as-tools.
- fast-agent: Explicit workflow types (chain, parallel, router, orchestrator, iterative_planner, evaluator_optimizer, maker).
  - Evidence: fast-agent/src/fast_agent/agents/agent_types.py, fast-agent/src/fast_agent/core/direct_decorators.py

### 10) Provider and model coverage
- call: Focused on runtime behavior and orchestration; provider coverage is implied by the runtime and prompt metadata.
- fast-agent: Explicit multi-provider support (OpenAI, Anthropic, Google, Azure, etc.) documented in README.

### 11) OpenAI API strategy (Agents SDK vs Chat Completions, Responses API)
- call: Uses OpenAI Agents SDK in runtime, with documented Responses API behavior and constraints (vendor lock tradeoff vs higher-level features).
  - Evidence: call/app/call.py, call/docs/mcp_sse_timeouts.md
- fast-agent: Supports both OpenAI Chat Completions and Responses in the OpenAI provider; provider-agnostic architecture reduces lock-in.
  - Evidence: fast-agent/src/fast_agent/llm/provider/openai/llm_openai.py, fast-agent/src/fast_agent/llm/model_factory.py

### 12) Fit to the strategy document
- call: Matches the described Call subsystem, prompt repo, unified invocation, MCP, Telegram, and org-level routing.
- fast-agent: Useful as an internal engine for building agents or for MCP/ACP features, but does not replace the call layer.

## Requirement coverage matrix (from process-agents.md)

Decision format: Go, Conditional Go, No-Go

| Option | Decision | Fit to goals | Gaps | Risk | Effort to reach parity | Rationale |
| --- | --- | --- | --- | --- | --- | --- |
| call | Go | High | Low | Low | Low | Already implements the call subsystem surface area (repo index, prompt cards, CLI/REST/MCP/Telegram, logging) described in the strategy doc. |
| fast-agent | Conditional Go | Medium | Medium | Medium | High | Strong agent engine and MCP/ACP features, but lacks org-level prompt repo, discovery, Telegram/REST surfaces, and unified invocation semantics. |

### Coverage details (supporting notes)

Legend: Yes, No, Higher, Lower, Unclear

| Requirement | call | fast-agent | Notes |
| --- | --- | --- | --- |
| Multi-LLM provider support | No | Yes | call is runtime-focused; fast-agent documents broad provider coverage |
| Chat Completions API support | No | Yes | call uses Agents SDK runtime; fast-agent OpenAI provider supports Chat Completions |
| Strong ACP standards support | No | Yes | call does not implement ACP; fast-agent implements ACP tool-call/permission handling |
| Workflow types¹ | No | Yes | call has no built-in workflow types; fast-agent exposes explicit workflow agents |
| OpenAI Agents SDK dependency (vendor lock risk) | Higher | Lower | call uses Agents SDK; fast-agent is multi-provider |
| Unified org-level invocation syntax | Yes | No | call has a single resolution model; fast-agent is app-level |
| Prompt repo with metadata + ready/draft | Yes | No | call has strict markdown cards and repo.db index |
| Multi-project / org routing | Yes | No | call is project-first; fast-agent is per app |
| REST API surface | Yes | No | call Actions API is documented |
| Telegram bot surface | Yes | No | call has full Telegram integration |
| CLI surface | Yes | Yes | both provide CLI entry points |
| MCP server surface | Yes | Yes | both support MCP, but in different roles |
| OpenAI Responses API support | Yes | Yes | call docs mention Responses/Agents behavior; fast-agent includes Responses provider |
| MCP allowlist per agent | Yes | Yes | call documents allowlist; fast-agent supports per-agent tool gating |
| AgentCard support | Yes | Yes | call uses agent.md cards; fast-agent loads AgentCards from Markdown/YAML |
| Strong MCP standards support | Yes | Yes | call is MCP-first for runtime surfaces; fast-agent has comprehensive MCP stack |
| Standardized logging and traceability | Yes | Yes | call emphasizes multi-surface logs and error envelopes |
| Agents-as-tools orchestration | Yes | Yes | both support, with different focus |

## Conclusion

- If the goal is the org-level Call subsystem described in the strategy document, call is the correct base repository.
- fast-agent is valuable as an agent engine or for advanced MCP/ACP features, but it does not replace the call layer without significant additional infrastructure.
- If avoiding OpenAI vendor lock is critical, call’s reliance on Agents SDK / Responses is a tradeoff; fast-agent’s multi-provider stance reduces that lock-in.
- If Chat Completions API compatibility is a requirement, fast-agent supports it; call does not.

## Suggested usage pattern

- Use call as the main runtime and orchestration layer.
- Use fast-agent as a specialized engine for specific agents or workflows where its MCP/ACP features add value.
- If integrating fast-agent, expose it as a tool or backend through call rather than replacing call.

---

¹ Workflow types: chain, parallel, router, orchestrator, iterative_planner, evaluator_optimizer, maker.
