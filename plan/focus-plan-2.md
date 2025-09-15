# Фокус-план (неделя): @AgentFab: + @PresentMaker + MCP: voice

## Обновления (Aug 18)

- [x] Удалён раздел `Proxy note (Windows/PowerShell 7)` из `voice/README.md`.
- [x] Исправлены депрекейшены времени в тестах (`datetime.utcnow()` → `datetime.now(UTC)`).
- [x] Прогнан тестовый набор: `pytest -q` → 45 passed, 20 skipped.

## Обновления (Aug 19)

- [x] MCP Voice: добавлены режимы `mode=[compact|full]` для:
  - [x] `search(..., mode="compact")` — по умолчанию compact: оставляет ключевые поля (`_id, chat_id, session_type, is_active, created_at, is_messages_processed, last_message_timestamp, last_voice_timestamp, current_spreadsheet_file_id, is_finalized, done_at, to_finalize, finished_at, project_id, session_name, access_level, participants`) и нормализует `performer[]`; поле `processors` исключено.
  - [x] `fetch(id, mode="full")` — по умолчанию full; `compact` возвращает те же отфильтрованные поля, что и `search` в compact.
- [x] Обновлена документация `voice/README.md`: добавлены разделы про режимы `compact/full` для `search` и `fetch` + примеры запросов.

## Обновления (Aug 31) - Упрощение Agent Discovery и Prompt Loading

- [x] Удалён код функции `load_prompt` полностью
- [x] Упрощена процедура поиска агентов - только по имени каталога (убрано сканирование реестров и метаданных)
- [x] Реализована рекурсивная загрузка файлов агента в `_seed_history` как текстовый список
- [x] Обработка случая 0 промптов - использование только agent.yaml как промпт
- [x] Обработка существующих промптов - использование первого промпта с объединением метаданных агента
- [x] Упрощена загрузка промптов - извлечение первого слова из списка prompts, попытка загрузки как .md или .yaml
- [x] Улучшена отчётность ошибок: JSON-ответ теперь содержит type, message, file, line и полный call stack

## Обновления (Sep 1) — Стандартизация памяти агентов (UxCreator)

- [x] UxCreator: все промпты переведены на плоский каталог `memories/` и Markdown‑выходы (`*.md`).
- [x] Добавлены явные `input.files` и `output.files` для чейнинга между шагами.
- [x] Framework (`AgentFab/framework.yaml`): стандарт памяти — `/home/strato-space/agents/<AgentName>/memories/`; контракт `files_contract` с `memories/*.md`.
- [x] AgentFab (`AgentFab/agent.yaml`): рекомендации обновлены на `files_contract.inputs/outputs`.
- [x] BusinessAnalyticAgent: путь памяти `/memories`, контракт `files_contract`, выходы ограничены `*.md`, диаграмма обновлена.
- [x] PromptSpec шаблон: заменён `context.artifacts` → `context.files`, добавлены `input.files` и `output.files` с `memories/*.md`.

## Executive summary (Sep 01–12)

- **Telegram routing fixed + HTML aligned to Bot API**: replies respect caller chat/thread; sanitizer centralized; only supported tags/attrs emitted (see `call/app/utils/html_sanitizer.py`).
- **CLI ergonomics**: argparse; `--name` and `--input` are optional; input‑only runs supported; `--echo` prints parsed args and discovered `AgentPath`.
- **API flexibility**: `call.lib.api.call_async(name: Optional[str], ...)` allows empty/None agent names; discovery is optional; agent_path=null when skipped.
- **MCP UX**: per‑instance Telegram logging (`MCPServerStdioHook`), cleaner progress bar, YAML args echoed to console and Telegram.
- **Voice packaging**: unified under `src/voice/`, deps and scripts normalized; nginx proxy/websocket routes modernized.

## Обновления (Sep 12) — Call/Telegram/CLI

