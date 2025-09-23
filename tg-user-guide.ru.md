# Call и Prompt Repo. AgentFab и Agents

Вызов AgentFab и созданных Агентов - Руководство пользователя Telegram

## /call — @AgentFab или @Agent

- `/call @AgentFab @31-*` — обработать промпты, совпадающие с шаблоном `31-*`, через AgentFab.
- `/call @AiNewsAggr` — вызвать готового агента `AiNewsAggr`
- `/call @AiNewsAggr Новости Apple` — вызвать готового агента `AiNewsAggr` с входом `Новости Apple`

## Списки промптов в Prompt Repo

- `/prompts_draft`  — список промптов в статусе draft со ссылками на githib 
- `/prompts_ready`  — список промптов в статусе ready со ссылками на githib
- [Prompt Repo: strato-space/prompt](https://github.com/strato-space/prompt)

## Обновления индекса промтов и контекнсых боксов в боте

- Перестроить индекс: `/reload`

## Как создать новый промпт (через AgentFab)

- Пример:
  - `/call @AgentFab Создай промпт goal: подготовка повестки daily id:301-Daily`
- AgentFab интерпретирует задачу, создаёт карточку промпта в Prompt Repo и добавляет её в индекс после `/reload`.

## Как создать контекстный бокс (Content Box)

- Сейчас контекстные боксы создаются созданием папки в Prompt Repository:
  - [AgentFab/ContentBoxes](https://github.com/strato-space/prompt/tree/main/AgentFab/ContentBoxes)
- Создайте новую подпапку с понятным именем и поместите внутрь необходимые материалы (md, txt, csv и т.д.). 
- После обновления github выполните `/reload`.

## Как использовать контексный бокс

- в input в любом месте текста указать context-box: @BusinessAnalytycAgent 

## Как изменить состав или инстукции AgentFab

- [Карточка AgentFab в Prompt Repo](https://github.com/strato-space/prompt/blob/main/AgentFab/AgentFab.md)
- [Карточка StratoFormatter](https://github.com/strato-space/prompt/blob/main/AgentFab/StratoFormatter.md)
- [Конексный бокс BusinessAnalyticAgent](https://github.com/strato-space/prompt/tree/main/AgentFab/ContentBoxes/DialogOnlineAnalysis/content)
- [Промпт 49-BusinessagAntanalytic](https://github.com/strato-space/prompt/blob/main/draft/49-BusinessagAntanalytic.md)
- [Промпт 50-Discoveryagent.md](https://github.com/strato-space/prompt/blob/main/draft/50-Discoveryagent.md)
- [Выключенный SelfReflection](https://github.com/strato-space/prompt/blob/main/AgentFab/SelfReflection/agent.md)


## Как добавить новый пропмп в цепочку в AgentFab

- Отредактировать список prompt в разделе METADATA - см. [Карточка AgentFab в Prompt Repo](https://github.com/strato-space/prompt/blob/main/AgentFab/AgentFab.md)

## Особенности

- Имена чувствительны к регистру. Используйте точные названия `Project`, `Agent`, `Prompt`, как в Prompt Repo.
- Бот работае в личных сообщениях и в группах с указанием команды /call.
- В процессе работы @AgentFab бот выводит помежуточные шаги, вызовы MCP Filesystem, MCP Sequential Thinking, MCP Voicebot, вызовы дочених агентов в ту же группу где вызывана команда /call
- Бот сохранияет истрию переписки даже при перезапуске, и позволят строить длительные диалоги с агентами и промптами. 


## Быстрый старт

- В личке: отправьте текст или `@Target <ввод>`
- В группах: упомяните бота и укажите `Target` или `@Target` в том же сообщении
- Список промптов: `/prompts`, `/prompts_ready`, `/prompts_draft` (с фильтрами)
- Перестроить индекс: `/reload`

## Бот и назначение

- Бот: [@StratoSpaceAiBot](https://t.me/StratoSpaceAiBot)
- Предназначен для запуска `AgentFab`, готовых агентов и промптов из Prompt Repository.


## Команды бота

- `/reload`
  - Пересканировать репозитории из `.env` (например, `repos=agent,prompt`) и пересобрать SQLite‑индекс.

- `/prompts_ready`, `/prompts_draft`
  - Выводят список промптов из индекса с гибкими фильтрами.
  - Варианты ready/draft заранее применяют `state=ready` / `state=draft`.

### Дополниельные фильтры (доступны во всех трёх командах)

- `--project <ProjectName>`
- `--agent <AgentName>`
- `--prompt <PromptName>`
- `--target <plain|pattern>`
- `--state ready|draft`
- Поддерживаются подстановки `*` во всех фильтрах.
- Принимаются формы key=value и сокращение `@Agent`.
- Фильтры объединяются по правилу AND.

Примеры:

- `/prompts --project UxFab --agent DialogPostAnalysis --state ready`
- `/prompts_ready --project * --prompt 10* --target r:*`
- `/prompts_draft --project AgentFab --prompt 3*-*`

# Документация разрабочка 

## Как парсятся сообщения

- __Личные сообщения__
  - Простой текст (без @) → запускается как input‑only (аналог `/call <input>`)
  - `@Target <input>` → выполняется, если Target существует (приоритет: prompt > agent > project)
  - Одиночное `@ <input>` → трактуется как input‑only (без target)
  - Начальный `@BotName` допускается и отбрасывается

- __Групповые чаты__
  - Обрабатываются только сообщения с явным @‑упоминанием
  - `@Target <input>` выполняется только при валидном Target
  - `@BotName Target <input>` ведёт себя так же; если `Target` невалиден → input‑only
  - `@ <input>` → input‑only

## Резолвинг цели и приоритеты

- Если указан `target`, приоритет такой:
  1) prompt
  2) точное совпадение project
  3) agent
  4) нечёткое/шаблонное совпадение project
- Поддерживаемые формы:
  - Непрефиксированная с подстановками: `Ux*` (по попытке: prompt → agent → project)
  - Похоже на путь: `path:project/agent/prompt`
    - `path:UxFab/DialogPostAnalysis/33-*`
    - `path:UxFab/DialogPostAnalysis`
    - `path:UxFab`

## Шаблоны (wildcards) в тексте

- Токены вида `@31-*` или `32-*` резолвятся через БД репозитория.
- Первая найденная карточка по каждому токену добавляется в контекст как файловая ссылка
  `{ type: "file", name, path, mutable: true }`.
- Несколько токенов поддерживаются; дубликаты удаляются.
- Ведущий `@` и суффиксы `.md/.markdown` автоматически убираются.

Примеры:

- `@31-*` → добавит один контекстный файл
- `31-* 32-*` → добавит два контекстных файла

## Соответствие MCP (mcp-voicebot)

- Инструменты:
  - `agents(query?, include_aliases?, project_name?)`
  - `prompts(project?, agent?, prompt?, state?)`
  - `exec(payload: object)`
  - `reload()`
- Маппинг:
  - `/reload` → `reload()`
  - `/prompts*` → `prompts()` с переданными фильтрами
  - Сообщение с `@Target` и текстом → `exec(payload)` с полями `target/input/context`

### Контракт exec payload (используется в Actions/MCP)

```json
{
  "project": "?",
  "agent": "?",
  "prompt": "?",
  "target": "?",
  "context": [],
  "echo": false,
  "session_id": "chat[:thread]"
}
```

- Ровно один из `project|agent|prompt|target` должен быть указан.
- В Telegram чаще всего используется `target`; остальное формируется парсером.

## ToDo примеры curl команд 

- todo

## Todo Actions

## Todo MCP

## Сессии и маршрутизация

- Формат `session_id`: `chat` или `chat:thread` (например, `AgentName:-100123:10`).
- Если `session_id` передан, он приоритетный; библиотека сама разберёт `chat_id/thread_id`.
- Иначе используются `chat_id/thread_id` из обновления или окружения.

## Практические примеры

- Списки:
  - `/prompts --project AgentFab --format text`
  - `/prompts_ready --project * --agent * --prompt 3*-*`
- Запуски:
  - `@AgentFab "Сделай обзор по @11-ExtractUserPain"`
  - `@DialogPostAnalysis "Проанализируй https://docs.google.com/document/d/FILE_ID/edit"`
  - `@path:UxFab/DialogPostAnalysis/33-* "Выполни пайплайн 33-*"`
- Группы:
  - `@BotName AgentFab "Собери дайджест по 31-* 32-*"`
  - `@BotName @AiNewsAggr "Сделай краткую сводку"`

## Полезные переменные окружения

- `CALL_DEBUG=1` — подробные логи (в т.ч. решения парсинга с префиксом [bot])
- `CALL_LOG_JSON=1` — JSON‑логи
- `CALL_LOG_FILE=logs/app.log` — запись логов в файл

## Диагностика

- Ошибки выбора:
  - `TOO_MANY_ROWS` — неоднозначный выбор (вернутся варианты)
  - `NO_DATA_FOUND` — ничего не найдено (уточните регистр/фильтры)
- Ограничения апстрима:
  - `REQUEST_FORBIDDEN` (403) — ограничения провайдера/трассинга
- Включите `CALL_DEBUG=1` и смотрите строки `[bot]`, чтобы понять, как бот распознал сообщение.

## Ссылки

- Источник истины по поведению: `call/README.md`.