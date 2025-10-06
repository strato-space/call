# SRS: Нормализация обработки ошибок и централизованного логирования

**Файл:** `srs-logging-refactoring.md`  
**Статус:** Draft → Ready (после M3)  
**Владелец:** @slava (Strato Space)  
**Затронутые пакеты:** `lib/`, `cli/`, `actions/`, `app/`, `telegram_bot/`, `mcp/`

---

## 1. Область действия и цели

Нужно унифицировать **формат ошибок** и **поведение логирования** во всех входных точках (CLI, FastAPI Actions, MCP, библиотека, Telegram bot) и во внутренних хелперах. Цель — единая ошибка‑обёртка, предсказуемые поля, отсутствие «сырого» `print`, централизованные логеры и воспроизводимая диагностика в проде и тестах. Основания и действующие принципы зафиксированы в репозитории (KISS, явные отказные пути, «логируй каждое исключение»). fileciteturn0file0

---

## 2. Термины и ссылки

- **Конверт ошибки (error envelope)** — стандартный JSON с полями `ok=false`, вложенным объектом `error` и зеркальным `description`. Схема и порядок полей описаны в документации репозитория. fileciteturn0file1
- **Фасад логирования** — `call.lib.logging` c `configure_logging`, `get_logger`, `debug_print`, переключатели `CALL_DEBUG`, `CALL_LOG_JSON` и флаг `--json-logs` в CLI. fileciteturn0file1
- **Дизайн‑принципы** — KISS, SOLID/DI, явные ошибки, наблюдаемость. fileciteturn0file0

---

## 3. Наблюдаемые проблемы (сводка)

1) Части кода собирают ошибки вручную (`{ok:false,...}`), игнорируя общий конструктор.  
2) Входные точки (CLI, HTTP middleware) используют «сырой» `print`, что обходит конфигурацию логов и JSON‑режим.  
3) Исключения часто подавляются без записи в лог, что делает инциденты невоспроизводимыми.  
4) Вокруг `debug_print` встречаются лишние `try/except`, создающие «чёрные дыры».  
5) Документация и тесты частично фиксируют желаемое поведение, но нет регресс‑защит (линт/тест) на появление `print` и расхождение схемы ошибок. fileciteturn0file0

---

## 4. Требования

### 4.1 Функциональные
- Экспортировать **публичные** хелперы ошибки и применять их повсеместно.
- Заменить все «ручные» ошибки и HTTP/CLI ответы на вызовы общего хелпера.
- Стандартизировать CLI‑обработку ошибок, ранний вызов `configure_logging` при `--debug/--json-logs`. fileciteturn0file1
- Перед каждым возвратом конверта фиксировать исключение/контекст в лог.
- Удалить «сырые» `print` в рантайме; использовать `get_logger`/`debug_print`. fileciteturn0file1

### 4.2 Нефункциональные
- **Наблюдаемость:** каждое исключение и I/O‑ошибка — в лог (включая продолжение работы). fileciteturn0file0
- **Стабильность схемы:** порядок и наличие полей в ошибке соответствуют документации. fileciteturn0file1
- **Совместимость:** сохранить коды/exit‑codes, обновить места, где читали устаревшее поле `code` (верхнего уровня). fileciteturn0file1

---

## 5. Проектные решения (выбор лучших положений из 4 планов)

### 5.1 Канонические конструкторы ошибок
- Поднять приватные `_error_payload` / `_error_payload_event` до публичных API:  
  `error_response(...)`, `event_error_response(...)` (названия примерные).  
- Гарантировать включение: `error`, `error.code`, `error.message`, `error_code`, `description`; опционально — `type`, `param`, `provider_code`, `agent`, `project`, `details`.  
- Поддержать упрощённые входы (строка/Exception) и перенос контекста (ids, опции).  
- Докстринги с примерами и ссылкой на схему. fileciteturn0file1