- [x] Call: фикс роутинга Telegram — ответы идут в исходный чат/тред из апдейта; значения `chat_id/thread_id`, полученные из вызывающей стороны (бот/CLI), больше не перетираются YAML или `.env`.
- [x] HTML‑санитайзер для Telegram — приведён к списку поддерживаемых тегов/атрибутов по Bot API; заголовки `<h1..h6>` → жирный текст + перенос; списки разворачиваются в строки; поддержаны `tg-spoiler`, `tg-emoji`, `blockquote expandable`, `code class=language-*`.
- [x] Централизация санитайзера/обрезки: `html_sanitizer.prepare_telegram_html()` теперь единая точка входа; `telegram_text.telegram_prepare_html()` делегирует туда.
- [x] CLI Call: добавлены именованные аргументы `--name <AgentName>` и `--input <text...>`; оба опциональны. Можно запускать «input‑only» (без агента).
- [x] Library API: `call.lib.api.call_async(name: str | None, input_text: str, ...)` — поддерживает `name = "" | None`: агент создаётся с пустыми инструкциями, используется только входной текст; в ответе `agent_path = null`.
- [x] Документация обновлена: `call/README.md`, `call/CHANGELOG.md`.
- [x] Удалён алиас `Default` у `BusinessAnalyticAgent` (исключает случайные совпадения `@Default`).
 - [x] Welcome‑баннер: единый формат сообщения в Telegram перед стартом агента —
   - Заголовок: `🔌 <AgentName>` со ссылкой на `agent.yaml` в GitHub
   - Далее: текст ввода (plain), затем при наличии блоки `mcp: [...]`, `vs: [...]`, и в конце строка `model: ...`
   - Вывод баннера и диагностик теперь логируется только при `CALL_DEBUG=1` (см. ниже)
 - [x] CALL_DEBUG‑gating: добавлен `debug_print()`; все отладочные сообщения в `call/app/call.py`, `_log_update` в боте и периодические дампы asyncio включаются только при `CALL_DEBUG=1|true|yes|on` (stderr‑дамп также учитывает флаг; файловый — всегда включается при задании файла)
 - [x] Dev: добавлен `scripts/dev-venv.ps1` — активирует `.venv` и выставляет `DEBUG=1`, `CALL_DEBUG=1` для текущей сессии PowerShell

## Обновления (Sep 03) — Call/MCP/Discovery

- [x] Рефакторинг ядра Call: введён `AgentConfig` и фабрика `build_agent_config(name)`; упрощён пайплайн сборки агента.
- [x] MCP‑интеграция: класс‑обёртка `MCPServerStdioHook` ведёт отдельные сообщения в Telegram для каждого MCP‑инстанса; прогресс‑бар рендерится только при `thoughtNumber>0` и `totalThoughts>0`; остальные инструменты (fs и др.) выводят аргументы в YAML и в консоль, и в Telegram.
- [x] Диагностика CLI: режим `--echo` печатает разобранные аргументы и обнаруженный `AgentPath` без запуска пайплайна.
- [x] Исправления discovery/индексов: `_ensure_indices(rep)` — исправлена ошибка имени параметра; индексы генерируются из фактических директорий.
- [x] Тесты: `call/app/tests/test_discovery.py` покрывает кейсы специального разрешения `@AgentFab`, разрешение алиасов (в т.ч. исторически — `@Default` → BAAgent), прямой поиск по имени каталога.

## Обновления (Sep 01–02) — Prompt/Agents

- [x] BusinessAnalyticAgent: доработки карточки/промптов, расширение имён промптов; подготовка к Seq(u)ential Thinking итерациям для улучшений.
- [x] Репозиторий Prompt: контентные правки и заготовки (технические коммиты от 01 Sep).

## Обновления по дням (Sep 01–12)

- **2025-09-12**
  - call: упрощён `send_digest_notification` API; добавлены debug и тесты; исправлены кейсы с пустым текстом.
  - call: `call_async(name: Optional[str], …)` — поддержка пустого/None имени агента (input‑only режим); при пропуске discovery `agent_path = null`.
  - call: документация по CLI/Telegram (санитайзер/роутинг) обновлена.
  - prompt: добавлены заметки (эти обновления) в `focus-plan-2.md`; удалён алиас `Default` у BA.
  - prompt: StratoSummarizer2 — добавлен `input.files` для обязательной загрузки `prompt.yaml` и `structure.md` (по образцу BA: files_contract/input.files)
  - call: welcome‑баннер нового формата (plain‑секции), лог баннера и выбор модели — под `CALL_DEBUG`.
  - voice: изменений не зафиксировано в этот день.

  Детали:
  - `call/app/utils/html_sanitizer.py`: список тегов/атрибутов Telegram HTML; `<h1..h6>` → `<b>…</b>\n`; `<hr>` → `\n\n`; списки разворачиваются; `blockquote expandable`, `tg-emoji[emoji-id]`, `code[class~=language-*]` сохранены.
  - `call/app/utils/telegram_text.py`: делегирование в санитайзер; единый пайплайн подготовки HTML.
  - `call/lib/api.py`: `call_async(name: Optional[str], ...)` — пропуск discovery при пустом имени; нормализованное имя, корректный `agent_path`.
  - `call/app/call.py`: argparse‑CLI (`--name`, `--input`, `--echo`); отладочные строки `[DEBUG] call AgentName=...`, `[DEBUG] call input=...`.

