# From Proprietary `call` platform to fast-agent.ai: Lessons in Open Agent Platforms

I spent a **lot** of time building my own agent platform `call` on top of OpenAI Agents SDK… and then I made a decision that felt both painful and obvious:

**I’m migrating to open source** — if we’re a small team, our job is to build agents, not platforms.

This isn’t a “my framework is better than your framework” story. It’s a story about why *closed* agent platforms are structurally doomed — and why the next wave of agent infrastructure will be built around open standards.

---

## The assumptions I designed `call` around

When I started, my mental model was simple:

- **Agents should be Markdown “cards” in GitHub** — editable by any teammate.
- **Use OpenAI platform** as much as possible (Responses API, WebSearch/FileSearch tools, whisper, gpt-image-1).
- **Sequential thinking** and step-by-step decomposition is a great default.
- **Non-technical users must access agents from ChatGPT** (via Actions in chatgpt.com) → REST API is mandatory.
- **Non-technical users must access agents from Telegram** → adapters and an ergonomic UX matter.
- **Bot configuration should be plain text.**
- **Inside Telegram or chatgpt.com, agents should be addressable via @mentions.**
- **Complex LangChain workflows are often unnecessary** if agents can call other agents.

So I built it [strato-space/call](<https://github.com/strato-space/call>). 

And yes — it worked:

- python library + REST + MCP + Telegram interfaces
- hot updates of agents while the system is running via /reload command
- detailed logs include all mcp calls tracing and subagent call
- progress optional streaming into Telegram

But the cost was… **enormous**.
Not just engineering hours — the “maintenance gravity” of owning a proprietary runtime.

---

## The strategic realization

Closed platforms like this eventually collapse under their own weight.

Because the real competitive surface isn’t “who has the most clever orchestrator.”
It’s:

- who interoperates best,
- who ships the cleanest developer experience,
- and who embraces standards so the ecosystem can compound.

That’s why **fast-agent.ai** stood out during my platform research.

---

## Why fast-agent.ai

fast-agent.ai [evalstate/fast-agent](<https://github.com/evalstate/fast-agent>) pulled me in with a very specific combo:

- 1st‑class support for all MCP aspects — including sampling and elicitation
- broad model support (pragmatic reality: everyone is multi-provider now)
- full workflow set out of the box
- a compact, readable panel that shows execution flow *as it happens*
- clear, transparent documentation
- fast onboarding
- a polished CLI
- product-grade “mass adoption” potential (it was built as a shippable product from day one)
- you can read user reviews here: https://fast-agent.ai/ref/fast_agent_ai_articles/

And then I hit the first big migration friction…

---

## Agents-as-Tools: the first serious contribution

In OpenAI Agents SDK, “Agents as Tools” is a native concept: the parent agent can call child agents like tools, in parallel, with clean separation.

I needed that immediately — because one of my core use cases is **same prompt, many datasets**.
Example: daily/weekly summaries across **20+ projects**.

So I started implementing **Agents-as-Tools** for fast-agent.ai — and that became my first major contribution.

It turns out: doing this *well* in fast-agent.ai is harder than in a minimal console runtime, because fast-agent.ai has something better:

- a shared panel
- structured logs
- token stats
- and the expectation of clean per-instance telemetry

That’s not a downside — it’s the bar.

---

## Respect where it’s due — and where the world is going

Huge respect to **Sarmad Qadri** and the core idea behind *mcp-agent*:

> mcp-agent’s vision is that MCP is all you need to build agents, and that simple patterns are more robust than complex architectures for shipping high-quality agents.

I agree with the spirit completely: **simple patterns win**.

But I think the “minimum viable open agent stack” is now slightly bigger than “just MCP”.

After many discussions with the project author — Shaun Smith (https://huggingface.co/evalstate / https://github.com/evalstate) — the stack I see emerging is:

- **MCP** for tool/runtime interoperability
- **ACP** to connect agents to IDEs and IM
- **REST API adapter** for web applications
- **Chatbot SDK** for messaging platforms
- **Agents calling agents** as the primitive (not “workflows as code, just call from prompt”)
- **SKILLS** as portable, typed capability modules
- **AgentCard** as a portable agent definition format (removing vendor lock-in)
- **True REPL** for agent development: **run → observe → self-reflect → evolve → run again**

That last piece matters more than it sounds.
Without a true REPL loop, agents become “compiled artifacts” that you redeploy.
With it, agents become living systems you iterate on safely.

This is exactly why I’m excited about the AgentCard initiative and the discussion here:

- https://github.com/evalstate/fast-agent/issues/522

(AgentCard may end up being as ecosystem-shifting as MCP itself.)

---

## The real thesis

Closed agent platforms are doomed because they can’t compound with the ecosystem.

Open platforms will win — but only if they deeply support standards and composability:

- **ACP, MCP, AgentCard, SKILLS, True REPL** + **Building Effective Agents** in the box

- **ACP**: [Agent Client Protocol](<https://zed.dev/acp>)
- **MCP**: [Model Context Protocol](<https://modelcontextprotocol.io>)
- **AgentCard**: [AgentCard at the Summit: The Multi-Agent Standardization Revolution](<https://github.com/evalstate/fast-agent/blob/main/plan/agentcard-standards-mini-article.md>)
- **SKILLS**: <https://github.com/anthropics/skills>
- **True REPL**: run → observe → self-reflect → evolve → run again
- **Anthropic’s Workflows Definitions**: [Building Effective Agents](<https://www.anthropic.com/engineering/building-effective-agents>)

---

## Call to action

If you’re building agent infrastructure — or betting your company on agents — this is the time to converge on shared primitives.

- Follow fast-agent.ai: https://github.com/evalstate/fast-agent/
- Join the discussion: https://discord.com/invite/xg5cJ7ndN6

---

## A question to end on

What is the **ideal agent platform** *right now*?

What qualities should it have out of the box:

- interoperability?
- a broad set of adapters out of the box?
- transparency?
- REPL-first development?
- portable agents (AgentCard)?
- portable capabilities (Skills)?
- All Anthropic’s Workflows Definitions?
- portable AgentJARs (AgentCards + Skills + MCP config) in zip format?
- agent-to-agent composition as the default?

And how compatible do we actually want platforms to be:

- compatible at the tool layer only (MCP)?
- compatible at the agent definition layer (AgentCard)?
- compatible at the capability layer (Skills)?
- compatible at the memory/state layer?

I’m convinced this is where real competition should happen — on standards, composability, and developer ergonomics — not on closed ecosystems.

---
