# MCP SSE / Streamable HTTP timeouts

This document records:

- Historical **local patches** in `.venv` that raised `sse_read_timeout` to 30 minutes (sections 1-6).
- The current `call` runtime state where we **returned to the default 5-minute** timeouts and solved disconnects on the `call` side via MCP auto-reinitialization.

Current strategy (2025-11-19):

- All MCP clients (`mcp.client.sse`, `mcp.client.streamable_http`, `agents.mcp.server`, `fast_agent`) use `sse_read_timeout = 60 * 5` (5 minutes). The 30-minute patches are rolled back.
- On `anyio.ClosedResourceError`, `httpx.ReadTimeout` / `ConnectTimeout`, or `McpError` during agent startup, `call` treats the MCP session as "dead" and runs:
  - `cleanup_mcp_servers()`
  - `wait_for_mcp_init(timeout=120.0)`
  - one retry of `Runner.run`.
- Therefore, SSE drops after a few minutes of idle time are treated as a normal "recreate the session" signal, not a fatal error.

Sections 1-6 below describe patches that **can be reused** if a 30-minute timeout is needed again.

> NOTE: all paths below are **inside the virtualenv** used by `call`.
> After reinstalling deps, these files will be overwritten.

---

## 1. agents: increase default SSE read timeout for MCP servers (historical patch)

File:

- `.venv/lib/python3.13/site-packages/agents/mcp/server.py`

Changes:

- In `MCPServerSseParams` docstring, the default is still described as
  "5 minutes", but the effective default is now changed via code below.
- In `MCPServerSse.create_streams()`:

  ```python
  return sse_client(
      url=self.params["url"],
      headers=self.params.get("headers", None),
      timeout=self.params.get("timeout", 5),
-     sse_read_timeout=self.params.get("sse_read_timeout", 60 * 5),
+     sse_read_timeout=self.params.get("sse_read_timeout", 60 * 30),
  )
  ```

- In `MCPServerStreamableHttp.create_streams()` (both branches):

  ```python
  return streamablehttp_client(
      url=self.params["url"],
      headers=self.params.get("headers", None),
      timeout=self.params.get("timeout", 5),
-     sse_read_timeout=self.params.get("sse_read_timeout", 60 * 5),
+     sse_read_timeout=self.params.get("sse_read_timeout", 60 * 30),
      terminate_on_close=self.params.get("terminate_on_close", True),
      ...
  )
  ```

Effect (when the patch was active):

- If `sse_read_timeout` was not explicitly set in MCP server params, `agents`
  used **30 minutes** instead of **5 minutes** for SSE / Streamable HTTP.

In the current `call` runtime, these changes are rolled back and the libraries
use the default `60 * 5`.

---

## 2. mcp.client: StreamableHTTP defaults (historical patch)

File:

- `.venv/lib/python3.13/site-packages/mcp/client/streamable_http.py`

Changes:

- In `StreamableHTTPTransport.__init__`:

  ```python
- def __init__(..., timeout: float | timedelta = 30, sse_read_timeout: float | timedelta = 60 * 5, ...):
+ def __init__(..., timeout: float | timedelta = 30, sse_read_timeout: float | timedelta = 60 * 30, ...):
  ```

- In the helper context manager `streamablehttp_client`:

  ```python
- async def streamablehttp_client(..., timeout: float | timedelta = 30, sse_read_timeout: float | timedelta = 60 * 5, ...):
+ async def streamablehttp_client(..., timeout: float | timedelta = 30, sse_read_timeout: float | timedelta = 60 * 30, ...):
  ```

Effect (when the patch was active): the transport used **30 minutes** as the
default SSE read timeout. In the current `call` install this is rolled back and
library defaults (5 minutes) are used.

---

## 3. mcp.client: SessionGroup helper defaults (historical patch)

File:

- `.venv/lib/python3.13/site-packages/mcp/client/session_group.py`

Changes:

- Structs used by `ClientSessionGroup` now have 30-minute defaults:

  ```python
  class SseServerParameters(BaseModel):
      ...
-     sse_read_timeout: float = 60 * 5
+     sse_read_timeout: float = 60 * 30

  class StreamableHttpParameters(BaseModel):
      ...
-     sse_read_timeout: timedelta = timedelta(seconds=60 * 5)
+     sse_read_timeout: timedelta = timedelta(seconds=60 * 30)
  ```

Effect (when the patch was active): any code using `ClientSessionGroup` without
explicit `sse_read_timeout` inherited a 30-minute SSE timeout.

