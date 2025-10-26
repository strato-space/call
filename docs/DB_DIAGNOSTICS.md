# Database Diagnostics Tool

## Overview

The `debug_db.py` script provides diagnostic utilities for inspecting the Call repository database. Use it to verify target resolution, check for duplicates, and debug database-related issues.

## Location

```text
call/debug_db.py
```

## Usage

```bash
cd call
uv run python debug_db.py
```

## Features

### 1. Database Reload

Automatically reloads the database from filesystem before running diagnostics:

```python
result = repo_fs.reload()
print(f"Reload result: {result}")
```

### 2. Project Card Inspection

Checks entries for specific projects:

```python
# Example: Check UxFab project card
cur.execute("SELECT target, project, agent, prompt, type, state FROM repo WHERE target = 'UxFab'")
```

### 3. Target Resolution Testing

Tests cascading target resolution (project → agent → prompt):

```python
from call.lib import api
row = api.interpret_target(project=None, agent=None, prompt=None, target="StratoProject")
print(f"Type: {row.type}, Agent: {row.agent}, Prompt: {row.prompt}")
```

### 4. Duplicate Detection

Identifies targets with multiple entries (which should not exist with PRIMARY KEY constraint):

```python
cur.execute("SELECT target, COUNT(*) FROM repo GROUP BY target HAVING COUNT(*) > 1")
```

## Common Diagnostic Scenarios

### Scenario 1: Target Not Found

**Symptom**: `SelectionNotFoundError: No card found matching the provided filters`

**Diagnosis**:
```python
# Check if target exists in database
cur.execute("SELECT * FROM repo WHERE target = ?", (target_name,))
rows = cur.fetchall()
if not rows:
    print(f"Target '{target_name}' not found in database")
```

**Solution**: Run `repo_fs.reload()` or check if the file exists in the filesystem.

### Scenario 2: Multiple Matches

**Symptom**: `TooManyRowsError: Multiple cards matched the provided filters`

**Diagnosis**:
```python
# Find duplicates
cur.execute("SELECT target, project, agent, prompt, type FROM repo WHERE target = ?", (target_name,))
print(f"Found {len(rows)} matches")
```

**Solution**: This indicates a bug in filesystem scanning. Check for:
- Duplicate target IDs in metadata
- Same file indexed multiple times
- Primary key constraint not enforced

### Scenario 3: Target Resolution Priority

**Symptom**: Wrong type selected (e.g., prompt instead of project)

**Diagnosis**:
```python
# Test cascading lookup
cur.execute("SELECT type FROM repo WHERE target = ? ORDER BY CASE type WHEN 'project' THEN 1 WHEN 'agent' THEN 2 WHEN 'prompt' THEN 3 END", (target,))
```

**Expected Behavior**: With priority **project → agent → prompt**:
1. If project exists with this target, return project
2. Else if agent exists, return agent
3. Else return prompt

### Scenario 4: Executable vs Non-Executable Projects

**Symptom**: `No agent found matching criteria` for project targets

**Diagnosis**:
```python
row = api.interpret_target(target="StratoProject")
has_prompt = bool(row.card or getattr(row, 'prompt_text', None))
print(f"Type: {row.type}, Has prompt: {has_prompt}")
```

**Solution**:
- **Executable projects** (with PROMPT section) run directly
- **Non-executable projects** require an agent to run

## Database Schema

```sql
CREATE TABLE repo (
    target   TEXT PRIMARY KEY,  -- Unique identifier
    project  TEXT,               -- Parent project
    agent    TEXT,               -- Agent name (empty for projects/prompts)
    prompt   TEXT,               -- Prompt name (empty for projects/agents)
    path     TEXT,               -- Absolute filesystem path
    state    TEXT,               -- ready|draft (prompts only)
    engine   TEXT,               -- openai|openai-agents
    orchestration TEXT,          -- sequence|handoff|langgraph
    type     TEXT,               -- project|agent|prompt
    rel_path TEXT,               -- Repository-relative path
    url      TEXT,               -- GitHub URL
    goal     TEXT,               -- Purpose/description
    card     TEXT                -- Full markdown content
)
```

## Key Indices

```sql
CREATE INDEX idx_repo_project ON repo(project)
CREATE INDEX idx_repo_agent ON repo(agent)
CREATE INDEX idx_repo_prompt ON repo(prompt)
CREATE INDEX idx_repo_state ON repo(state)
CREATE INDEX idx_repo_target ON repo(target)
```

## Target Resolution Priority

Since 2025-10-23, target resolution follows **project → agent → prompt** priority (most secure):

1. **Projects** have highest priority (cannot be overridden)
2. **Agents** have medium priority
3. **Prompts** have lowest priority

This ensures that:
- Prompts cannot hijack project or agent names
- Explicit type constraints are respected
- Security is maintained in multi-level hierarchies

## Environment Variables

- `DB_PATH`: Database location (default: `.cache/call/repo.db`)
- `PROMPT_REPO`: Path to prompt repository
- `AGENT_REPO`: Path to agent repository
- `CALL_DEBUG`: Enable debug logging

## Extending the Tool

Add custom diagnostics to `debug_db.py`:

```python
# Example: Find all prompts without metadata
print("\n=== Prompts without metadata ===")
cur.execute("SELECT target, path FROM repo WHERE type='prompt' AND (goal IS NULL OR goal = '')")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")
```

## Troubleshooting

### Database Locked

If you get "database is locked", close other processes accessing the database.

### Schema Mismatch

If schema changes are not applied, delete the database and reload:

```bash
rm .cache/call/repo.db
uv run python debug_db.py
```

### Performance Issues

For large repositories (>1000 cards), add indices:

```sql
CREATE INDEX idx_repo_type ON repo(type);
CREATE INDEX idx_repo_project_agent ON repo(project, agent);
```

## See Also

- [README.md](../README.md) - Main documentation
- [repo_db.py](../lib/repo_db.py) - Database interface
- [repo_fs.py](../lib/repo_fs.py) - Filesystem scanner