- **2025-09-08**
  - voice: рефакторинг упаковки под `src/voice`; добавлены deps (mcp/anyio); консольные скрипты `voice.*`; подготовка к uvx‑импортам.
  - voice: обновлены nginx‑правила (WebRTC/Socket), нормализация переписывания URL, сохранены gzip/таймауты; удалён legacy HTTP‑блок.

  Детали:
  - `voice/src/`: единая структура пакета; импорты через `voice.*`.
  - `server/Nginx/` (или соответствующие конфиги): переписаны правила `/socket.io`, WebRTC; суб‑фильтры приводят бэкенд URL к публичному домену; длительные таймауты сохранены.

- **2025-09-03**
  - call: интеграция SelfReflection‑контроля; прогресс‑бар только при `thoughtNumber>0`; остальные MCP‑инструменты печатают YAML‑аргументы в консоль и Telegram.
  - call: введён `AgentConfig` + `build_agent_config`; рефакторинг пайплайна; `--echo` выводит разобранные args и `AgentPath` без запуска.
  - call: фиксы discovery/индексов (`_ensure_indices`), удаление лишних инструментов из снапшота; тесты discovery/алиасов.

  Детали:
  - `call/app/call.py`: классы/хелперы `AgentConfig`, `build_agent_config(...)`, `MCPServerStdioHook.call_tool` — YAML‑вывод аргументов; логика прогресс‑бара; `_ensure_indices(rep)` фикс параметра; `run_digest_pipeline()` — упрощён возврат `final_output`.
  - Тесты: `call/app/tests/test_discovery.py` — спец‑кейс `@AgentFab`, алиасы (в т.ч. ист. `@Default` → BA), фоллбек‑сканирование директорий.

- **2025-09-02**
  - call: перенос CHANGELOG; фиксы runpy; `post_run_git_push` — тихий no‑op без изменений; merge правок в `discover_agent_yaml` (приоритет AgentFab); тайминги/комментарии.

  Детали:
  - `call/CHANGELOG.md` создан; `__init__` загрузка смещена; упругий `post_run_git_push` (проверка `git status --porcelain -uno`).
  - `discover_agent_yaml(...)`: разрешение конфликтов, приоритет `AgentFab` над `agents`.

- **2025-09-01**
  - prompt: правки `BusinessAnalyticAgent` (карточка/промпты), расширение имён промптов; подготовка к итерациям Sequential Thinking; прочие контентные коммиты.

  Детали:
  - `prompt/AgentFab/BusinessAnalyticAgent/agent.yaml`: корректировки карточки и алиасов; расширены имена промптов; контентные улучшения для итераций.

## 1) Главные цели спринта @AgentFab + @PresentMaker

### @PresentMaker
- [ ] Реализовать @PresentMaker как цепочку на LangChain (или LangGraph) с сохранением всех промежуточных артефактов на файловую систему через FS‑MCP. 
- [ ] Требования: идемпотентность шагов, повторные прогоны улучшают результат и не затирают ранее сохранённые артефакты (reuse по путям)
- [ ] Совместимость со Strato‑схемой выходов

#### 1.1. Варианты использования 
- [ ] **Через WebUI VoiceBot**: внутри сессии по кнопке запускается агент/цепочка промптов; в репозитории создаются файлы конкретного прогона (промежуточные + финальные), на них агент опирается в ходе работы; итог показывается и в UI и в репозитории.
- [ ] **Через OpenCanvas**: при необходимости открываем промежуточные файлы и дорабатываем с любой стадии; каждый промпт запускается отдельно как самостоятельный агент.

#### 1.2. RMS‑first и источники
- [ ] На старте тестируем на RMS: выгружаем материалы в репозиторий; все MCP‑сервера смотрят в этот репозиторий.
- [ ] Позже расширяем MCP, чтобы права доступа и контекст подтягивались из VoiceBot/Telegram‑бота.

#### 1.3. Стандартизация формата
- [ ] Единый формат промптов и описаний агентов (Strato‑схема), пригодный и для движка цепочек, и для прямой работы с GPT.

#### 1.4. Первые агенты
- [ ] `@AgentFab` — по goal генерирует агента.
- [ ] `@PresentMaker` — конвейер презентации со всеми промежуточными артефактами на ФС.