### 5.2 Повсеместное применение
- `lib/` (например, `reload`, `clear_session`, интерпретация `exec`‑payload) — вместо ad‑hoc словарей вызываем `error_response`.  
- `actions/` — все `JSONResponse` по ошибкам строятся через хелпер, HTTP‑статус синхронизирован с `error.code`.  
- `mcp/` — ранние выходы и валидация используют тот же хелпер (или требуют прокидывания уже готового конверта из `lib`).  
- `cli/` — вводится `_emit_error(error_response)`; все `except` печатают его через stderr‑безопасный вывод, сохраняя `exit 1`. fileciteturn0file1

### 5.3 Единое логирование вместо `print`
- В `actions`‑middleware и других путях — `get_logger("<module>")` + `debug_print` для CALL_DEBUG‑трасс.  
- В `app/`, `agent_utils`, `telegraph_utils` — вместо `print` использовать `debug_print` (для шума) и `logger.warning/error` (для проблем).  
- Ранние инициализации: CLI при `--debug/--json-logs` вызывает `configure_logging` до первой печати. fileciteturn0file1

### 5.4 Логируй перед возвратом
- Перед возвратом `error_response` писать короткую строку: модуль, причина, ключевые поля (agent/project, счетчики и т.п.); при `CALL_DEBUG=1` — стек. fileciteturn0file0

### 5.5 Упростить вокруг `debug_print`
- Удалить обёртки `try/except: pass` вокруг `debug_print` — он уже безопасен; оставить единый стиль префиксов `[app]`, `[actions]`, `[repo.scan]`, `[bot]`. fileciteturn0file0

### 5.6 Расширение фасада логирования (минимализм + полезность)
- Добавить вспомогательные `log_exception(logger_name, msg, exc)` и контекст‑менеджер *log‑and‑suppress* — для единообразия сообщений и трасс. (Опционально; не ломает KISS.) fileciteturn0file0

### 5.7 Документация и регресс‑защита
- Обновить разделы **Error payload schema**, **Logging** и **CLI**; указать экспорт новых хелперов и запрет `print` в рантайме. fileciteturn0file1  
- Линт‑правило/тест, проваливающее CI при наличии `print(` вне whitelisted‑скриптов. fileciteturn0file0

---

## 6. Контракты и интерфейсы

### 6.1 Error Envelope (канонический)
Сервер/библиотека/CLI обязаны возвращать/печать единый конверт (порядок полей стабилен):  
`ok=false`, `error{ code, message, type?, param?, provider_code? }`, `error_code`, `description`, `agent?`, `project?`, `final_output`, `echo`, `session_id?`. fileciteturn0file1

### 6.2 Публичные хелперы ошибок
```python
def error_response(
    message: str | Exception,
    *,
    code: int | None = None,
    type: str | None = None,
    param: str | None = None,
    provider_code: str | None = None,
    agent: str | None = None,
    project: str | None = None,
    details: dict | None = None,
    echo: bool | None = None,
    session_id: str | None = None,
) -> dict: ...
```
- **Гарантии:** наличие `error` и зеркальной `description`; когда `message` — исключение, `code` и `type` маппятся на разумные значения, стек добавляется в лог при `CALL_DEBUG=1` (не в ответ).

Аналогично: `event_error_response(...)` для event‑каналов.

### 6.3 CLI
- Хелпер `_emit_error(payload: dict) -> None` печатает ровно конверт; `exit 1`. Логи — через `get_logger("cli")`. Опции `--debug`, `--json-logs` включают раннее `configure_logging`. fileciteturn0file1

### 6.4 Логирование
- Переключатели: `CALL_DEBUG`, `CALL_LOG_JSON`, CLI `--json-logs`, опциональный файл через `CALL_LOG_FILE`. fileciteturn0file1
- Требование: **никаких `print`** для диагностики в `app/`, `actions/`, `lib/`, `telegram_bot/`, `mcp/`. Пользовательский **нормальный вывод** CLI допускается, **ошибки** — только через конверт + stderr. fileciteturn0file0

---

## 7. Совместимость и миграция

- В документации уже зафиксировано, что «устаревшее верхнеуровневое поле `code` удалено; статус читается из `error.code`». Проверить внешние потребители и обновить их. fileciteturn0file1
- Сохранить `exit 1` в CLI при `ok:false`. fileciteturn0file1
- JSON‑порядок полей в конверте не менять. fileciteturn0file1

