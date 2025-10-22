# Test Scenario: Specialized Bot with Project Default Target

## Setup
Bot: `UralRMSBot` (специализированный бот для проекта `UralRMS`)
Project: `UralRMS` 
Default target: `UralRMS` (project.md)

## Test Cases

### 1. Mention бота без target (должен вызвать project.md)
**Input (в группе):**
```
@UralRMSBot расскажи статус проекта
```

**Ожидается:**
- `project=UralRMS`
- `target=UralRMS`
- Вызов `prompt/UralRMS/project.md`

---

### 2. Mention бота с target (должен вызвать указанный target)
**Input (в группе):**
```
@UralRMSBot @PM-1 статус за вчера
```

**Ожидается:**
- `project=UralRMS`
- `target=PM-1`
- Вызов промпта `PM-1-DayStatusSummarizer` из проекта `UralRMS` (если есть)

---

### 3. Plain text в приватном чате (должен вызвать project.md)
**Input (в DM):**
```
расскажи статус проекта
```

**Ожидается:**
- `project=UralRMS`
- `target=UralRMS`
- Вызов `prompt/UralRMS/project.md`

---

### 4. Mention target без бота (должен вызвать target)
**Input (в группе):**
```
@PM-1 статус за вчера
```

**Ожидается:**
- `project=UralRMS`
- `target=PM-1`
- Вызов промпта `PM-1`

---

### 5. StratoSpaceAiBot без target (универсальный бот, старая логика)
**Input (в группе):**
```
@StratoSpaceAiBot привет
```

**Ожидается:**
- `project=None`
- `target=None`
- Blank agent (старая логика без изменений)

---

## Implementation Details

### Code Changes in `telegram_bot/bot.py:_call_task()`

**Before (line 975-976):**
```python
# If no explicit target name, do not pass project — let library run a blank agent
proj = proj_baseline if (name or "").strip() else None
```

**After (line 975-988):**
```python
# For specialized bots: if no explicit target, use project name as target (runs project.md)
# For StratoSpaceAiBot: keep existing behavior (blank agent)
if (name or "").strip():
    # Explicit target provided by user
    proj = proj_baseline
    target_name = name
else:
    # No target: for specialized bot, use project as target; for universal bot, use None
    if proj_baseline:
        proj = proj_baseline
        target_name = proj_baseline  # Use project name as target to invoke project.md
    else:
        proj = None
        target_name = None
```

---

## Architecture

1. **Bot initialization**: `--bot-name UralRMSBot` → `PROJECT_NAME=UralRMS`
2. **Token lookup**: `TELEGRAM_TOKEN.UralRMS` from `.env`
3. **Message handling**: 
   - Groups: requires `@` mention
   - Private: all text accepted
4. **Target resolution**:
   - Explicit: `@Name` → target=Name
   - Implicit (specialized): no `@Name` → target=PROJECT_NAME
   - Implicit (universal): no `@Name` → target=None
5. **Call execution**: `call_api.call_async(project=PROJECT_NAME, target=target_name)`
6. **Library resolution**: `target` resolved as `prompt > agent > project`

---

## Benefits

✅ Specialized bots can operate without explicit target mention
✅ `@BotName <input>` invokes project's orchestrator (project.md)
✅ Universal bot (StratoSpaceAiBot) behavior unchanged
✅ Consistent with CLI: `call exec --project X --target X`
✅ `/start` command shows project goal for specialized bots

---

## Additional Feature: Project Goal in /start

### Test Case 6: /start command for specialized bot

**Input:**
```
/start
```

**Expected Output:**
```
🎯 StratoProject

Головной orchestrator для PM-пакета. Вызывает PM-Router для построения routing-item массива,
определяет нужный PM-агент (1-11) и итерирует по каждому элементу (по одному за раз).

---

call-bot

Commands:
...
```

**Implementation:** `bot.py:handle_start()` (lines 651-670)
- Loads project card via `call_api.read(PROJECT_NAME)`
- Extracts `goal` from YAML metadata
- Prepends goal section to help text
