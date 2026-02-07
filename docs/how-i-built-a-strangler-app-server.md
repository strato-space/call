# How I Built a "Strangler" App Server That Lived 25+ Years in Telco BSS

This is a draft for a longer write-up about a telco BSS core that survived (and kept evolving) for more than 25 years.

The short version: Oracle was the center of the BSS universe, the UI started as classic client-server (Oracle + Delphi, with small Oracle Forms elements), and the "app server" gradually moved closer and closer to the database until it effectively lived there.

The system lasted because it was:
- simple enough to reason about
- close to the data (and transactions)
- designed to evolve via a strangler approach instead of rewrites

## Starting point: Oracle + Delphi client-server

At the time, the architecture was the obvious one:
- Oracle RDBMS as the core
- Delphi thick client (plus small Oracle Forms islands where it made sense)
- business logic split between client code and database code

Then requirements shifted:
- web UIs, partner integrations, new channels
- XML/XSLT wave
- early mobile devices
- more external APIs, more data consumers
- higher scale and stricter correctness expectations

A rewrite was not an option. Too risky. Too expensive. Billing never sleeps.

So we evolved the system in-place.

## Why PL/SQL worked (better than many expect)

Oracle PL/SQL is a very strong procedural language when you accept its strengths:
- it is tightly integrated with SQL
- transactional semantics are a first-class default
- modularity via packages is a real architectural boundary
- a small team can ship business logic quickly and safely

Oracle also tried to bolt on OOP (object types, etc.). In practice, for BSS-style domains, the procedural style plus good package boundaries was the winning model.

The core architectural idea was:

> keep algorithms close to data

When logic lives next to the data:
- the mental model is simpler (fewer moving parts and hops)
- correctness is easier (fewer distributed edge cases)
- performance is better (less chatty client <-> DB traffic)
- operations are safer (fewer services to keep in sync)

## The strangler move: evolve without rewriting

We did not "replace the system".
We strangled it (Strangler Fig Pattern style): introduce a new path, route more and more behavior through it, keep the old path alive until it is no longer needed.

In practice, the steps looked like:
1. keep the Oracle model stable (schemas, invariants, transactions)
2. move more business rules into PL/SQL packages where correctness matters
3. add new integration surfaces around it without breaking existing clients

## PL/SQL for everything (including HTML): mod_plsql era

There was a period where we experimented with mod_plsql / PL/SQL gateway style:
- PL/SQL handlers as request endpoints
- HTML generated directly from PL/SQL

It sounds weird now, but it was pragmatic then.
I even remember Tom Kyte's site being on a similar platform at some point, which was a reassuring signal that this wasn't purely a hack.

The important point is not "PL/SQL generated HTML".
The important point is: we added a web surface without a rewrite.

## XML/XSLT and the early mobile era

XML/XSLT and early mobile clients pushed the system toward a cleaner separation:
- PL/SQL as the business/API layer
- structured payloads (XML first, later JSON) as the output contract
- transformations/presentation handled outside the DB when needed

Still one strong core, but with cleaner interfaces.

## Design principle: PL/SQL maps 1:1 to REST (bind variables = parameters)

A pattern that aged surprisingly well:
- PL/SQL procedures/functions define business operations
- SQL queries with bind variables define efficient data access
- HTTP endpoints map to those operations almost 1:1

Mental template:
- `GET /customers/{id}/services` -> `pkg_services.get_services(p_customer_id => :id)`
- `POST /contracts/{id}/activate` -> `pkg_contracts.activate(p_contract_id => :id, p_actor => :user)`

Bind variables map naturally to:
- path parameters
- query parameters
- request bodies (after validation/normalization)

This made it feasible to add APIs incrementally and keep them honest.

## Performance and resilience: caching without the database

Telco reality: the DB can be hot, slow, or temporarily unreachable, but the product still needs to behave.

We had patterns where parts of the system could serve from caches:
- list of available services
- reference/catalog data
- precomputed entitlements for read-heavy flows

Not everything can work without the DB, but more of the UX can degrade gracefully than people assume.

## It was basically "tools over Oracle" before MCP was mainstream

If you squint, an API surface built from PL/SQL packages is a tool surface:
- stable operations
- transactional semantics
- auditable execution paths

In that sense, it was a "server" for calling Oracle business operations long before modern tool schemas and transports became mainstream.

## Does "RDBMS returns JSON/XML" still make sense today?

I think yes, in the right places:
- when you need strong consistency
- when relational semantics are complex and central
- when latency matters and chatty service graphs hurt
- when the team is small and correctness is expensive

A modern version of the pattern:
- stored procedures return JSON (or XML where needed)
- upstream services/clients treat the DB layer as an API boundary
- contracts are versioned and tested

Not universal. Still valid.

## "Agents will replace ALL software": why this story matters again

Microsoft CEO Satya Nadella has been quoted (often in a viral framing) as essentially predicting that business apps will shift toward agent-driven interaction.

Video: https://www.youtube.com/watch?v=uGOLYz2pgr8

Even ignoring hype, the direction feels real:
- the UI becomes more "agent-shaped"
- the durable layer becomes the tool/API layer
- SaaS becomes more composable and less monolithic

In that world, BSS systems do not disappear. They become tool servers:
- discoverable operations (contracts, billing, entitlements)
- strict auth and audit
- stable, versioned interfaces
- predictable performance

And "algorithms near the data" becomes interesting again because agent runtimes reward:
- fewer hops
- clearer contracts
- stronger transactional semantics

## What I'd do differently today

If I were rebuilding the same evolution now, I'd keep the core idea but add modern discipline:
- explicit API contracts + contract tests
- correlation IDs end-to-end
- structured logging for every operation
- strict versioning strategy for procedures/endpoints
- automated CI for PL/SQL packaging, linting, and deploys
- clear split between:
  - write paths (transactional)
  - read models (caches, materialized views, search)

## Closing

This was not "the best architecture on paper".
It was a durable architecture under telco constraints:
- correctness over cleverness
- simplicity over sprawl
- evolvability over purity

And the agent era might make this style popular again, just with better tooling, transport, packaging, and standards.

## TODOs (to turn this into a real case study)

- timeline: years + major transitions (Delphi, mod_plsql, XML/XSLT, REST/JSON)
- scale: TPS, DB size, subscriber/contracts counts, batch volumes
- team: size, cadence, biggest operational lessons
- one incident story: why transactions-at-the-core mattered
- one migration story: feature moved from client code -> PL/SQL -> API without breaking clients