#### 1.5. Ближайшие цели движка цепочек
- [ ] Узел‑валидатор результата: решает «повторить/исправить или продолжить». 
- [ ] Интеграция MCP во все шаги запросов.
- [ ] Исследование протокола a2a для общения промптов (склейка цепочек/агенты‑как‑узлы).
- [ ] Встройка веб‑приложения в VoiceBot (учесть особенности macOS).

### @AgentFab
- [ ] По входу команды формата `@AgentName <input>` порождать и запускать **готового к применению агента** посредством @AgentFab
- [ ] Автогенерация карточки/конфига, совместимого с цепочками
- [ ] `<input>` может быть строкой (тогда трактуется как `goal`) или ссылкой на файл (агент/заготовка) или блоком теста с атрибутами в стиле yaml
- [ ] Довести инфраструктуру: @StratoFormatter, MCP к диалогам, артефакты на ФС для Presentation Maker и подключение Vector (RAG)

### MCP: voice
- [x] **Что:** обернуть клиент VoiceBot в MCP‑сервер для доступа к проектам/сессиям/персонам и экспорта материалов в репозиторий (RMS‑first)

**MCP функции (snake_case) → Endpoints:**
- [x] `projects()` → список проектов
- [x] `search(name_substring?, project?, project_excluded?, since?, date?, limit?, mode?)` → список сессий (поиск)
  - [x] `mode=compact|full` (по умолчанию `compact`)
- [x] `fetch(id, mode?)` → детальная сессия (raw + транскрипт + вложения)
  - [x] `mode=full|compact` (по умолчанию `full`)
- [x] `persons()` → список персон
- [ ] `dump(project, project_excluded, dump_dir)` → обёртка над CLI для экспорта

**DoD:**
- [x] MCP‑методы возвращают структуры, совместимые с клиентом, и покрыты smoke‑тестами.
- [ ] Экспорт по команде выше создаёт группировку по проектам и индекс `index.md`.
- [ ] Интеграция с цепочками: PresentMaker/AgentFab читают материалы через FS‑MCP из каталогов выше.

## 2) Ключевые архитектурные решения (ADR)
- [x] **Промпт ≈ Агент (единица поставки)**: каждый промпт — минимальный «агент» и может вызываться соло или в составе цепочки. Наследование общих атрибутов на уровне «агент-карты».   
- [ ] **Группа агентов** — группа агентов такая как @AgentFab тоже является Агентом и может вызываться соло или в составе цепочки.
- [ ] **Формат промптов** — схема Strato (принята [ ] указать ссылку на пример), «неразрушительная» трансформация через StratoFormater (сохраняет весь текст и атрибутику).   
- [x] **Оркестрация** — в будущем LangChain/LangGraph для цепочек и replay; хранение всех артефактов как кода в Git. Начинаем с кода подсистемы call на OpenAI Agent SDK;  
- [x] **Рабочая область агентов** — домашний каталог, содержащий все репозитории, например репозиторий `prompt/` или repo `rms/` как общий «песок» артефактов (ядра, планы, слайды, логи прогона).   
- [x] **MCP-подключения** — «голова и хвост»: MCP к источникам (диалоги/совещания) и к файловой системе для артефактов промежуточных шагов.   
- [ ] **RAG/Vector** — TODO подключаем простой векторный стор (наше «Vector»), используем как подсистему знаний для агентов и презентатора. 

## 3) Эпики и критерии готовности (DoD)

### E1. @AgentFab
- [ ] **Что:** по команде `@AgentFab @AgentName <input>` создаём/разворачиваем посредством @AgentFab нового агента
- [ ] **Выходы:** Сохранённый или обновлённый агент в каталоге `prompt/<Project>/<AgentName>/`

### E2. @StratoFormater (неразрушительное форматирование)
- [ ] **Что:** агент-форматер приводит сырой промпт к Strato-схеме, **ничего не теряя** (содержимое + атрибуты), умеет пакетно прогонять набор файлов
- [ ] **DoD:** на наборе эталонных промптов дифф = только структурные перемещения/ключи схемы; ни один смысловой блок не утерян

### E3. MCP VoiceBot: «Диалоги и совещания как источник»
- [x] **Что:** обернуть клиент VoiceBot в MCP для получения списка/деталей сессий и выгрузки материалов
- [x] **Endpoints (минимум):** `voicebot.projects.list()`, `voicebot.sessions.list()`, `voicebot.sessions.get()`, `voicebot.persons.list()`
- [ ] **DoD:** MCP-методы возвращают структуры в соответствии с клиентом; экспорт в RMS создаёт группировку по проектам и индекс `index.md`

