# Specialized Bots with Natural Language Interaction

> **Commit:** `c66135f` - feat: specialized bots with natural language interaction

## Overview

Specialized bots are project-specific Telegram bots that enable natural language communication without slash commands. Users interact naturally, and the bot automatically invokes the project orchestrator (`project.md`) as the default target when no specific target is mentioned.

## Features

### 1. Project as Default Target

When a specialized bot receives a mention without an explicit target, it automatically invokes the project's orchestrator (`project.md`).

**Example:**
```text
User: @UralRMSBot расскажи статус проекта
Bot executes: call_async(project="UralRMS", target="UralRMS")
Result: Runs prompt/UralRMS/project.md
```

### 2. Natural Language Help (/start)

Specialized bots display project goal and usage examples instead of command reference:

```text
🎯 StratoProject

Головной orchestrator для PM-пакета. Вызывает PM-Router для построения 
routing-item массива, определяет нужный PM-агент (1-11) и итерирует 
по каждому элементу (по одному за раз).

---

💬 Общайтесь со мной на естественном языке.

В приватном чате просто напишите запрос.
В группе упомяните меня: @StratoProjectBot

Примеры:
- "статус проекта"
- "задачи на сегодня"
- "отчёт за неделю"
```

Universal bots (like `StratoSpaceAiBot`) continue to show full command reference.

## Usage Patterns

### Private Chats

| Input | Behavior |
|-------|----------|
| `статус проекта` | Invokes `project.md` with input |
| `@Target задачи` | Invokes specified target (target name must include `@`) |

### Group Chats

| Input | Behavior |
|-------|----------|
| `@ProjectNameBot статус` | Invokes `project.md` with input |
| `@ProjectNameBot @Target задачи` | Invokes specified `@Target` |
| `@Target задачи` | Invokes specified target (target name must include `@`) |
| Regular text | Ignored (no bot mention) |

## Setup

### 1. Create Project Structure

```text
prompt/
  YourProject/
    project.md        # Orchestrator with goal in metadata
    PM-1.md           # Optional sub-agents or prompts
    PM-2.md
```

### 2. Configure Bot Token

Add to `.env`:
```bash
TELEGRAM_TOKEN.YourProject=your_bot_token_here
```

### 3. Start Bot

```bash
python -m call.telegram_bot.bot --bot-name YourProjectBot
```

The bot will:
- Extract project name: `YourProjectBot` → `YourProject`
- Load token from `TELEGRAM_TOKEN.YourProject`
- Set default target name to `YourProject` which invoike `YourProject/project.md` card.

## Implementation Details

### Code Changes

**File:** `telegram_bot/bot.py`

#### 1. Default Target Logic (_call_task)

Lines 975-988:
```python
if (name or "").strip():
    # Explicit target provided by user
    proj = proj_baseline
    target_name = name
else:
    # No target: for specialized bot, use project as target
    if proj_baseline:
        proj = proj_baseline
        target_name = proj_baseline  # Invoke project.md
    else:
        proj = None
        target_name = None
```

#### 2. Natural Language Help (handle_start)

Lines 651-685:
- Load project card via `call_api.read(PROJECT_NAME)`
- Extract `goal` from YAML metadata
- Display goal + usage examples (no commands)
- Universal bots show full command reference

### Resolution Chain

1. **Bot mention**: `@BotName input` → extract target from input
2. **Target resolution**
   - Explicit: use provided target
   - Implicit: use `PROJECT_NAME` as target
3. **Library resolution**: `call_api.call_async(project, target)`
   - Resolves as: `prompt > agent > project`
   - For `target=PROJECT_NAME`: executes `project.md`

## Benefits

✅ **Reduced friction**: No need to remember command syntax  
✅ **Natural interaction**: Users communicate in their own words  
✅ **Contextual help**: `/start` explains bot's specific purpose  
✅ **Orchestrator pattern**: `project.md` acts as intelligent router  
✅ **Backward compatible**: Universal bot behavior unchanged  

## Examples

### Example 1: Daily Status Request

**User:** `@UralRMSBot статус за вчера`

**Execution:**
- `project=UralRMS`
- `target=UralRMS` (no explicit target)
- Runs `prompt/UralRMS/project.md`
- Project orchestrator parses intent and routes to PM-1

### Example 2: Specific Agent Call

**User:** `@UralRMSBot @PM-2 неделя`

**Execution:**
- `project=UralRMS`
- `target=PM-2` (explicit target)
- Runs `prompt/draft/PM-2-WeekStatus.md`

### Example 3: Universal Bot (No Change)

**User:** `@StratoSpaceAiBot привет`

**Execution:**
- `project=None`
- `target=None`
- Blank agent (existing behavior)

## Comparison: Before vs After

| Scenario | Before | After |
|----------|--------|-------|
| `@SpecializedBot input` | Blank agent | Invokes `project.md` |
| `/start` (specialized) | Generic help | Goal + examples |
| `/start` (universal) | Command reference | Command reference |
| `@SpecializedBot @Target` | Target | Target (unchanged) |

## See Also

- [Project-Level Prompts](project-level-prompts.md) - How project.md is indexed
- [MCP Hook Routing](mcp-hook-routing.md) - Debug message routing
- [Telegram Bot Guide](../telegram_bot/srs.md) - Bot architecture