The default is now back to 5 minutes.

---

## 4. fast_agent: SSE tracking transports (historical patch)

Files:

- `.venv/lib/python3.13/site-packages/fast_agent/mcp/sse_tracking.py`
- `.venv/lib/python3.13/site-packages/fast_agent/mcp/streamable_http_tracking.py`

Changes:

1. `fast_agent/mcp/sse_tracking.py`:

   ```python
   @asynccontextmanager
   async def tracking_sse_client(
       url: str,
       headers: dict[str, Any] | None = None,
       timeout: float = 5,
-      sse_read_timeout: float = 60 * 5,
+      sse_read_timeout: float = 60 * 30,
       ...
   ):
   ```

2. `fast_agent/mcp/streamable_http_tracking.py`:

   ```python
   class ChannelTrackingStreamableHTTPTransport(StreamableHTTPTransport):
       def __init__(
           self,
           url: str,
           *,
           headers: dict[str, str] | None = None,
           timeout: float | timedelta = 30,
-          sse_read_timeout: float | timedelta = 60 * 5,
+          sse_read_timeout: float | timedelta = 60 * 30,
           ...
       ) -> None:
           super().__init__(
               url,
               headers=headers,
               timeout=timeout,
               sse_read_timeout=sse_read_timeout,
               auth=auth,
           )
   ```

   ```python
   @asynccontextmanager
   async def tracking_streamablehttp_client(
       url: str,
       headers: dict[str, str] | None = None,
       *,
       timeout: float | timedelta = 30,
-      sse_read_timeout: float | timedelta = 60 * 5,
+      sse_read_timeout: float | timedelta = 60 * 30,
       ...
   ):
   ```

Effect (when the patch was active): `fast_agent` tracking transports followed a
30-minute SSE timeout. The default is now 5 minutes.

---

## 5. fast_agent: config-level default for SSE read timeout (historical patch)

File:

- `.venv/lib/python3.13/site-packages/fast_agent/config.py`

Changes:

- In `MCPServerSettings`:

  ```python
-  read_transport_sse_timeout_seconds: int = 300
+  read_transport_sse_timeout_seconds: int = 1800
   """The timeout in seconds for the server connection."""
  ```

Effect (when the patch was active): when reading `fastagent.config.yaml`, the
default became 1800 seconds (30 minutes). In the current `call` config, the
default is back to 300 seconds (5 minutes).

---

## 6. mcp.client.sse: base SSE client default (historical patch)

File:

- `.venv/lib/python3.13/site-packages/mcp/client/sse.py`

> This patch was applied earlier (before this document) but is listed
> here for completeness.

Changes:

- In `sse_client(...)` signature:

  ```python
- async def sse_client(..., timeout: float = 5, sse_read_timeout: float = 60 * 5, ...):
+ async def sse_client(..., timeout: float = 5, sse_read_timeout: float = 60 * 30, ...):
  ```

Effect (when the patch was active): the low-level SSE client used a 30-minute
default timeout. The default is now back to 5 minutes.

---

## 7. Where `sse_read_timeout` can be configured instead of patching

Beyond these patches, the timeout is also **configurable** at several layers:

1. **agents (Python API)**

   - `agents.mcp.server.MCPServerSseParams` includes an optional field
     `sse_read_timeout: float`.
   - `MCPServerStreamableHttpParams` similarly has
     `sse_read_timeout: timedelta | float`.
   - Our patched code now uses:

     ```python
     sse_read_timeout=self.params.get("sse_read_timeout", 60 * 30)
     ```

   - If you manually create `MCPServerSse(...)` or `MCPServerStreamableHttp(...)`
     in your code, you can pass `sse_read_timeout` in params and control the
     timeout without patching libraries.

2. **mcp.client (Python API)**

   - `mcp.client.sse.sse_client(..., sse_read_timeout=...)`
   - `mcp.client.streamable_http.streamablehttp_client(..., sse_read_timeout=...)`

   Any custom MCP client can set this parameter explicitly.

3. **fast_agent config (`fastagent.config.yaml`)**

   - In `fast_agent.config.Settings.mcp.servers.<name>` you can set:

     ```yaml
     mcp:
       servers:
         call:
           read_transport_sse_timeout_seconds: 1800  # or another value
     ```

   - This value is used in `fast_agent.mcp.mcp_connection_manager` when creating
     `tracking_sse_client` / `tracking_streamablehttp_client` and is passed as
     `sse_read_timeout`.

### Why we still patch `.venv`

