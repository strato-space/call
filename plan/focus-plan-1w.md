# Фокус-план (неделя): @AgentFab: + @PresentMaker + MCP: voice

## Обновления (Aug 18)

- Удалён раздел `Proxy note (Windows/PowerShell 7)` из `voice/README.md`.
- Исправлены депрекейшены времени в тестах (`datetime.utcnow()` → `datetime.now(UTC)`).
- Прогнан тестовый набор: `pytest -q` → 45 passed, 20 skipped.


## Обновления (Aug 19)

- MCP Voice: добавлены режимы `mode=[compact|full]` для:
  - `search(..., mode="compact")` — по умолчанию compact: оставляет ключевые поля (`_id, chat_id, session_type, is_active, created_at, is_messages_processed, last_message_timestamp, last_voice_timestamp, current_spreadsheet_file_id, is_finalized, done_at, to_finalize, finished_at, project_id, session_name, access_level, participants`) и нормализует `performer[]`; поле `processors` исключено.
  - `fetch(id, mode="full")` — по умолчанию full; `compact` возвращает те же отфильтрованные поля, что и `search` в compact.
- Обновлена документация `voice/README.md`: добавлены разделы про режимы `compact/full` для `search` и `fetch` + примеры запросов.


## 1) Главные цели спринта @AgentFab + @PresentMaker

### @PresentMaker
Также вторая главная цель: реализовать @PresentMaker как цепочку на LangChain (или LangGraph) с сохранением всех промежуточных артефактов на файловую систему через FS‑MCP. Требования: идемпотентность шагов, повторные прогоны улучшают результат и не затирают ранее сохранённые артефакты (reuse по путям), совместимость со Strato‑схемой выходов.

#### 1.1. Варианты использования 
- **Через WebUI VoiceBot**: внутри сессии по кнопке запускается агент/цепочка промптов; в репозитории создаются файлы конкретного прогона (промежуточные + финальные), на них агент опирается в ходе работы; итог показывается и в UI и в репозитории.
- **Через OpenCanvas**: при необходимости открываем промежуточные файлы и дорабатываем с любой стадии; каждый промпт запускается отдельно как самостоятельный агент.

#### 1.2. RMS‑first и источники
- На старте тестируем на RMS: выгружаем материалы в репозиторий; все MCP‑сервера смотрят в этот репозиторий.
- Позже расширяем MCP, чтобы права доступа и контекст подтягивались из VoiceBot/Telegram‑бота.

#### 1.3. Стандартизация формата
- Единый формат промптов и описаний агентов (Strato‑схема), пригодный и для движка цепочек, и для прямой работы с GPT.

#### 1.4. Первые агенты
- `@AgentFab` — по goal генерирует агента.
- `@PresentMaker` — конвейер презентации со всеми промежуточными артефактами на ФС.

#### 1.5. Ближайшие цели движка цепочек
- Узел‑валидатор результата: решает «повторить/исправить или продолжить». 
- Интеграция MCP во все шаги запросов.
- Исследование протокода a2a для общения промптов (склейка цепочек/агенты‑как‑узлы).
- Встройка веб‑приложения в VoiceBot (учесть особенности macOS).

### @AgentFab
По входу команды формата `@AgentName <input>` порождать и запускать **готового к применению агента** посредством @AgentFab, с автогенерацией карточки/конфига, совместимого с цепочками. `<input>` может быть строкой (тогда трактуется как `goal`) или ссылкой на файл (агент/заготовка) или блоком теста с атрибутами в стиле yaml. Дополнительно — довести инфраструктуру: @StratoFormatter, MCP к диалогам, артефакты на ФС для Presentation Maker и подключение Vector (RAG). Основа — принятый процесс/пайплайн и формат фреймворка промптов.

### MCP: voice
**Что:** обернуть клиент VoiceBot в MCP‑сервер для доступа к проектам/сессиям/персонам и экспорта материалов в репозиторий (RMS‑first). Основано на `voice/src/cli/voicebot_cli.py` и `voice/README.md`.

**MCP функции (snake_case) → Endpoints:**
- `projects()` → список проектов
- `search(name_substring?, project?, project_excluded?, since?, date?, limit?, mode?)` → список сессий (поиск)
  - `mode=compact|full` (по умолчанию `compact`)
- `fetch(id, mode?)` → детальная сессия (raw + транскрипт + вложения)
  - `mode=full|compact` (по умолчанию `full`)
- `persons()` → список персон
- `dump(project, project_excluded, dump_dir)` → обёртка над CLI для экспорта

**CLI (pwsh, примеры):**
- Список сессий (YAML):
  ```powershell
  python voice/src/cli/voicebot_cli.py list-sessions --project "Ural" --output yaml
  ```
