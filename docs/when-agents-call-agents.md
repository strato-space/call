# When Agents Call Agents (and why Skills aren’t enough)

You don’t run **one** project. You run a **portfolio**.

20+ projects. Different teams. Different tools. Different truths.

As a CTO or PMO lead, you want to ask—anytime:

- What’s up with Project X?
- Where are the risks?
- What changed since yesterday?
- What’s the next best decision?

And ideally, every morning before standup, you want a clean **independent audit** per project: status, risks, blockers, opportunities—consistent format, comparable across projects.

That is not a “single assistant” problem.

It’s a **delegation** problem.

---

## Skills are necessary, but not sufficient

Skills (tools, functions, capabilities) are great: summarize docs, query Jira, scan meetings, extract risks.

But at PMO scale, the bottleneck is not the capability list. It’s the **operating model**.

Skills alone fail when:

1) **Context must be owned**
- A project audit needs charter, plan, stakeholders, sources, local rules.
- A skill doesn’t *own* that context; it executes inside someone else’s.

2) **Work is a loop, not a call**
- Auditing is gather → validate → cross-check → summarize → report.
- Skills fire once; they don’t *operate*.

3) **Parallelism must be first-class**
- You can parallelize tool calls.
- PMO needs *parallel, independent executors* with traceability.

So the right decomposition is:

- **Skills = capabilities**
- **Agents = operators with owned context**
- **Agent→Agent calls = composition at scale**

---

## The primitive that scales: Agent→Agent composition

The scalable architecture is simple:

- a **Portfolio Orchestrator** agent
- calling **Project Manager** agents (one per project / stream)
- each Project Manager agent calls skills/tools to pull sources, verify facts, and produce an audit

This is *not* “complex workflow code”.
It’s a robust pattern: **delegate, isolate, run in parallel, aggregate**.

---

## Agents-as-Tools in fast-agent.ai

FastAgent implements the **Agents-as-Tools** pattern inspired by OpenAI’s Agents SDK: child agents are exposed as callable tools to a parent.

The key engineering choice is what makes it PMO-grade:

> FastAgent spawns **detached per-call clones** of child agents, so each parallel execution has its own **LLM + MCP stack**—no shared-state hacks.

At 20–50 projects, shared state becomes a bug farm: mixed logs, name overrides, tool collisions, wrong attribution.

Per-call clones give you:

- **independent context boundaries** (no “context soup”)
- **clean logs** (no interleaving chaos)
- **correct stats per instance** (tokens/tools per project)
- **safe parallel audits** (each project runs like its own mini-system)

This is exactly what a PMO needs: not just answers, but **auditable operations**.

---

## A mental model: PMO is a swarm, not a monolith

Your “PMO assistant” is really a **swarm**:

- one agent per project
- each agent knows its sources (folders, chats, docs, task filters, meetings)

Daily loop:

1) Orchestrator spawns project agents in parallel
2) Each project agent rebuilds a fresh view from sources
3) Each returns a compact audit (status/risks/blockers/opportunities)
4) Orchestrator normalizes and produces a portfolio report

That’s how you get repeatable, comparable, decision-ready outputs.

---

## Simple patterns win

Huge respect to **Sarmad Qadri** and the *mcp-agent* vision:

> “MCP is all you need to build agents… simple patterns are more robust than complex architectures.”

I agree: **simple patterns win**.

But the minimum viable **open agent stack** is now slightly bigger than “just MCP”.

What’s emerging in practice:

- **MCP** — tool/runtime interoperability
- **Skills** — portable capability modules
- **AgentCard** — portable agent definition (reduces vendor lock)
- **Agent→Agent composition** — delegation as the default primitive
- **True REPL loop** — Read Eval Print Loop: run → inspect → self-reflect → patch → rerun

The point isn’t complexity. The point is **composability and compatibility**.

---

## Links & examples
- **FastAgent**: fast-agent.ai
- **MCP**: [Model Context Protocol](<https://modelcontextprotocol.io>)
- **SKILLS**: <https://github.com/anthropics/skills>
- **FastAgent repo + examples**: 
```bash
git clone https://github.com/evalstate/fast-agent
cd fast-agent/examples/workflows-md
uv run fast-agent --card agents_as_tools_simple
```
- [**AgentCard at the Summit: The Multi-Agent Standardization Revolution**](<https://github.com/evalstate/fast-agent/blob/main/plan/agentcard-standards-mini-article.md>)
- **Agent→Agent composition** [fast-agent.ai: Agents As Tools](<https://fast-agent.ai/agents/workflows/#agents-as-tools_1>), [OpenAI Agents SDK: Agents As Tools](<https://openai.github.io/openai-agents-python/tools/#agents-as-tools>)
- **Discord**: https://discord.com/invite/xg5cJ7ndN6

If you want to experiment: run the FastAgent workflow examples.
If you want to debate architecture - welcome to Discord / LinkedIn.
