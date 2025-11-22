# fast_agent / MCP SSE timeout notes

## Контекст

- Стек: `fast_agent` + `mcp.client` + `agents.mcp.server` + HTTP/SSE-прокси (nginx) + внешние MCP-сервера (в т.ч. call-mcp, gsh-mcp).
- Ранее были локальные патчи в `.venv`, поднимавшие `sse_read_timeout` до 30 минут. Сейчас они откатаны до дефолтных 5 минут, а устойчивость обеспечивается авто‑reinit MCP на уровне `call`.
- Цель этого документа — зафиксировать наблюдения по таймаутам и возможные улучшения, которые стоит оформить как upstream issue / patch позже.

## Наблюдение 1: read_transport_sse_timeout_seconds не применяется к HTTP-транспорту

**Файл:** `.venv/lib/python3.13/site-packages/fast_agent/mcp/mcp_connection_manager.py`

Фрагмент выбора транспорта:

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

**Файл:** `.venv/lib/python3.13/site-packages/fast_agent/config.py`

```python
class MCPServerSettings(BaseModel):
    ...
    read_timeout_seconds: int | None = None
    """The timeout in seconds for the session."""

    read_transport_sse_timeout_seconds: int = 300
    """The timeout in seconds for the server connection."""
```

**Файл:** `.venv/lib/python3.13/site-packages/fast_agent/mcp/streamable_http_tracking.py`

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

### Суть

- Конфиг `MCPServerSettings` предоставляет поле `read_transport_sse_timeout_seconds`, которое в `mcp_connection_manager` **используется только для SSE‑транспорта** (`tracking_sse_client`).
- Для HTTP‑транспорта (`transport == "http"`) тот же параметр не пробрасывается в `tracking_streamablehttp_client`, то есть дефолтное значение `sse_read_timeout` (5 минут) не может быть изменено через конфиг.

### Идея патча

- Выровнять поведение HTTP и SSE‑транспорта:

```python
return tracking_streamablehttp_client(
    config.url,
    headers,
    sse_read_timeout=config.read_transport_sse_timeout_seconds,
    auth=oauth_auth,
    channel_hook=channel_hook,
)
```

- При таком изменении цепочка конфиг → транспорт → `httpx.Timeout(..., read=...)` будет одинаковой для обоих типов транспорта.

### Черновик issue (набросок)

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

## Наблюдение 2: разный дефолт sse_read_timeout в StreamableHTTPTransport и streamablehttp_client

**Файл:** `.venv/lib/python3.13/site-packages/mcp/client/streamable_http.py`

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

### Суть

- Конструктор `StreamableHTTPTransport` по умолчанию использует `sse_read_timeout=60*5` (5 минут).
- Обёртка `streamablehttp_client` по умолчанию задаёт `sse_read_timeout=60*30` (30 минут).
- Для пользователей, которые используют только `streamablehttp_client(...)`, это может быть нормой, но для тех, кто ожидает единый дефолт у класса и хелпера, поведение выглядит слегка неинтуитивным.

### Идея issue

- Обсудить в upstream, должен ли `streamablehttp_client` синхронизировать свой дефолт с `StreamableHTTPTransport`, либо это сознательное решение API (упрощённый helper с более длинным тайм-аутом).
- Это не блокирующая проблема, а скорее повод для прояснения и, возможно, выравнивания дефолтов.

---

Эти заметки — черновик. При подготовке реальных PR/issue стоит дополнить их конкретными версиями пакетов (`fast_agent`, `mcp`, `agents`) и небольшими примерами конфигурации (`fastagent.config.yaml`), воспроизводящими поведение.
