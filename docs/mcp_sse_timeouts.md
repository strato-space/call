# MCP SSE / Streamable HTTP timeouts

Этот документ фиксирует:

- Исторические **локальные патчи** в `.venv`, которыми мы поднимали
  `sse_read_timeout` до 30 минут (разделы 1–6).
- Текущее состояние runtime `call`, в котором мы **вернулись к дефолтным
  5‑минутным** тайм-аутам и решили проблему обрывов на стороне `call` через
  авто‑переинициализацию MCP.

Актуальная стратегия (2025‑11‑19):

- Во всех MCP‑клиентах (`mcp.client.sse`, `mcp.client.streamable_http`,
  `agents.mcp.server`, `fast_agent`) используется `sse_read_timeout = 60 * 5`
  (5 минут) — то есть патчи на 30 минут **откатаны**.
- При `anyio.ClosedResourceError`, `httpx.ReadTimeout` / `ConnectTimeout` или
  `McpError` во время запуска агента `call` считает MCP‑сессию «мёртвой» и
  выполняет:
  - `cleanup_mcp_servers()`
  - `wait_for_mcp_init(timeout=120.0)`
  - один повторный запуск `Runner.run`.
- Таким образом, обрыв SSE после нескольких минут простоя рассматривается как
  нормальный сигнал «нужно пересоздать сессию», а не как фатальная ошибка.

Разделы 1–6 ниже описывают патчи, которые **могут быть переиспользованы**,
если в будущем снова понадобится 30‑минутный тайм-аут.

> NOTE: all paths below are **inside the virtualenv** used by `call`.
> After reinstalling deps, these files will be overwritten.

---

## 1. agents: increase default SSE read timeout for MCP servers (исторический патч)

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

Effect (когда патч был активен):

- Если `sse_read_timeout` не задавался явно в MCP server params, `agents`
  использовал **30 минут** вместо **5 минут** для SSE / Streamable HTTP.

В текущем состоянии runtime `call` эти изменения **откатаны**, код библиотек
работает с дефолтом `60 * 5`.

---

## 2. mcp.client: StreamableHTTP defaults (исторический патч)

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

Effect (когда патч был активен): транспорт использует **30 минут** как
дефолтный SSE read timeout. В текущей установке `call` это изменение
откатано, и используется дефолт библиотеки (5 минут).

---

## 3. mcp.client: SessionGroup helper defaults (исторический патч)

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

Effect (когда патч был активен): любой код, использующий `ClientSessionGroup`
без явного `sse_read_timeout`, наследовал 30‑минутный SSE тайм-аут.

Сейчас дефолт снова 5 минут.

---

## 4. fast_agent: SSE tracking transports (исторический патч)

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

Effect (когда патч был активен): `fast_agent` tracking‑транспорты следовали
30‑минутному SSE тайм‑ауту. Сейчас используется значение по умолчанию (5 минут).

---

## 5. fast_agent: config-level default for SSE read timeout (исторический патч)

File:

- `.venv/lib/python3.13/site-packages/fast_agent/config.py`

Changes:

- In `MCPServerSettings`:

  ```python
-  read_transport_sse_timeout_seconds: int = 300
+  read_transport_sse_timeout_seconds: int = 1800
   """The timeout in seconds for the server connection."""
  ```

Effect (когда патч был активен): при чтении `fastagent.config.yaml` значение по
умолчанию становилось 1800 секунд (30 минут). В текущей конфигурации `call`
дефолт снова 300 секунд (5 минут).

---

## 6. mcp.client.sse: base SSE client default (исторический патч)

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

Effect (когда патч был активен): low‑level SSE client использовал 30‑минутный
тайм-аут по умолчанию. Сейчас дефолт возвращён к 5 минутам.

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

   - Если вы где-то в своём коде вручную создаёте `MCPServerSse(...)` или
     `MCPServerStreamableHttp(...)`, можно **передать `sse_read_timeout` в params**
     и управлять тайм-аутом без патча библиотек.

2. **mcp.client (Python API)**

   - `mcp.client.sse.sse_client(..., sse_read_timeout=...)`
   - `mcp.client.streamable_http.streamablehttp_client(..., sse_read_timeout=...)`

   Любой собственный клиент MCP может задавать этот параметр явно.

3. **fast_agent config (`fastagent.config.yaml`)**

   - В `fast_agent.config.Settings.mcp.servers.<name>` можно указать:

     ```yaml
     mcp:
       servers:
         call:
           read_transport_sse_timeout_seconds: 1800  # или другое значение
     ```

   - Это значение используется в `fast_agent.mcp.mcp_connection_manager` при
     создании `tracking_sse_client` / `tracking_streamablehttp_client` и
     передаётся как `sse_read_timeout`.

### Почему мы всё равно патчим `.venv`

Текущий `call` runtime и массовые запуски `AgentFab` используют стек
`agents` + `mcp.client`, где не было удобного конфиг-уровня для
` sse_read_timeout` именно для удалённого `call-mcp`.

Чтобы **быстро снять ограничение в ~5 минут**, мы подняли дефолты на всех
слоях, задействованных в трассе:

- `agents.mcp.server` → MCPServerSse / MCPServerStreamableHttp
- `mcp.client.sse` / `mcp.client.streamable_http`
- `mcp.client.session_group`
- `fast_agent` tracking-транспорты и config default

При будущих рефакторингах можно добавить явную прокладку
`read_transport_sse_timeout_seconds` / `sse_read_timeout` из конфигов
(например, из `fastagent.config.yaml` или `call/mcp_config.yaml`) и после
этого отказаться от `site-packages` патчей.

---

## 8. Как повторить после обновления библиотек

1. Обновите зависимости (`pip install -r requirements.txt` и т.п.).
2. В активированном `.venv` выполните быстрый поиск:

   ```bash
   rg "sse_read_timeout" "$(python -c 'import sys,site; print(site.getsitepackages()[0])')"
   ```

3. Сверьтесь с этим документом и снова примените изменения во всех файлах
   из разделов 1–6, возвращая `60 * 5` → `60 * 30` и `300` → `1800` там,
   где это описано.

После этого долгие сессии `call-mcp`/AgentFab снова не должны падать по
`httpx.ReadTimeout` через 5 минут простоя.

---

## 9. OpenAI backend 5-минутный лимит (Responses / Agents)

Этот документ в основном описывает **клиентские** тайм-ауты чтения SSE и
Streamable HTTP. В ходе дальнейшего исследования поведения AgentFab было
обнаружено отдельное ограничение примерно в **5 минут на стороне backend OpenAI**,
которое влияет на долгие задачи, выполняемые синхронно через Responses / Agents
API.

Ключевые наблюдения (конец 2025):

- Официальный Python SDK OpenAI использует дефолтный HTTP-тайм-аут **10 минут**
  (`DEFAULT_TIMEOUT = Timeout(600, connect=5.0)`), поэтому он **не** является
  источником 5-минутного обрыва.
- Наши слои MCP и `fast_agent` теперь используют **30 минут** как дефолтный
  `sse_read_timeout` (см. разделы 1–7 выше).
- Тем не менее, при использовании AgentFab с OpenAI Agents SDK / Responses API
  в **блокирующем режиме** (без `background: true`) сложные задачи по-прежнему
  обрываются примерно через 5 минут с ошибкой A2A `TaskNotFoundError`
  (`code = -32001`), которая затем пробрасывается через MCP.
- Ошибка и отсутствие соответствующего лимита в локальных библиотеках указывают
  на существование **серверного TTL порядка 5 минут** для синхронных задач
  Responses / Agents.
- Этот TTL сейчас **не конфигурируется** с нашей стороны; рекомендованный способ
  обхода со стороны OpenAI — запускать долгие задачи с `background: true` и
  далее опрашивать `/responses/{id}`.

Практические последствия для `call` / AgentFab:

- Локальные настройки тайм-аутов SSE на 30 минут гарантируют, что клиенты
  готовы ждать достаточно долго.
- Если задача AgentFab всё равно умирает примерно через 5 минут с
  `TaskNotFoundError(-32001)` или похожей ошибкой, наиболее вероятная причина —
  именно этот **backend-лимит**, а не тайм-аут MCP/SSE.
- Чтобы избежать этого, слой Agents должен:
  - создавать Responses с `background: true`,
  - сохранять `response.id` и
362→  - забирать результат через `responses.retrieve(...)` (polling или
363→    продолжение стрима).

Этот раздел носит информационный характер; никаких дополнительных изменений в
MCP SSE-стеке для учёта 5-минутного backend-лимита OpenAI не требуется.

---

## 10. Кеш агентов и «мёртвые» MCP-сессии

- В `call/app/call.py` есть небольшой кеш агентов `AGENT_CACHE`, который хранит экземпляры `Agent` по имени (в том числе под-агенты, используемые как tools вроде `PM-4-DialogTaskSummary`). Это позволяет не создавать агента заново на каждом вызове.
- После внедрения авто‑reinit MCP (последовательность `cleanup_mcp_servers()` → `wait_for_mcp_init()` → повторный `Runner.run`) выяснилось, что один и тот же кешированный агент может продолжать ссылаться на **старые** экземпляры MCP‑серверов, у которых уже вызван `cleanup()` и `session = None` (типичный симптом: `UserError("Server not initialized. Make sure you call connect() first.")` от `agents.mcp.server`).
- Чтобы этого избежать, фабрика `get_or_create_agent()` теперь при каждом повторном использовании кешированного агента **обновляет его поле `mcp_servers`** свежим списком MCP‑серверов, собранным для текущего запуска.
- В связке с авто‑reinit это означает:
  - при обрыве SSE / истечении тайм-аута мы пересоздаём MCP‑сессии;
  - при следующем запуске под‑агент из кеша получает новый список `mcp_servers` и больше не держит «мёртвые» подключения (в т.ч. к удалённому `gsh`).
- Если вы видите повторяющиеся ошибки `"Server not initialized"` на втором и последующих вызовах после простоя, имеет смысл:
  - проверить, что логика обновления `agent.mcp_servers` при reuse действительно присутствует;
  - затем уже анализировать сетевые тайм-ауты и поведение удалённого MCP‑сервера по частям этого документа.
