# Target Resolution Priority Change - 2025-10-23

## Summary

Changed target resolution priority from **prompt → agent → project** to **project → agent → prompt** for improved security and hierarchy integrity.

## Changes

### 1. Database Configuration Fix

**File**: `call/.env`

**Before**:
```ini
DB_PATH=c/call/repo.db  # ❌ Invalid path
```

**After**:
```ini
DB_PATH=.cache/call/repo.db  # ✅ Correct path
```

### 2. Target Resolution Priority

**File**: `call/lib/repo_db.py` - `select_card()` function

**New Priority** (most secure):
1. **Project** → Cannot be overridden by agents or prompts
2. **Agent** → Cannot be overridden by prompts
3. **Prompt** → Lowest priority

**Implementation**:
```python
if target and not kind:
    # Try project first (with constraints if provided)
    try:
        result = select_card(project=project, agent=agent, prompt=prompt, target=target, kind="project")
        return result
    except SelectionNotFoundError:
        pass
    
    # Try agent second
    try:
        result = select_card(project=project, agent=agent, prompt=prompt, target=target, kind="agent")
        return result
    except SelectionNotFoundError:
        pass
    
    # Try prompt last
    result = select_card(project=project, agent=agent, prompt=prompt, target=target, kind="prompt")
    return result
```

**Security Rationale**: 
- Prevents prompt files from hijacking project or agent names
- Maintains clear hierarchy: Projects → Agents → Prompts
- Aligns with principle of least surprise

### 3. Executable Project Support

**File**: `call/lib/api.py` - `build_and_run_agent()` function

**Problem**: Projects with PROMPT sections were rejected with "No agent found matching criteria"

**Solution**: Check if project has executable content before requiring an agent:

```python
if cfg_type == "project":
    # If project has prompt text (card_text or prompt_text), it's executable directly
    # Skip agent resolution for executable projects
    has_prompt = bool(
        getattr(cfg, "card_text", None) or 
        getattr(cfg, "prompt_text", None) or 
        getattr(cfg, "instructions", None)
    )
    if not has_prompt:
        # Non-executable project: try to find an agent to run
        agent_probe = resolve_agent(...)
        ...
```

**Examples**:
- ✅ **StratoProject** - Has `<!-- PROMPT:START -->` section, runs directly
- ✅ **AgentFab** - Has `<!-- PROMPT:START -->` section, runs directly
- ❌ **Non-executable project** - Metadata only, requires agent

### 4. Documentation Updates

**Files Updated**:
- `call/README.md` - Added target resolution priority, executable projects explanation
- `call/docs/DB_DIAGNOSTICS.md` - New diagnostic tool documentation

**Key Additions**:
- Target resolution priority explanation
- Executable vs non-executable projects
- Security rationale
- Link to DB diagnostics tool

### 5. Test Coverage

**New Tests** (`app/tests/test_target_resolution_via_target.py`):

1. `test_api_normalize_selector` - Selector normalization
2. `test_interpret_target_projects_agentfab_uxfab` - Project target resolution
3. `test_build_cfg_project_via_target_preview_has_project_card` - Project config building
4. `test_build_cfg_executable_project_stratoproj` - **NEW**: Executable project test
5. `test_build_cfg_agents_via_target_fanfab` - Agent target resolution
6. `test_build_cfg_prompts_via_target` - Prompt target resolution

**Test Results**: 6/6 tests pass ✅

### 6. Database Diagnostics Tool

**File**: `call/docs/DB_DIAGNOSTICS.md`

**Features**:
- Database reload and inspection
- Target resolution testing
- Duplicate detection
- Common diagnostic scenarios with solutions
- Schema documentation
- Troubleshooting guide

**Usage**:
```bash
cd call
uv run python debug_db.py
```

## Migration Guide

### For Users

**No action required** if:
- You use explicit `project`, `agent`, or `prompt` parameters
- Your targets are unique across all levels

**Action required** if:
- You have targets with duplicate names (e.g., project "Test" and prompt "Test")
- You relied on prompt priority (prompts will now be shadowed by projects/agents)

### For Developers

**Before** (old priority: prompt → agent → project):
```bash
# If "Test" exists as both project and prompt, prompt was selected
call exec --target Test
```

**After** (new priority: project → agent → prompt):
```bash
# If "Test" exists as both project and prompt, project is selected
call exec --target Test

# To force prompt selection:
call exec --prompt Test  # Explicit kind
```

## Breaking Changes

### Potential Breaking Change

If you have duplicate target names across levels (e.g., project="AgentFab", prompt="AgentFab"), the behavior changes:

**Before**: Prompt "AgentFab" would be selected
**After**: Project "AgentFab" will be selected

**Mitigation**: Use explicit selectors:
```bash
# Force prompt selection
call exec --prompt AgentFab

# Force project selection (now default)
call exec --project AgentFab
```

## Benefits

1. **Security**: Prevents prompt injection by name shadowing
2. **Predictability**: Clear hierarchy aligns with mental model
3. **Debugging**: Diagnostic tool helps troubleshoot resolution issues
4. **Flexibility**: Executable projects can run directly without agents
5. **Test Coverage**: 6 comprehensive tests document expected behavior

## Test Results

**Before changes**: 144 passing, 9 failing
**After changes**: 150 passing, 4 failing (improved by 6 tests)

**Target resolution tests**: 6/6 passing ✅

## Related Files

- `call/.env` - Database path configuration
- `call/lib/repo_db.py` - Database interface with cascading logic
- `call/lib/api.py` - Executable project support
- `call/README.md` - Updated documentation
- `call/docs/DB_DIAGNOSTICS.md` - New diagnostic guide
- `call/app/tests/test_target_resolution_via_target.py` - Test suite
- `call/debug_db.py` - Diagnostic tool

## Rollback Instructions

If issues arise, revert commits:

```bash
git revert <commit-hash>
```

Or manually restore old priority in `repo_db.py`:

```python
# Old behavior (prompt → agent → project)
if target and not kind:
    try:
        result = select_card(..., kind="prompt")
        return result
    except SelectionNotFoundError:
        pass
    
    try:
        result = select_card(..., kind="agent")
        return result
    except SelectionNotFoundError:
        pass
    
    result = select_card(..., kind="project")
    return result
```

## Future Improvements

1. Add explicit warning when target shadows another level
2. Implement `--strict` mode to reject ambiguous targets
3. Add metrics for target resolution patterns
4. Improve error messages with suggestions