- Деталь сессии:
  ```powershell
  python voice/src/cli/voicebot_cli.py get-session --id <SESSION_ID> --output yaml
  ```
- Экспорт диалогов в RMS (предпочтительно):
  ```powershell
  python voice/src/cli/voicebot_cli.py list-sessions --project "Ural" --project-excluded "Ural BortProvodnik" --dump rms/docs/voicebot
  ```

**FS‑пути:**
- `rms/docs/voicebot/` — дампы исходных диалогов/сессий (по проектам, с `index.md`).
- `rms/output/meetings/` — производные артефакты (summary, topics, action items и т.д.).

**DoD:**
- MCP‑методы возвращают структуры, совместимые с клиентом, и покрыты smoke‑тестами.
- Экспорт по команде выше создаёт группировку по проектам и индекс `index.md`.
- Интеграция с цепочками: PresentMaker/AgentFab читают материалы через FS‑MCP из каталогов выше.

## 2) Ключевые архитектурные решения (ADR)
- **Промпт ≈ Агент (единица поставки)**: каждый промпт — минимальный «агент» и может вызываться соло или в составе цепочки. Наследование общих атрибутов на уровне «агент-карты».   
- **Группа агентов** — группа агентов такая как @AgentFab тоже является Агентом и может вызываться соло или в составе цепочки.
- **Формат промптов** — схема Strato (принята [ ] указать ссылку на пример), «неразрушительная» трансформация через StratoFormater (сохраняет весь текст и атрибутику).   
- **Оркестрация** — в будущем LangChain/LangGraph для цепочек и replay; хранение всех артефактов как кода в Git. Начинаем с кода подсистемы call на OpenAI Agent SDK;  
- **Рабочая область агентов** — домашний каталог, сожержащий все репозиатрии, например репозиторий `prompt/` или repo `rms/` как общий «песок» артефактов (ядра, планы, слайды, логи прогона).   
- **MCP-подключения** — «голова и хвост»: MCP к источникам (диалоги/совещания) и к файловой системе для артефактов промежуточных шагов.   
- **RAG/Vector** — TODO подключаем простой векторный стор (наше «Vector»), используем как подсистему знаний для агентов и презентатора. 

## 3) Эпики и критерии готовности (DoD)

### E1. @AgentFab
**Что:** по команде `@AgentFab @AgentName <input>` создаём/разворачиваем посредством @AgentFab нового агента. Например команда `@AgentFab @UxDesigner создавать дизайн по спецификациям от @UxResearcher` порождает нового агента @UxDesigner в prompt/agent.  Здесь `<input>` = создавать дизайн по спецификациям от @UxResearcher: это строка‑goal. Так же доспустимы ссылка на файл с агентом/заготовкой; блок текста в формате Yaml.   
**Выходы:**
Сохранённый или обновлённый агент в каталоге `prompt/agents/`. См. подробности: `prompt/AgentFab/agent.md`.

### E2. @StratoFormater (неразрушительное форматирование)
**Что:** агент-форматер приводит сырой промпт к Strato-схеме, **ничего не теряя** (содержимое + атрибуты), умеет пакетно прогонять набор файлов.   
**DoD:** на наборе эталонных промптов дифф = только структурные перемещения/ключи схемы; ни один смысловой блок не утерян.

### E3. MCP VoiceBot: «Диалоги и совещания как источник»
**Что:** обернуть клиент VoiceBot в MCP для получения списка/деталей сессий и выгрузки материалов. Основано на `voice/README.md` и CLI `voice/src/cli/voicebot_cli.py`.   
**Endpoints (минимум):**
- `voicebot.projects.list()` → список проектов.  
- `voicebot.sessions.list(project?, project_excluded?)` → список сессий (диалогов) с атрибутами `id`, `session_name`, `project.name`, `created_at`, ...  
- `voicebot.sessions.get(id)` → детальная сессия (POST `/voicebot/session`).  
- `voicebot.persons.list()` → список персон.  
**CLI-интеграция (pwsh, по умолчанию):**
- Список сессий (TSV/JSON/YAML): `python voice/src/cli/voicebot_cli.py list-sessions --project "Ural"` (+ `--output json|yaml`).  
- Деталь сессии: `python voice/src/cli/voicebot_cli.py get-session --id <SESSION_ID>`.  
- Экспорт диалогов RMS (предпочтительно): `python voice/src/cli/voicebot_cli.py list-sessions --project "Ural" --project-excluded "Ural BortProvodnik" --dump rms/docs/voicebot`.  
**DoD:** MCP-методы возвращают структуры в соответствии с клиентом; экспорт в RMS по команде выше создаёт группировку по проектам и индекс `index.md` согласно README.