---

## 8. План внедрения (Milestones)

- **M1 — API**
  - Экспортировать `error_response`, `event_error_response`; докстринги; базовые тесты.
- **M2 — Adoption**
  - `lib/` и `actions/` перевод на хелперы; MCP ранние возвраты; CLI `_emit_error`.
- **M3 — Logging**
  - Удаление `print`; переход middleware/утилит на `get_logger`/`debug_print`; раннее `configure_logging` в CLI.
- **M4 — Exceptions**
  - Гарантия «логируй перед возвратом»; точечные сообщения с контекстом.
- **M5 — Docs & Tests & Lint**
  - Обновить README/AGENTS; добавить линт‑правило на `print(`; регресс‑тесты (CLI/Actions/Bot).
- **M6 — (Optional) Logging Helpers**
  - `log_exception` и контекст‑менеджер; покрытие тестами.

---

## 9. Критерии приёмки

- Все изменённые пути при сбое возвращают конверт **строго по схеме** (наличие `error`, зеркального `description`, валидных `error.code`/`error_code`). fileciteturn0file1  
- В `actions` и `cli` нет «ручных» словарей ошибок; покрыто тестами.  
- В `app/`, `actions/`, `lib/`, `telegram_bot/`, `mcp/` отсутствуют «сырые» `print` (провал линта при появлении). fileciteturn0file0  
- При `CALL_DEBUG=1` исключение логируется со стеком до возврата конверта; ответ не содержит стека.  
- CLI при `--json-logs` эмитит JSON‑логи; ошибки печатаются единообразно. fileciteturn0file1

---

## 10. Тест‑план (минимальный регресс)

- **Unit (lib):** хелперы ошибок — наличие полей; строка/Exception; опции (`provider_code`, `param`).  
- **Actions (FastAPI):** невалидные запросы к `/prompts`, `/exec` → конверт; статус = `error.code`; лог‑хук срабатывает. fileciteturn0file1  
- **CLI:** команды `list/models/call/reload/clear-session` — симулировать исключения и проверить `_emit_error` + `exit 1` + раннее логирование. fileciteturn0file1  
- **Bot:** целевые тесты на маршруты логирования (существующие тесты расширить).  
- **Lint:** правило «нет `print(`» по `app/`, `actions/`, `lib/` (whitelist для утилит‑скриптов). fileciteturn0file0

---

## 11. Политики кодирования (выдержка)

- KISS, явные отказные пути, «логируй каждое исключение и I/O‑ошибку». fileciteturn0file0  
- Использовать `call.lib.logging.debug_print` и структурированные конверты из `call.lib.api`. fileciteturn0file0

---

## 12. Риски и откат

- **Риск:** пропущенные места ручной сборки ошибок → **Митигируем** поиском по `{"ok": False` и `error_code` + тестами.  
- **Риск:** нарушения порядка полей → **Митигируем** snapshot‑тестами и статической проверкой структуры.  
- **Откат:** фича‑флаги не требуются; изменения обратимы на уровне API‑хелперов (стабильные сигнатуры).

---

## 13. Метрики успеха

- % путей, покрытых общим хелпером ошибок (цель: 100%).  
- Кол‑во «сырых» `print` в рантайме (цель: 0).  
- Время на диагностику инцидента (p50/p95) — снижение после M3.  
- Отсутствие регрессий по форматам CLI/HTTP (все контракты зелёные).

---

## 14. Приложение A — Пример конверта ошибки

```json
{
  "ok": false,
  "error": {
    "code": 400,
    "message": "Your input exceeds the context window of this model. Please adjust your input and try again.",
    "type": "invalid_request_error",
    "param": "input",
    "provider_code": "context_length_exceeded"
  },
  "error_code": 400,
  "description": "Your input exceeds the context window of this model. Please adjust your input and try again.",
  "agent": "2-SplitByTopics",
  "project": "UxFab",
  "final_output": null,
  "echo": false
}
```
(См. документацию по схеме — порядок полей стабилен, `error.message` дублируется в `description`.) fileciteturn0file1

```

# Конец SRS
