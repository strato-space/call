# MCP MediaGen

## 1. Goal & Scope
- [ ] Предоставить **два OpenAI-based инструмента генерации изображений**:
  - [x] `image_generation_gpt_image` — обёртка над OpenAI Images API `gpt-image-1`.
  - [ ] `image_generation_gpt` — обёртка над OpenAI Responses API `gpt-5.*` с встроенным `image_generation` tool.
- [x] Принимать текстовый `prompt`, вызывать соответствующий OpenAI endpoint и сохранять все бинарные результаты на файловую систему.
- [x] Каждый сгенерированный файл сохраняется под уникальным GUID-именем в одном каталоге `MEDIA_GEN_MCP_OUTPUT_DIR`.
- [ ] Инструмент возвращает только:
  - [ ] текстовый ответ модели (или список текстовых ответов),
  - [ ] одномерный массив либо локальных путей (`paths: [...]`), либо публичных URL (`urls: [...]`) — формат выбирается простым селектором.

## 2. Public Contract (Inputs / Outputs / Config)

### 2.1 Входы: `image_generation_gpt_image` (OpenAI Images API, `gpt-image-1`)

- `model: "gpt-image-1"` — выбор image‑модели.
- `prompt: string` — текстовое описание для генерации.
- `n: integer` — количество изображений.
- `size: "1024x1024" | "1024x1536" | "1536x1024"` — размер картинки.
- `quality: "low" | "medium" | "high"` — качество рендеринга.
- `background: "auto" | "transparent" | "opaque"` — режим фона.
- `output_mode: "paths" | "urls"` — локальный параметр инструмента; управляет тем, возвращает ли он `paths: [...]` (абсолютные пути) или `urls: [...]`. В OpenAI Images API **не уходит**.
- `mode: "compact" | "filtered" | "full"` — локальный параметр инструмента, определяющий вид текстового выхода (см. 2.3). В OpenAI Images API **не уходит**.

### 2.2 Входы: `image_generation_gpt` (OpenAI Responses API, `gpt-5.*` + `image_generation`)

- `model: "gpt-5" | "gpt-5.1" | ...` — выбор текстовой/мультимодальной модели.
- `input` / `messages` — текст/сообщения, в которых формулируем запрос и все пожелания к изображению.
- `tools: [{"type": "image_generation"}, ...]` — список tools, среди которых есть встроенный `image_generation`.
- `output_mode: "paths" | "urls"` — локальный параметр инструмента; управляет форматом ответа (`paths: [...]` или `urls: [...]`). В OpenAI Responses API (параметры `model`/`input`/`tools`) **не уходит** и используется только на стороне нашего рантайма.
- `mode: "compact" | "filtered" | "full"` — локальный параметр инструмента; управляет тем, как возвращается текст/структура ответа модели (см. ниже). В OpenAI Responses API не уходит.

### 2.3 Выход: `ImageDeliveryResult`

То, что видит вызывающая сторона (REST/MCP/CLI):

- `paths?: list[str]` — если для вызова инструмента был выбран `output_mode="paths"`, массив абсолютных путей в `MEDIA_GEN_MCP_OUTPUT_DIR`.
- `urls?: list[str]` — если выбран `output_mode="urls"`, массив публичных URL, собранных из `MEDIA_GEN_MCP_URL_PREFIX`.
- `output_text?: str` — человекочитаемый текст, в основе **как его вернула модель** (Responses API);
  - код image‑тула не дописывает и не переформулирует текст,
  - допускается только механическая замена встроенных base64/data‑URL/временных ссылок на финальные `paths`/`urls` в ответе.

Режим `mode` влияет на то, какие дополнительные поля, помимо `paths`/`urls` и `output_text`, возвращаются в envelope:

- `"compact"` (дефолт) — минимальный режим:
  - возвращается только `ImageDeliveryResult` (как описано выше);
  - полный raw-ответ LLM наружу **не** отдаётся.
- `"full"` — полный ответ LLM:
  - к `ImageDeliveryResult` добавляется поле `llm_response_raw` с оригинальным ответом LLM (Responses API) **без** каких-либо модификаций;
  - `output_text`, если присутствует, может дублировать основной текст из этого ответа.