### E4. PresentMaker: цепочки + артефакты на ФС
**Что:** связать стадии презентера и сохранить промежуточные/финальные артефакты через FS‑MCP. Для стадии Strategymark используем контракт из `prompt/agents/PresentMaker/34-Strategymark.md`.   
**FS-макет (минимум):**
```text
[ ] todo
```
**Примечания:**
- Формат `10_strategymark.yaml` — строго по OUTPUT из `34-Strategymark.md`.
- Остальные стадии могут ссылаться на поля Strategymark (outline/blocks/visual).
**DoD:** один запуск формирует `10_strategymark.yaml` валидный схеме; повторный прогон учитывает предыдущие результаты (idempotent+improve) и не затирает артефакты.

### E5. Vector-проект (интеграция)
**Что:** подключить Vector как RAG-подсистему: индексирует диалоги/артефакты и отдаёт пассы в цепочки BA/Discovery/Presentation Maker.  
**DoD:** простой query-ответ на «известный факт из диалога» с указанием источника; индекс пополняется из `rms/output/` автоматически. 

## 4) Интерфейсы и форматы

### 4.1. Формат вызова агента (E1)
```text
@AgentName <input>
```
- `<input>` — либо произвольная строка (трактуется как `goal`), либо ссылка/путь на файл с агентом или его заготовкой.

### 4.2. Интерфейсы Call/MCP и FS (минимум)
- MCP Call: `call(agentName, input)` — выполняет агент с указанным входом.
- Резолверы в подсистеме Call:
  - `agentName` → `AgentObj`
    - Порядок поиска (creator → execution):
      1) `prompt/AgentFab/<AgentName>.yaml`
      2) `prompt/AgentFab/<AgentName>/agent.yaml`
      3) `prompt/agents/<AgentName>/agent.yaml`
  - `input` (string) → `inputObj`
  - `output` (string) → `outputObj`
- FS‑MCP: `fs.save(path, content)` / `fs.read(path)` / `fs.list(dir)` — все промежуточные/финальные артефакты под `rms/output/`.

## 5) Рабочая область RMS, репо и ссылки
Рабочая зона (артефакты): `rms/output/` (presentations/, tech-docs/, ui-docs/).  
- Дамп диалогов: `rms/docs/voicebot/` (экспорт через CLI, см. E3).  
- Карточки/агенты (execution): `prompt/agents/`; карточки‑создатели (creator): `prompt/AgentFab/`.  
- MCP-конфиг: `prompt/mcp_config.json`.

## 6) Риски и меры
- **Разнобой форматов** → единый StratoFormatter в обязательном шаге цепей.   
- **Слом контекста при реигре** → все промежуточные артефакты версионируем, линкуем в цепях по путям, не по «памяти».   
- **Инсталляция/аудио-роутинг на macOS** → фиксируем минимальный путь без виртуального кабеля; Windows — задокументировать Voicemeeter/аналог (минимум).  
- **Скорость индексации Vector** → на старте индексируем только нужные каталоги (`Output/presentations`, `Output/meetings`), батчим по расписанию. 

## 7) Ближайшие задачи (Action Items)
(формат как в meeting-structure) 

- **Антон** – Связать промпты Presentation Maker в одну цепь (E4) и прокинуть сохранение шагов через FS-MCP; путь как в макете. [Aug 18] [P1]  
- **Антон** – Подключить Vector к цепям (E5): индекс `prompt/Output/meetings` и `prompt/Output/presentations/<project>`. [Aug 19] [P1]  
- **Юра** – Доделать StratoFormater: режим «неразрушительный», пакетный прогон каталога `prompt/agents/` (E2). [Aug 17] [P1]   
- **Валерий П.** – Сверстать список «иностранных диалогов» для первичной загрузки; указать атрибуты: `{id,title,date,tags}` (E3). [Aug 16] [P2]  
- **Юра** – Реализовать MCP `meetings.list/get/summarize` по шаблону meeting-structure (E3). [Aug 19] [P1]   
- **Антон** – Добавить «однокнопочный» генератор AgentFab (CLI/МСР-вызов) с выводом card/prompt/config/tests (E1). [Aug 20] [P1]  
- **Юра** – Док по установке на macOS/Windows для аудио-роутинга (минимум), с вариантами без виртуального кабеля (E5 вспом.). [Aug 20] [P3]

## 8) Acceptance (общие чек-пункты)
- Агенты, созданные через `@AgentFab @AgentName <input>`, **запускаются в демо-цепочке** без ручных правок.  
- Presentation Maker пишет **все** промежуточные артефакты на диск и корректно улучшает результат при повторном прогонах.  
- MCP-диалоги доступны из любого диалога/сессии: `list → get → summarize` в нашем шаблоне.   
- StratoFormatter проходит эталонный набор без потерь контента/атрибутов. 

---

Хочешь — вынесу это в канвас как печатный бриф/roadmap и добавлю чек-листы под каждую задачу.