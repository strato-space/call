# How I Built an "App Server" That Lived 25+ Years in Telco BSS (Without a Rewrite)

Most people hear "25-year-old telco BSS" and think "rewrite".
In BSS, rewriting billing is a great way to lose sleep and customers.

We kept a core system alive for 25+ years by doing something boring and effective:
move logic closer to the data, and evolve interfaces via a strangler approach.

## The original stack

At the center of the BSS world was Oracle.
The UI started as classic client-server:
- Oracle RDBMS core
- Delphi thick client
- a few small Oracle Forms islands

For performance-critical paths we also had server-side C++ code talking to Oracle via OTL:
https://otl.sourceforge.net/

Then the world changed:
- web and partner integrations
- XML/XSLT wave
- early mobile devices
- more consumers, more APIs, more scale

We could not afford a big-bang rewrite, so we evolved in-place.

## What actually made it last

Three things mattered more than any framework choice:

1. **Algorithms near the data**
2. **Modularity via PL/SQL packages**
3. **Strangler evolution instead of rewrites**

That combination scaled surprisingly well for a small team and a high-correctness domain.

## PL/SQL: great procedural core, mediocre OOP story

Oracle PL/SQL is not fashionable, but it is extremely practical for BSS-style domains:
- deep integration with SQL (you do not fight the database, you use it)
- transactions are first-class (money and entitlements stay consistent)
- packages are a strong modular boundary

Oracle did attempt to bolt on OOP (object types, etc.). For us, the sweet spot was:
procedural logic, good package boundaries, and clear contracts.

The core idea:

> keep algorithms close to data

It simplifies the system because you have fewer hops and fewer distributed edge cases.

## Tradeoffs (pros and cons)

Some of our architectural choices were "right" in context, but they had a price.

Pros:
- C++ + OTL made the system fast early on (low overhead, high throughput).
- Keeping caches in-process (STL containers) made hot reads extremely cheap.

Cons:
- The C++ core was harder to change quickly. Shipping new business behavior got slower over time.
- In-process caches widened the failure domain: when the main process died, everything died and cold start took longer.
- The original architect got promoted into management too fast, and we never got a clean "extension layer" (we wanted to embed a scripting language like JS).
- Java dominated enterprise stacks back then, but Java + C++ integration was painful, so the "make it scriptable" plan kept slipping.
- We introduced XSLT not only at the edge (Apache `mod_xslt_proxy` style), but also inside the server, which produced some very strange scripts.

## The strangler approach (the only safe way in BSS)

We did not "replace the system".
We strangled it.

Practical steps:
1. keep the Oracle model stable (schemas, invariants, transactions)
2. move more business rules into PL/SQL packages where correctness matters
3. add new integration surfaces without breaking existing clients
4. gradually route more usage through the new surfaces

This is how you modernize without creating a second system that never catches up.

## Interfaces evolved: HTML to XML to JSON to REST

Yes, there was a mod_plsql era:
- PL/SQL handlers as web endpoints
- HTML generated from PL/SQL

The key point is not "PL/SQL generated HTML".
The key point is: we added a web surface without a rewrite.

Then came XML/XSLT and mobile, and the system naturally moved toward structured payloads:
- emit XML first, later JSON
- keep PL/SQL as the business/API layer
- transform/present elsewhere when needed

## Design rule: PL/SQL maps cleanly to REST (bind variables = parameters)

One pattern that aged extremely well:
- PL/SQL procedure/function = business operation
- SQL queries with bind variables = efficient data access
- HTTP endpoint = 1:1 mapping to that operation

Example mental model:
- `GET /customers/{id}/services` -> `pkg_services.get_services(p_customer_id => :id)`
- `POST /contracts/{id}/activate` -> `pkg_contracts.activate(p_contract_id => :id, p_actor => :user)`

It makes incremental API growth straightforward and keeps the business layer explicit.

## Resilience: sometimes you must serve without the database

Telco reality: the DB can be hot, slow, or temporarily unreachable.
Some parts of the product still need to behave.

We used caches for read-heavy, user-facing flows like:
- service catalog / list of services
- reference data
- precomputed entitlements (where safe)

Not everything can run without the DB. More can degrade gracefully than most teams plan for.
But putting caches into the same C++ address space as the main service is a tradeoff:
great latency, bad blast radius.

## What we should have done sooner

If I could go back in time, two changes should have happened earlier (even as "ugly" incremental steps):
- detach the cache from the main process (separate failure domain, faster recovery)
- embed a widely used scripting language (JS would have been ideal) to speed up changes

## What I would do now: MCP transport + generated interfaces

If I was modernizing this system today, I would not start with a rewrite.
I would start by making it callable and composable:
- add an MCP transport
- generate tool schemas and interfaces dynamically from the DB/PL/SQL surface (and version them)
- treat the Oracle core as a tool server (auth, audit, contracts)

## It was basically a "tool server" for Oracle before MCP was mainstream

If you squint, a PL/SQL package surface looks like a tool surface:
- stable operations
- transactional semantics
- auditable execution paths

Modern stacks give this a better shape (schemas, auth methods, transports), but the architectural intent is the same:
make the data core callable via a stable set of operations.

## Why I care now: "Agents will replace all software"

Satya Nadella has been quoted (often with viral framing) as predicting that business apps shift toward agent-driven interaction.

Video: https://www.youtube.com/watch?v=uGOLYz2pgr8

Ignore the hype and look at the shape:
- the UI becomes more agent-like
- the durable layer becomes the tool/API layer

In that world, BSS systems do not disappear.
They become tool servers for billing, entitlements, and contracts.

And "algorithms near the data" becomes relevant again because agent runtimes reward:
- fewer hops
- clearer contracts
- stronger transactional semantics

## What I'd do differently today (same idea, more discipline)

If I rebuilt this evolution today, I would keep the core idea and add modern guardrails:
- explicit API contracts + contract tests
- correlation IDs end-to-end
- structured logs and audit trails for every operation
- strict versioning strategy for procedures/endpoints
- CI for packaging, linting, and deploys
- clear split between write paths (transactions) and read models (caches/search)

## Closing

This was not "the prettiest architecture on paper".
It was a durable architecture under telco constraints:
- correctness over cleverness
- simplicity over sprawl
- evolvability over purity

If you want this as a real case study, I can add a follow-up with:
timeline, scale metrics, team size, and a couple of war stories.