The current `call` runtime and mass AgentFab runs use the `agents` + `mcp.client`
stack, which did not expose a convenient config-level way to set
` sse_read_timeout` for remote `call-mcp`.

To **quickly remove the ~5-minute limit**, we raised defaults at all layers
in the trace:

- `agents.mcp.server` -> MCPServerSse / MCPServerStreamableHttp
- `mcp.client.sse` / `mcp.client.streamable_http`
- `mcp.client.session_group`
- `fast_agent` tracking transports and config default

In future refactors, we can add explicit wiring for
`read_transport_sse_timeout_seconds` / `sse_read_timeout` from config
(e.g., from `fastagent.config.yaml` or `call/mcp_config.yaml`) and then
stop patching `site-packages`.

---

## 8. How to reapply after upgrading libraries

1. Update dependencies (`pip install -r requirements.txt`, etc.).
2. In the activated `.venv`, run a quick search:

   ```bash
   rg "sse_read_timeout" "$(python -c 'import sys,site; print(site.getsitepackages()[0])')"
   ```

3. Compare with this document and reapply changes in all files from sections 1-6,
   changing `60 * 5` -> `60 * 30` and `300` -> `1800` where described.

After that, long `call-mcp`/AgentFab sessions should no longer fail with
`httpx.ReadTimeout` after 5 minutes of idle time.

---

## 9. OpenAI backend 5-minute limit (Responses / Agents)

This document mostly covers **client-side** SSE/Streamable HTTP read timeouts.
Further research on AgentFab behavior revealed a separate limit of about
**5 minutes on the OpenAI backend**, which affects long-running tasks executed
synchronously via the Responses / Agents API.

Key observations (late 2025):

- The official OpenAI Python SDK uses a default HTTP timeout of **10 minutes**
  (`DEFAULT_TIMEOUT = Timeout(600, connect=5.0)`), so it is **not** the source
  of the 5-minute cutoff.
- Our MCP and `fast_agent` layers now use **30 minutes** as the default
  `sse_read_timeout` (see sections 1-7 above).
- Still, when using AgentFab with the OpenAI Agents SDK / Responses API in
  **blocking mode** (without `background: true`), complex tasks still fail
  after about 5 minutes with an A2A `TaskNotFoundError` (`code = -32001`),
  which is then propagated through MCP.
- The error and the absence of any such limit in local libs indicate a
  **server-side TTL of about 5 minutes** for synchronous Responses / Agents.
- This TTL is **not configurable** on our side. The recommended workaround
  from OpenAI is to run long tasks with `background: true` and then poll
  `/responses/{id}`.

Practical implications for `call` / AgentFab:

- Local SSE timeout settings at 30 minutes ensure clients are willing to wait.
- If an AgentFab task still dies after ~5 minutes with
  `TaskNotFoundError(-32001)` or a similar error, the most likely cause is this
  **backend limit**, not an MCP/SSE timeout.
- To avoid this, the Agents layer should:
  - create Responses with `background: true`,
  - store `response.id`, and
  - retrieve the result via `responses.retrieve(...)` (polling or stream resume).

This section is informational; no additional changes to the MCP SSE stack are
required to handle the 5-minute OpenAI backend limit.

---

## 10. Agent cache and "dead" MCP sessions

- `call/app/call.py` has a small agent cache `AGENT_CACHE` that keeps `Agent`
  instances by name (including sub-agents used as tools, e.g.
  `PM-4-DialogTaskSummary`). This avoids re-creating agents for every call.
- After introducing MCP auto-reinit
  (`cleanup_mcp_servers()` -> `wait_for_mcp_init()` -> retry `Runner.run`), we
  found that the same cached agent could keep references to **old** MCP servers
  that were already `cleanup()`'d with `session = None` (typical symptom:
  `UserError("Server not initialized. Make sure you call connect() first.")`
  from `agents.mcp.server`).
- To avoid this, `get_or_create_agent()` now **refreshes the agent's
  `mcp_servers` field** on each reuse with a fresh MCP server list for the
  current run.
- Together with auto-reinit, this means:
  - when SSE drops or timeouts occur, we recreate MCP sessions;
  - on the next run, a cached sub-agent gets the new `mcp_servers` list and no
    longer holds "dead" connections (including to remote `gsh`).
- If you see repeated "Server not initialized" errors on second and later calls
  after idle time, it is worth:
  - verifying that `agent.mcp_servers` is refreshed on reuse;
  - only then investigating network timeouts and remote MCP server behavior in
    the rest of this document.