- `"filtered"` — полный ответ LLM, очищенный от бинарных данных:
  - к `ImageDeliveryResult` добавляется поле `llm_response_filtered` с тем же форматом, что и raw-ответ LLM,
  - во всём этом объекте **все бинарные поля** (base64/data‑URL/временные ссылки/встроенные blob’ы) удаляются, а вместо них подставляются строковые значения с соответствующими `paths` или `urls`;
  - `output_text`, если присутствует, следует тем же правилом (только механическая замена ссылок на пути/URL).

### 2.4 Настройки окружения (filesystem + URL)

- [x] `MEDIA_GEN_MCP_OUTPUT_DIR` — базовый каталог для сохранения всех сгенерированных файлов, сюда складываются `paths`.
- [x] `MEDIA_GEN_MCP_URL_PREFIX` — опциональный URL‑префикс (например, `"https://media-gen.example.com/static"`), используется только если выбрана выдача URL.

Все файлы всегда сохраняются в `MEDIA_GEN_MCP_OUTPUT_DIR` под именами вида `"<guid>.<ext>"` (одноуровневый склад). Разница только в том, что наружу отдаётся либо список путей, либо список URL — это определяется полем `output_mode` в параметрах конкретного инструмента (`image_generation_gpt_image` или `image_generation_gpt`).

## 3. Core Modules Layout (call/image_tool/*)

### 3.1 image_tool/common.py

- Определения артефактов backend’ов: `ImageArtifact`, `ImageDeliveryResult`.
- Валидация параметров, которые реально уходят в OpenAI:
  - проверка размеров/значений `size`, `n`, `quality`, `background` для `gpt-image-1`.

### 3.2 image_tool/storage.py

- Работа с каталогом `MEDIA_GEN_MCP_OUTPUT_DIR`:
  - `ensure_output_dir(MEDIA_GEN_MCP_OUTPUT_DIR: Path) -> Path` — создаёт каталог при необходимости.
- Генерация GUID-имен файлов: `"<uuid4>.<ext>"`.
- Прямая запись артефактов backend’а в файлы внутри `MEDIA_GEN_MCP_OUTPUT_DIR` и возврат финальных путей.

### 3.3 image_tool/delivery.py

- Функция `deliver_artifacts(artifacts: list[ImageArtifact], output_mode: Literal["paths", "urls"]) -> ImageDeliveryResult`.
- Логика:
  - для каждого `ImageArtifact` выбрать расширение файла и сгенерировать GUID-имя;
  - через `storage` сохранить содержимое сразу в `MEDIA_GEN_MCP_OUTPUT_DIR`;
  - если `output_mode="paths"` — вернуть `paths` с абсолютными путями;
  - если `output_mode="urls"` — вернуть `urls`, собранные как `MEDIA_GEN_MCP_URL_PREFIX + <guid> + <ext>`;
  - собрать `ImageDeliveryResult` без дополнительного текста (кроме `output_text` от модели).

### 3.4 image_tool/backends/responses_backend.py

- Функция `generate_with_responses(request: ImageGenerationRequest, client: OpenAI) -> list[ImageArtifact]`.
- Использует **Responses API**:
  - `client.responses.create()` или специализированный image-эндпоинт, в т.ч. `model="gpt-5.*"`;
  - складывает полученные `b64_json` / file ids в `ImageArtifact` и сохраняет через `storage` напрямую в `MEDIA_GEN_MCP_OUTPUT_DIR`.

## 4. Subsystem Integration

### 4.1 call.lib

- Добавить высокоуровневую функцию `generate_image_api(...) -> dict`:
  - реализована в `call.lib.api` и оборачивает вызов core-модулей (`image_tool/*`).
  - принимает параметры, описанные в разделах 2.2/2.3 (`prompt`, OpenAI-поля для нужного backend’а, `output_mode`).
  - маппит исключения в стандартный envelope (`ok`, `error`, `error_code`, `description`, `attributes.images`).
