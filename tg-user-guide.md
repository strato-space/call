# Telegram User Guide — Call / MCP

## Overview
- Unified bot for running agents and prompts from the Call repo index (`call/repo.db`).
- Names are case-sensitive. Use exact `Project`, `Agent`, `Prompt` names as listed by the index.
- Private chats accept plain text; group chats require explicit @-mentions.
- Source of truth for behavior: `call/README.md`.

## Quick Start
- In DM: send text or `@Target <input>`
- In groups: mention the bot, then `Target` or `@Target` in the same message
- List prompts: `/prompts`, `/prompts_ready`, `/prompts_draft` (with filters)
- Rebuild index: `/reload`

## Bot Commands
- `/reload`
  - Re-scan repos from `.env` (e.g., `repos=agent,prompt`) and rebuild the SQLite index.

- `/prompts`, `/prompts_ready`, `/prompts_draft`
  - List prompts from the index with flexible filters.
  - Ready/draft variants pre-apply `state=ready` / `state=draft`.

### Filters (apply to all three prompt commands)
- `--project <ProjectName>`
- `--agent <AgentName>`
- `--prompt <PromptName>`
- `--target <plain|pattern>`
- `--state ready|draft`
- Supports wildcards `*` across all filters.
- Key=value forms and `@Agent` shorthand are accepted.
- Filters are combined with AND.

Examples:
- `/prompts --project UxFab --agent DialogPostAnalysis --state ready`
- `/prompts_ready --project * --prompt 10* --target r:*`
- `/prompts_draft --project AgentFab --prompt 3*-*`

## How messages are parsed
- **Private DMs**
  - Plain text (no @) → run as input-only (equivalent to `/call <input>`)
  - `@Target <input>` → passed to library for resolution (priority: prompt > agent > project). Target must include the `@` prefix.
  - A single `@ <input>` → input-only (no target)
  - Leading `@ProjectNameBot` is allowed and stripped; if no explicit `@Target` follows, project bots fall back to the project orchestrator (`project.md`).
  - `@StratoSpaceAiBot` without a target launches "void" mode (no instructions, just user input). With `@Target`, it can execute any prompt/agent/project in the catalog.
  - Note: Bot layer does NOT pre-validate targets. Resolution delegated to `call_api.call_async()`.

- **Group chats**
  - Only messages that mention the bot handle explicitly are handled (either `@ProjectNameBot` or `@StratoSpaceAiBot`)
  - `@Target <input>` → passed to library for resolution (target must start with `@`)
  - `@ProjectNameBot <input>` → when no explicit `@Target` follows, project bots invoke their project orchestrator (`project.md`)
  - `@ProjectNameBot @Target <input>` behaves the same way (bot name stripped, `@Target` delegated)
  - `@StratoSpaceAiBot ...` → universal bot; falls back to void mode without `@Target`, otherwise executes the referenced card
  - `@ <input>` → input-only
  - Note: Unknown targets will trigger error from library (not silently ignored by bot)

## Target resolution and precedence
- When `target` is provided, precedence is:
  1) prompt
  2) exact project
  3) agent
  4) fuzzy/wildcard project
- Supported forms:
  - Unprefixed with wildcards: `Ux*` (tries prompt → agent → project)
  - Path-like: `path:project/agent/prompt`
    - `path:UxFab/DialogPostAnalysis/33-*`
    - `path:UxFab/DialogPostAnalysis`
    - `path:UxFab`

## Wildcard tokens in text
- Tokens like `@31-*` or `32-*` are resolved via the repo DB.
- First match per token is added to context as a file reference
  `{ type: "file", name, path, mutable: true }`.
- Multiple tokens are supported, duplicates are de-duplicated.
- Leading `@` and `.md/.markdown` suffixes are stripped automatically.

Examples:
- `@31-*` → adds one context file
- `31-* 32-*` → adds two context files

## MCP mapping (mcp-voicebot)
- Tools:
  - `agents(query?, include_aliases?, project_name?)`
  - `prompts(project?, agent?, prompt?, state?)`
  - `exec(payload: object)`
  - `reload()`
- Mapping:
  - `/reload` → `reload()`
  - `/prompts*` → `prompts()` with filters
  - Message with `@Target` and text → `exec(payload)` with `target/input/context`

### Exec payload contract (used by Actions/MCP)
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

## Attachments and context (Telegram)

- When you send a **photo with a caption** like `@Target make a collage` in a
  private chat, the bot resolves the Telegram file via `get_file()` and adds a
  `type: resource_link` item to `context` pointing at the
  `https://api.telegram.org/file/bot<token>/...` URL.
- When you **reply to a message with a PDF** and write
  `@Target summarize this`, the payload will contain a `resource_link` for that
  document (including `uri`, `mimeType` and Telegram `source` metadata with
  `chat_id`, `message_id`, and `direction`).

Example (simplified YAML excerpt):

```yaml
target: Target
input: make a collage
context:
  - type: resource_link
    uri: https://api.telegram.org/file/bot<token>/photos/file_0.jpg
    name: photo_<file_id>.jpg
    description: Telegram photo (1024x768; size=123456 bytes; mime=image/jpeg)
    source:
      type: telegram
      chat_id: -100123456789
      message_id: 30
      direction: input
    mimeType: image/jpeg
```

## Sessions & routing
- `session_id` формат: `chat` или `chat:thread` (например, `AgentName:-100123:10`).
- Если `session_id` передан, он приоритетный; библиотека разберёт `chat_id/thread_id` автоматически.
- Иначе используются `chat_id/thread_id` из обновления или окружения.

## Practical examples
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

## Useful environment variables
- `CALL_DEBUG=1` — подробные логи (в т.ч. решения парсинга с префиксом [bot])
- `CALL_LOG_JSON=1` — JSON-логи
- `CALL_LOG_FILE=logs/app.log` — запись логов в файл

## Troubleshooting
- Ошибки выбора:
  - `TOO_MANY_ROWS` — неоднозначный выбор (вернутся варианты)
  - `NO_DATA_FOUND` — ничего не найдено (уточните регистр/фильтры)
- Ограничения апстрима:
  - `REQUEST_FORBIDDEN` (403) — ограничение сервисов трейсинга/провайдера
- Включите `CALL_DEBUG=1` и посмотрите `[bot]`-строки, чтобы понять, как бот распознал сообщение.
