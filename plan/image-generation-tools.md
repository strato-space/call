# Image Generation Tools Plan

## 1. Purpose & Scope
- Deliver two interchangeable tools for image generation based on **OpenAI Agents SDK** and **OpenAI Responses API**.
- Accept user prompts enriched with filesystem/URL references resolved through the active MCP servers declared in `mcp_config.yaml`.
- Route generated assets automatically (no inline previews) to destinations chosen per-request: Telegram chat/thread, filesystem path, HTTP-served directory (nginx), base64 envelope (compatible with `mcp-images` contract), or combinations thereof.
- Keep implementation modular enough to be reused outside of `call/` (future dedicated MCP), even at the cost of selective duplication.
- Touchpoints to cover: `lib`, `mcp`, `actions`, `telegram_bot`, and configuration files (notably `mcp_config.yaml`).

## 2. Current Architecture Reference
- `app/call.py` already wires tool factories (`get_tool_by_name`, `ImageGenerationTool`, `function_tool`).
- `actions/main.py`, `mcp/server.py`, and `telegram_bot/bot.py` share the MCP owner lifecycle; new tools must respect `wait_for_mcp_init()` and structured envelopes from `call.lib.api`.
- `mcp_config.yaml` lists available MCP servers (filesystem, tg-ro, etc.) and can describe future image-delivery MCP endpoints.

## 3. Shared Building Blocks (new module group)
1. **`image_tool/common.py` (new)**
   - Request DTO describing: prompt text, optional background/size/model overrides, and a `routing` map (telegram, filesystem, nginx, base64, etc.).
   - Utilities for pulling referenced files via MCP handles (filesystem paths, remote URLs) and bundling them into OpenAI uploads.
   - Validation helpers enforcing "no public preview" default; require explicit routing targets.
2. **`image_tool/delivery.py` (new)**
   - Telegram dispatcher wrapper that reuses `telegram_bot/bot.py` messaging helpers when chat/thread context is known.
   - Filesystem writer: ensures directories exist, returns canonical paths.
   - Nginx publisher: writes into configured document root and produces URL templates.
   - Base64 formatter aligned with `mcp-images/mcp_image.py` (for downstream evaluation agents).
   - Supports combinational routing lists with deduplicated writes.
3. **`image_tool/storage.py` (new)**
   - Shared temp directory manager, naming scheme, metadata manifest for each generation run.
   - Optionally register outputs in a catalog (for future agents-as-tools re-use).
4. **Configuration surface**
   - Extend `.env` / runtime config with defaults: `IMAGE_TOOL_OUTPUT_DIR`, `IMAGE_TOOL_NGINX_ROOT`, `IMAGE_TOOL_DEFAULT_MODEL`, `TELEGRAM_DEFAULT_CHAT_ID`, etc.
   - Document overrides in README/plan; do not hardcode secrets.

## 4. Tool Variants
### 4.1 Agents SDK Implementation
- File: `app/tools/image_agents_tool.py` (new) or similar, exporting `ImageAgentsTool`.
- Responsibilities:
  1. Instantiate OpenAI Agents SDK client with MCP attachments (reuse existing initialization path from `call.app.call` to share SSE/STDiO connectors).
  2. Register tool metadata so `agents.Agent` can call it via `get_tool_by_name()`.
  3. Accept the shared request DTO, translate into Agents SDK tool invocation (likely `client.responses.create_and_stream` via agents interface) while attaching retrieved files.
  4. Convert SDK response (which may include multiple files) into normalized artifact records for delivery module.
- Pros: reuses MCP hookups, easier when agent orchestrations already live inside Agents runtime.
- Cons: less granular control over each image parameter; debugging requires inspecting Agent tool traces.

### 4.2 Responses API Implementation
- File: `app/tools/image_responses_tool.py` (new) exposing `ImageResponsesTool`.
- Responsibilities:
  1. Direct `client.responses.create()` call with `model="gpt-image-1"` (or configurable) and full control over size/background/quantity.
  2. Manage file uploads manually (images, reference masks) by first calling `client.files.create` for MCP-provided paths/URLs when needed.
  3. Stream or poll until `output[ ].content` returns attachments; gather `b64_json` or hosted file ids.
  4. Hand outputs to delivery module; optionally keep low-level metadata for logging/telemetry.
