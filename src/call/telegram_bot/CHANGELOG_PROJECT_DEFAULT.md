# Changelog: Specialized Bot Enhancements

## 2025-10-22: Project Default Target & Goal Display

### Features Added

#### 1. **Project.md as Default Target** 
When specialized bot receives mention without explicit target, it invokes `project.md` as orchestrator.

**Example:**
- Input: `@UralRMSBot расскажи статус`
- Behavior: `call_async(project="UralRMS", target="UralRMS")`
- Result: Executes `prompt/UralRMS/project.md`

**Code:** `bot.py:_call_task()` lines 975-988

**Logic:**
```python
if (name or "").strip():
    # Explicit target from user
    proj = proj_baseline
    target_name = name
else:
    # No target: use project as target for specialized bots
    if proj_baseline:
        proj = proj_baseline
        target_name = proj_baseline  # Invoke project.md
    else:
        proj = None
        target_name = None
```

---

#### 2. **Project Goal in /start Command**
Specialized bots now display project goal from `project.md` metadata in `/start` response.

**Example Output:**
```
🎯 StratoProject

Головной orchestrator для PM-пакета. Вызывает PM-Router для построения 
routing-item массива, определяет нужный PM-агент (1-11) и итерирует 
по каждому элементу (по одному за раз).

---

call-bot

Commands:
...
```

**Code:** `bot.py:handle_start()` lines 651-670

**Logic:**
1. Check if `PROJECT_NAME` exists
2. Load project card: `call_api.read(PROJECT_NAME)`
3. Extract `goal` from YAML metadata via regex
4. Prepend goal section to help text

---

### Behavior Changes

#### Before
| Scenario | project | target | Result |
|----------|---------|--------|--------|
| `@UralRMSBot статус` | `UralRMS` | `None` | Blank agent |
| `/start` (UralRMSBot) | N/A | N/A | Generic help |

#### After
| Scenario | project | target | Result |
|----------|---------|--------|--------|
| `@UralRMSBot статус` | `UralRMS` | `UralRMS` | Executes `project.md` |
| `/start` (UralRMSBot) | N/A | N/A | Project goal + help |

---

### Benefits

✅ **Reduced friction**: Users don't need to know exact target names  
✅ **Contextual help**: `/start` immediately explains bot's purpose  
✅ **Orchestrator pattern**: `project.md` acts as entry point  
✅ **Backward compatible**: Universal bot behavior unchanged  

---

### Testing

See `TEST_PROJECT_DEFAULT.md` for comprehensive test scenarios.

---

### Files Modified

1. `telegram_bot/bot.py`
   - `_call_task()`: Added project-as-target logic (lines 975-988)
   - `handle_start()`: Added goal extraction and display (lines 651-670)

2. Documentation created:
   - `TEST_PROJECT_DEFAULT.md`: Test scenarios
   - `EXAMPLE_START_OUTPUT.md`: Example outputs
   - `CHANGELOG_PROJECT_DEFAULT.md`: This file

---

### Integration with Previous Features

This enhancement builds on the **project-level prompts** feature:
- `repo_fs.py`: `project.md` indexed as executable prompt
- `repo_db.py`: Project-level prompts in `prompts[]` array
- `api.py`: `resolve_agent()` handles prompts without agent

Together, these changes enable:
- **CLI**: `python -m call.cli.main exec --project X --target X`
- **Bot**: `@XBot <input>` → invokes `project.md`
