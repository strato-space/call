# fast_agent / MCP SSE timeout notes

## Context

- Stack: `fast_agent` + `mcp.client` + `agents.mcp.server` + HTTP/SSE proxy (nginx) + external MCP servers (incl. call-mcp, gsh-mcp).
- We previously had local patches in `.venv` that raised `sse_read_timeout` to 30 minutes. Those are now rolled back to the default 5 minutes, and stability is handled by MCP auto-reinit at the `call` layer.
- This document captures timeout observations and possible improvements to file as upstream issues/patches later.

## Observation 1: read_transport_sse_timeout_seconds is not applied to HTTP transport

**File:** `.venv/lib/python3.13/site-packages/fast_agent/mcp/mcp_connection_manager.py`

Transport selection snippet:

```python
elif config.transport == "sse":
    ...
    return tracking_sse_client(
        config.url,
        headers,
        sse_read_timeout=config.read_transport_sse_timeout_seconds,
        auth=oauth_auth,
        channel_hook=channel_hook,
    )
elif config.transport == "http":
    ...
    return tracking_streamablehttp_client(
        config.url,
        headers,
        auth=oauth_auth,
        channel_hook=channel_hook,
    )
```

**File:** `.venv/lib/python3.13/site-packages/fast_agent/config.py`

```python
class MCPServerSettings(BaseModel):
    ...
    read_timeout_seconds: int | None = None
    """The timeout in seconds for the session."""

    read_transport_sse_timeout_seconds: int = 300
    """The timeout in seconds for the server connection."""
```

**File:** `.venv/lib/python3.13/site-packages/fast_agent/mcp/streamable_http_tracking.py`

```python
@asynccontextmanager
async def tracking_streamablehttp_client(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    timeout: float | timedelta = 30,
    sse_read_timeout: float | timedelta = 60 * 5,
    terminate_on_close: bool = True,
    httpx_client_factory: McpHttpClientFactory = create_mcp_http_client,
    auth: httpx.Auth | None = None,
    channel_hook: ChannelHook | None = None,
) -> AsyncGenerator[...]:
    """Context manager mirroring streamablehttp_client with channel tracking."""

    transport = ChannelTrackingStreamableHTTPTransport(
        url,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
        auth=auth,
        channel_hook=channel_hook,
    )

    async with httpx_client_factory(
        headers=transport.request_headers,
        timeout=httpx.Timeout(transport.timeout, read=transport.sse_read_timeout),
        auth=transport.auth,
    ) as client:
        ...
```

### Summary

- `MCPServerSettings` exposes `read_transport_sse_timeout_seconds`, which **is only used for SSE transport** (`tracking_sse_client`) in `mcp_connection_manager`.
- For HTTP transport (`transport == "http"`), the same value is not passed into `tracking_streamablehttp_client`, so the default `sse_read_timeout` (5 minutes) cannot be overridden via config.

### Patch idea

- Align HTTP and SSE behavior:

```python
return tracking_streamablehttp_client(
    config.url,
    headers,
    sse_read_timeout=config.read_transport_sse_timeout_seconds,
    auth=oauth_auth,
    channel_hook=channel_hook,
)
```

- With this, the config → transport → `httpx.Timeout(..., read=...)` chain is consistent for both transport types.

### Draft issue (outline)

> **Title:** `MCPServerSettings.read_transport_sse_timeout_seconds` is not applied to HTTP transport
>
> **Summary:**
> `fast_agent` exposes `MCPServerSettings.read_transport_sse_timeout_seconds` as a config field for MCP server connections. For `transport="sse"` this value is correctly passed to `tracking_sse_client` as `sse_read_timeout`, and then to the underlying `mcp.client.sse` transport / `httpx.Timeout(..., read=...)`. For `transport="http"`, however, `MCPConnectionManager` calls `tracking_streamablehttp_client` **without** passing `sse_read_timeout`, so the config field has no effect for Streamable HTTP transports.
>
> **Expected behavior:**
> Both SSE and HTTP MCP transports should honor `read_transport_sse_timeout_seconds` so that operators can configure the SSE read timeout consistently via `fastagent.config.yaml`.
>
> **Suggested fix:**
> Update `MCPConnectionManager.launch_server` to pass `config.read_transport_sse_timeout_seconds` to `tracking_streamablehttp_client`:
>
> ```python
> return tracking_streamablehttp_client(
>     config.url,
>     headers,
>     sse_read_timeout=config.read_transport_sse_timeout_seconds,
>     auth=oauth_auth,
>     channel_hook=channel_hook,
> )
> ```
>
> This keeps behavior backward compatible (default is still 300 seconds) while making the config field effective for HTTP transports as well.

## Observation 2: different default sse_read_timeout in StreamableHTTPTransport vs streamablehttp_client

**File:** `.venv/lib/python3.13/site-packages/mcp/client/streamable_http.py`

```python
class StreamableHTTPTransport:
    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float | timedelta = 30,
        sse_read_timeout: float | timedelta = 60 * 5,
        auth: httpx.Auth | None = None,
    ) -> None:
        ...


@asynccontextmanager
async def streamablehttp_client(
    url: str,
    headers: dict[str, str] | None = None,
    *,
    timeout: float | timedelta = 30,
    sse_read_timeout: float | timedelta = 60 * 30,
    terminate_on_close: bool = True,
    httpx_client_factory: McpHttpClientFactory = create_mcp_http_client,
    auth: httpx.Auth | None = None,
) -> AsyncGenerator[...]:
    ...
    transport = StreamableHTTPTransport(url, headers, timeout, sse_read_timeout, auth)
```

### Summary

- `StreamableHTTPTransport` defaults to `sse_read_timeout=60*5` (5 minutes).
- The helper `streamablehttp_client` defaults to `sse_read_timeout=60*30` (30 minutes).
- For users who only use `streamablehttp_client(...)`, this may be fine, but for those expecting a single default across class and helper, the behavior is a bit counterintuitive.

### Issue idea

- Discuss upstream whether `streamablehttp_client` should align its default with `StreamableHTTPTransport`, or whether this is an intentional API decision (a helper with a longer timeout).
- This is not blocking, but worth clarifying and potentially aligning defaults.

---

These notes are a draft. If we open actual PRs/issues, we should add exact package versions (`fast_agent`, `mcp`, `agents`) and small config examples (`fastagent.config.yaml`) that reproduce the behavior.
