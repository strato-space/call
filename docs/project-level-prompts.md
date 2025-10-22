# Project-Level Prompts

> **Commit:** `e7f7697` - feat: support project-level prompts and dual-channel MCP routing  
> **Related:** `c66135f` - feat: specialized bots with natural language interaction

## Overview

Project-level prompts are prompts attached directly to a project without requiring an agent. This enables a more flexible prompt hierarchy: `project → prompt` instead of only `project → agent → prompt`.

## Supported Patterns

The system now supports three prompt attachment patterns:

1. **project → agent → prompt** (traditional)
2. **project → prompt** (new: project-level)
3. **draft → prompt** (draft without project)

## Key Changes

### 1. Scanning and Validation

**File:** `lib/repo_fs.py`

#### Project.md as Executable Prompt

Lines 372-408: Project cards (`project.md`) are now indexed twice:
- As **project card** (`type=project`)
- As **executable prompt** (`type=prompt`, `prompt=project_name`)

```python
# Insert project card (type=project)
_upsert_row(cur, target=proj_name, project=proj_name, agent="", 
            prompt="", type="project", ...)

# Also insert as executable prompt
_upsert_row(cur, target=proj_name, project=proj_name, agent="", 
            prompt=proj_name, type="prompt", state="ready", ...)
```

#### Optional Agent Field

Lines 528-537: Validation now requires only `project` field:
- **ready** state: `project` required, `agent` optional
- **draft** state: both optional (allows drafts without project)

```python
# Only project is required for ready; agent is optional
state_name = root.name.lower()
if not proj and state_name == "ready":
    debug_print("[repo.scan]", "[WARN]", 
                f"Prompt MD missing project (ready state): {p}")
```

### 2. Database Structure

**File:** `lib/repo_db.py`

Lines 293-306: Project structure now includes `prompts[]` array:

```python
proj_map[prj] = {
    "name": prj, 
    "type": "project", 
    "agents": [],      # Agent-based prompts
    "prompts": []      # Project-level prompts
}
```

**Hierarchy:**
```json
{
  "name": "StratoProject",
  "type": "project",
  "agents": [],
  "prompts": [
    "StratoProject",           // project.md
    "PM-1-DayStatusSummarizer", // prompt without agent
    "PM-2-WeekStatus",
    ...
  ]
}
```

### 3. Resolution and Execution

**File:** `lib/api.py`

Lines 1605-1643, 1688-1699: `resolve_agent()` handles prompts without agent:

```python
# Only project is required; agent is optional
valid_alt = [r for r in alt_recs if str(r.get("project") or "").strip()]

# For project-level prompts without agent, return path directly
if not ag:
    return {
        "ok": True,
        "resolved": {
            "project": pj,
            "name": "",
            "path": pr.get("path") or "",
            "aliases": [],
            "prompts": [prompt] if prompt else [],
        },
    }
```

## Usage

### CLI Execution

Execute project card directly:
```bash
python -m call.cli.main exec --project StratoProject --target StratoProject --input "test"
```

Execute project-level prompt:
```bash
python -m call.cli.main exec --project StratoProject --target PM-1 --input "статус"
```

### List Project Prompts

```bash
python -m call.cli.main list --project StratoProject --format yaml
```

**Output:**
```yaml
- name: StratoProject
  type: project
  agents: []
  prompts:
  - StratoProject      # project.md
  - PM-1-DayStatusSummarizer
  - PM-2-WeekStatus
  - PM-io
  - PM-Router
  - PM
```

### Telegram Bot

Specialized bots automatically invoke project card:

```text
User: @StratoProjectBot статус проекта
Executes: project=StratoProject, target=StratoProject
Runs: prompt/StratoProject/project.md
```

## Metadata Format

Project-level prompts require only `project` field in metadata:

```yaml
# prompt/draft/PM-1-DayStatusSummarizer.md
id: PM-1-DayStatusSummarizer
title: Daily Status Report
project: StratoProject
priority: P2
# agent field is optional
```

## Example Structure

### Before (Agent Required)

```text
prompt/
  StratoProject/
    project.md         # Project card only
    AgentA/
      agent.md
      prompt.md        # Requires agent
```

**Metadata:**
```yaml
project: StratoProject
agent: AgentA          # Required
```

### After (Agent Optional)

```text
prompt/
  StratoProject/
    project.md         # Project card + executable prompt
  draft/
    PM-1.md           # Project-level prompt
    PM-2.md
```

**Metadata:**
```yaml
project: StratoProject
# agent field optional for project-level prompts
```

## Scan Statistics

**Before:**
- 134 prompts scanned
- Warnings for prompts without agent

**After:**
- 136 prompts scanned (+2 from project.md double indexing)
- No warnings for project-level prompts
- Draft files can exist without project

## Benefits

✅ **Flexible hierarchy**: Prompts can attach to project or agent  
✅ **Reduced boilerplate**: No need to create agents for simple prompts  
✅ **Executable project cards**: `project.md` can be invoked directly  
✅ **Backward compatible**: Agent-based prompts work as before  
✅ **Clean drafts**: Draft files don't require metadata  

## Resolution Priority

When resolving a target, the system checks in order:

1. **Prompt** with matching `prompt` field
2. **Agent** with matching `agent` field  
3. **Project** with matching `project` field (runs `project.md`)

Example for `target=PM-1`:
1. Check: `prompt="PM-1"` → **Found**
2. Skip: agent/project checks

Example for `target=StratoProject`:
1. Check: `prompt="StratoProject"` → **Found** (project.md indexed as prompt)
2. Return: path to `project.md`

## Migration

To migrate existing prompts to project-level:

1. Remove `agent` field from metadata
2. Keep only `project` field
3. Move to `prompt/draft/` or `prompt/ready/`
4. Run `reload` to rescan

**Before:**
```yaml
project: MyProject
agent: MyAgent
```

**After:**
```yaml
project: MyProject
# agent removed
```

## See Also

- [Specialized Bots](specialized-bots.md) - Natural language bot interaction
- [Cards](cards.md) - Agent and prompt card system
- [Repo Scanning](../lib/repo_fs.py) - Repository indexing logic