- Pros: total control and richer logging; easier to support per-call overrides (samplers, seeds) later.
- Cons: duplicates some binding logic vs Agents path.

### 4.3 Runtime Selection
- `call.lib.api` gains a helper for declaring tool choices in `RunnableConfig.tools` (e.g., `image_generation_agents` vs `image_generation_responses`).
- CLI/MCP/Actions payloads can specify which backend to use via attributes or tool name; default can be set in `.env`.

## 5. Routing & Delivery Contract
1. **Telegram**
   - Use session metadata (`session_id`, `chat_id`, `thread_id`) coming from Actions/MCP/Bot payloads.
   - Delivery module prepares captions, attaches files via existing bot senders, and confirms message ids.
2. **Filesystem Path**
   - Write images under configured root (per request override allowed) and return absolute path(s) in final envelope.
3. **Nginx URL**
   - When nginx watches the filesystem root, compute URLs using base URL template (`IMAGE_TOOL_NGINX_BASE_URL`).
4. **Base64 Envelope**
   - Provide `data:image/png;base64,...` responses aligned with `mcp-images/mcp_image.py` so other models can consume inline.
5. **Combinations**
   - Routing map supports multiple destinations; delivery module executes them in deterministic order and aggregates a summary section for the final response.

## 6. Subsystem Integration Steps
1. **`call/lib`**
   - Add request/response DTOs and a high-level `generate_image()` API that both Actions and MCP surfaces can call.
   - Extend `RunnableConfig.tools` documentation to highlight new tool names and routing options.
2. **`app/call.py`**
   - Register both tool implementations in `get_tool_by_name` catalog.
   - Ensure `image_genetation_tool` legacy helper routes through the shared core (soft deprecation with warnings).
3. **`actions/main.py`**
   - Introduce a POST `/images` (or extend `/exec` contract) that accepts routing + prompt + backend selector.
   - Add OpenAPI schema pieces so clients know available routing flags and defaults.
4. **`mcp/server.py`**
   - Publish a dedicated MCP tool (`generate_image`) whose arguments mirror the shared DTO; enforce `wait_for_mcp_init()` before dispatching.
   - Update `mcp_config.yaml` intent/when_to_use blocks accordingly.
5. **`telegram_bot/bot.py`**
   - Add command (e.g., `/image`) and support inline `@image` shortcuts; parse routing flags (e.g., `--save`, `--url`).
   - Ensure the bot passes chat/thread context into the shared delivery module and respects "no preview" default.
6. **`mcp_config.yaml`**
   - Document new tool under `call` server entry and, if needed, add a future standalone `image-tool` MCP definition referencing the decoupled module.
7. **Docs & Examples**
   - Update `README.md` (architecture + usage), `AGENTS.md` (tool handler pattern), and add cookbook snippets.

## 7. Separation for Future Standalone MCP
- Keep shared code under a neutral namespace (`call/image_tool/`) to allow packaging as `mcp-image-tool` later.
- Avoid importing heavy `call.app.call` internals from the shared modules; rely on small adapter interfaces that can be re-implemented in another repo.

## 8. Testing Strategy
1. Unit tests for routing permutations (filesystem-only, telegram+base64, etc.).
2. Contract tests mocking OpenAI clients to ensure both Agents and Responses implementations return identical delivery metadata.
3. Integration smoke tests via `mcp/server.py` tool invocation and via `actions` endpoint.
4. Telegram bot e2e test stub using dry-run messenger to verify attachments and captions are formed.

## 9. Open Questions / Decisions Needed
1. Preferred default backend (Agents vs Responses) for production?
2. Should nginx publishing be synchronous (local FS) or rely on existing MCP file-servers?
3. Do we need quota/authorization controls per routing destination (e.g., restrict filesystem paths)?
4. How should multi-image prompts be expressed (explicit `count`, or infer from tool configuration)?
5. Is there an existing artifact catalog we should integrate with, or should the new storage manifest live independently?