- Обновить/расширить `RunnableConfig.tools`:
  - признать новые имена тулов: `image_generation_gpt` и `image_generation_gpt_image`.

### 4.2 app/call.py

- Зарегистрировать новые tool-фабрики в `get_tool_by_name`:
  - `"image_generation_gpt"` → обёртка над Responses API (`gpt-5.*` + `image_generation`).
  - `"image_generation_gpt_image"` → обёртка над Images API (`gpt-image-1`).

### 4.3 actions/main.py

- Добавить endpoint `/images`:
  - `POST /images` с JSON-пейлоадом вида:
    - `tool: "image_generation_gpt" | "image_generation_gpt_image"` — выбор backend’а.
    - `prompt: str`.
    - опциональные image-поля (`size`, `n`, `quality`, `background`) — только для `image_generation_gpt_image`.
    - `output_mode: "paths" | "urls"`.
  - Endpoint вызывает `generate_image_api` и возвращает стандартный envelope с `attributes.images` (см. 2.4).

### 4.4 mcp/server.py и mcp_config.yaml

- В MCP-сервере `call` добавить tool `generate_image`, который принимает тот же JSON, что и `/images`, и проксирует его в `generate_image_api`.
- В `mcp_config.yaml` в секции `call:` добавить `generate_image` в список tools с кратким описанием намерения ("генерация изображений и сохранение в FS/URL").

### 4.5 Точки интеграции в текущем коде

- **`call.lib.api`**
  - `RunnableConfig` и `build_runnable_instructions_config()` — формируют список tools, включая `image_generation_gpt` / `image_generation_gpt_image`.
  - `call()` / `call_async()` / `api_interpret_exec_payload()` — внешние entrypoint’ы (CLI, Actions, MCP, бот) используют `generate_image_api` по мере необходимости.

- **`call.app.call`**
  - Импортирует `image_generation_gpt` и `image_generation_gpt_image`, `function_tool`, `RunContextWrapper`.
  - `get_tool_by_name()` — возвращает соответствующий класс/функцию по имени.

- **`actions/main.py`**
  - `/call` и `/exec` как и прежде вызывают `api_call` / `api_call_async` и возвращают стандартный envelope.
  - `/images` вызывает `generate_image_api` напрямую.

- **`mcp/server.py`**
  - уже использует `call.lib.api`; новый MCP tool `generate_image` просто ещё один thin-wrapper вокруг `generate_image_api`.

## 6. Safety & Error Handling

- **Filesystem & URL:**
  - Все пути привязываются к `MEDIA_GEN_MCP_OUTPUT_DIR`.
  - Абсолютные пути допускаются только при `allow_custom_dirs=true` и белом списке префиксов.
- **Ошибки:**
  - Любая ошибка backend’а/маршрута не должна падать наружу исключением на верхнем уровне MCP/Actions/бота;
  - вместо этого возвращаем envelope с `ok=false`, `error`, `error_code`, `provider_code` (для OpenAI) и понятным `description`.

---

## 7. Testing Strategy

1. **Unit-тесты core-модулей**:
   - валидация DTO, безопасная нормализация путей и GUID-имен файлов.
2. **Backend-контрактные тесты** (с mock-OpenAI):
   - Images backend (`gpt-image-1`) и Responses backend (`gpt-5.*` + `image_generation`) для одинакового входного prompt возвращают согласованные `ImageArtifact`/пути при одинаковых настройках.
3. **Integration-тесты**:
   - вызов `/images` в Actions API.

## 8. Decisions

1. **Два отдельных инструмента вместо "дефолтного backend’а"**
   - `image_generation_gpt_image` и `image_generation_gpt` — это два разных инструмента (с разными именами тулов).
   - Вопрос "какой backend выбран по умолчанию" не ставится: вызывающая сторона явно выбирает нужный тул по имени.
2. **Без централизованной каталогизации артефактов**
   - Дополнительная таблица/реестр артефактов не вводится.
   - Для эксплуатации достаточно логов и возвращаемых путей/URL (`paths[]` / `urls[]`).