### E4. PresentMaker: цепочки + артефакты на ФС
- [ ] **Что:** связать стадии презентера и сохранить промежуточные/финальные артефакты через FS‑MCP. Для стадии Strategymark используем контракт из `prompt/<Project>/PresentMaker/34-Strategymark.md`.   
валидный схеме; повторный прогон учитывает предыдущие результаты (idempotent+improve) и не затирает артефакты

### E5. Vector-проект (интеграция)
- [ ] **Что:** подключить Vector как RAG-подсистему: индексирует диалоги/артефакты и отдаёт пассы в цепочки BA/Discovery/Presentation Maker
- [ ] **DoD:** простой query-ответ на «известный факт из диалога» с указанием источника; индекс пополняется из `rms/output/` автоматически

## 4) Интерфейсы и форматы

### 4.1. Формат вызова агента (E1)
```text
@AgentName <input>
```
- [x] `<input>` — либо произвольная строка (трактуется как `goal`), либо ссылка/путь на файл с агентом или его заготовкой

### 4.2. Интерфейсы Call/MCP и FS (минимум)
- [x] MCP Call: `call(agentName, input)` — выполняет агент с указанным входом
- [x] Резолверы в подсистеме Call:
  - [x] `agentName` → `AgentObj` (упрощённый поиск только по имени каталога)
  - [x] `input` (string) → `inputObj`
  - [x] `output` (string) → `outputObj`
- [ ] FS‑MCP: `fs.save(path, content)` / `fs.read(path)` / `fs.list(dir)` — все промежуточные/финальные артефакты под `rms/output/`

## 7) Ближайшие задачи (Action Items)

### Завершённые задачи (Aug 31)
- [x] **Упрощение Agent Discovery** – Убрать сложную логику поиска агентов по реестрам и метаданным, оставить только поиск по имени каталога
- [x] **Удаление load_prompt** – Полностью удалить реализацию функции load_prompt
- [x] **Упрощение Prompt Loading** – Реализовать простую логику загрузки промптов: первое слово из списка, попытка загрузки как .md или .yaml
- [x] **Рекурсивная загрузка файлов** – Добавить все файлы каталога агента в seed_history как текстовый список
- [x] **Расширенная диагностика ошибок** – Добавить форматированный JSON с file:line и стеком вызовов; обновить README

### Активные задачи
- [ ] **Антон** – Связать промпты Presentation Maker в одну цепь (E4) и прокинуть сохранение шагов через FS-MCP; путь как в макете. [Aug 18] [P1]  
- [ ] **Антон** – Подключить Vector к цепям (E5): индекс `prompt/Output/meetings` и `prompt/Output/presentations/<project>`. [Aug 19] [P1]  
- [ ] **Юра** – Доделать StratoFormater: режим «неразрушительный», пакетный прогон каталога `prompt/<Project>/` (E2). [Aug 17] [P1]   
- [ ] **Валерий П.** – Сверстать список «иностранных диалогов» для первичной загрузки; указать атрибуты: `{id,title,date,tags}` (E3). [Aug 16] [P2]  
- [ ] **Юра** – Реализовать MCP `meetings.list/get/summarize` по шаблону meeting-structure (E3). [Aug 19] [P1]   
- [ ] **Антон** – Добавить «однокнопочный» генератор AgentFab (CLI/МСР-вызов) с выводом card/prompt/config/tests (E1). [Aug 20] [P1]  
- [ ] **Юра** – Док по установке на macOS/Windows для аудио-роутинга (минимум), с вариантами без виртуального кабеля (E5 вспом.). [Aug 20] [P3]

### Новые задачи (Aug 31)
- [ ] **Тестирование** – Написать интеграционные и unit тесты для упрощённой логики agent discovery и prompt loading
- [ ] **Документация** – Обновить документацию по новой логике поиска агентов и загрузки промптов
- [ ] **Валидация** – Протестировать работу с существующими агентами после упрощения логики

## 8) Acceptance (общие чек-пункты)
- [ ] Агенты, созданные через `@AgentFab @AgentName <input>`, **запускаются в демо-цепочке** без ручных правок  
- [ ] Presentation Maker пишет **все** промежуточные артефакты на диск и корректно улучшает результат при повторном прогонах  
- [x] MCP-диалоги доступны из любого диалога/сессии: `list → get → summarize` в нашем шаблоне   
- [ ] StratoFormatter проходит эталонный набор без потерь контента/атрибутов
- [x] Упрощённая логика поиска агентов работает корректно с существующими агентами
- [x] Промпты загружаются по упрощённой схеме (первое слово, .md/.yaml)

---

Хочешь — вынесу это в канвас как печатный бриф/roadmap и добавлю чек-листы под каждую задачу.
