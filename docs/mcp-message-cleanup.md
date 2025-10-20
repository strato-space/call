# MCP Service Message Cleanup

## Overview

This document describes the automatic cleanup mechanism for MCP (Model Context Protocol) service messages in Telegram. When agents call MCP tools or sub-agents, intermediate debug messages are posted to Telegram for monitoring. After the agent completes its work, these service messages are automatically deleted to keep the chat clean while preserving the final result.

## Core Principles

1. **Track all service messages**: Intermediate debug messages are tracked during agent execution
2. **Clean after completion**: Service messages are deleted automatically when agent finishes
3. **Preserve final results**: Only intermediate messages are deleted, final outputs remain
4. **Configurable behavior**: Cleanup can be enabled/disabled via environment variable
5. **Fail-safe design**: Cleanup errors don't interrupt agent execution

## Configuration

### Environment Variable

```bash
# In call/.env
TG_CLEANUP_MCP_MESSAGES=1  # 1=enabled (default), 0=disabled
```

**Accepted values:**
- `1`, `true`, `yes`, `on` → Cleanup enabled (case-insensitive)
- `0`, `false`, `no`, `off` → Cleanup disabled
- Empty or unset → Defaults to enabled (`1`)

### When to Disable Cleanup

Disable cleanup (`TG_CLEANUP_MCP_MESSAGES=0`) when:
- Debugging agent execution flow
- Analyzing MCP tool call sequence
- Troubleshooting agent-as-tools invocations
- Preserving full execution trace for review

## Implementation

### 1. Service Message Tracking

**Class**: `MCPServerStdioHook` (extends `MCPServerStdio`)

**Storage**: 
```python
self.__service_message_ids: list[tuple[int, int]] = []  # [(chat_id, msg_id), ...]
```

**When messages are tracked:**

1. **MCP tool invocation messages**:
   - Posted when MCP tool starts execution
   - Contains tool name and YAML-formatted arguments
   - Format: `🛠️ {tool_name}\n\n{yaml_args}`

2. **Agent-as-tool invocation messages**:
   - Posted when parent agent calls child agent
   - Contains child agent name and input payload
   - Format: `🛠️ {child_agent}\nfrom {parent_agent}\n\n{yaml_payload}`

**Code location**: `app/call.py:2990-2991`

```python
if msg:
    self.__service_message_ids.append((msg.chat_id, msg.message_id))
```

### 2. Cleanup Execution

**Method**: `MCPServerStdioHook.cleanup_service_messages()`

**Code location**: `app/call.py:3255-3270`

```python
async def cleanup_service_messages(self) -> None:
    """Delete all tracked service messages from Telegram."""
    # Check if cleanup is enabled via environment variable
    cleanup_enabled = os.environ.get("TG_CLEANUP_MCP_MESSAGES", "1").strip().lower()
    if cleanup_enabled not in ("1", "true", "yes", "on"):
        return
    
    if not self.__service_message_ids:
        return
    await _init_bot_safe()
    for chat_id, msg_id in self.__service_message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    self.__service_message_ids.clear()
```

**When cleanup is triggered:**

After agent completes execution, in the finally block of agent context manager.

**Code location**: `app/call.py:4072-4078`

```python
# Cleanup: delete all MCP service messages after final result is delivered
try:
    for srv in mcp_servers:
        if isinstance(srv, MCPServerStdioHook):
            await srv.cleanup_service_messages()
except Exception:
    pass
```

**Error handling:**
- Individual message deletion failures are silently ignored (Telegram API may fail if message already deleted)
- Overall cleanup failure doesn't interrupt agent execution
- Errors are caught at two levels: per-message and per-cleanup-call

### 3. Message Lifecycle

```
1. Agent starts execution
   ↓
2. MCP tool called → service message posted → tracked
   ↓
3. Agent-as-tool invoked → service message posted → tracked
   ↓
4. More MCP tools → more service messages → tracked
   ↓
5. Agent completes → final output posted (NOT tracked)
   ↓
6. Cleanup triggered → all tracked messages deleted
   ↓
7. Result: Only final output visible in Telegram
```

## Examples

### Example 1: MCP Tool Sequence

**Before cleanup:**
```
Telegram Chat:
├─ 🛠️ fs:read_text_file         ← service message (will be deleted)
│  input: draft/PM-io.md
├─ 🛠️ voice:crm_tickets         ← service message (will be deleted)
│  project_id: 672315cb...
├─ 🛠️ tg-ro:list_messages       ← service message (will be deleted)
│  chat_id: 4220837117
└─ ✅ Final Report               ← final output (preserved)
   [detailed report content]
```

**After cleanup (default behavior):**
```
Telegram Chat:
└─ ✅ Final Report               ← only final output remains
   [detailed report content]
```

### Example 2: Agent-as-Tools Chain

**Before cleanup:**
```
Telegram Chat:
├─ 🛠️ PM-Router from PM          ← service message (will be deleted)
│  input: 17.10; DBI / Metro SPOT
├─ 🛠️ PM-1-DayStatusSummarizer from PM  ← service message (will be deleted)
│  input: 17.10; DBI / Metro SPOT
│  topic: DBI / Metro SPOT
│  interval: {...}
├─ 🛠️ tg-ro:list_messages       ← service message (will be deleted)
│  chat_id: 4220837117
└─ ✅ PM Execution Complete      ← final output (preserved)
   Топиков обработано: 1/1
```

**After cleanup:**
```
Telegram Chat:
└─ ✅ PM Execution Complete      ← only final output remains
   Топиков обработано: 1/1
```

### Example 3: Debugging with Cleanup Disabled

**Configuration:**
```bash
TG_CLEANUP_MCP_MESSAGES=0
```

**Result:** All service messages remain visible in Telegram for debugging

## Testing

### Test Cleanup Enabled (Default)

```bash
# Run agent with default settings
call PM "день"

# Expected: Only final output visible in Telegram
# All intermediate MCP tool messages deleted
```

### Test Cleanup Disabled

```bash
# Disable cleanup
export TG_CLEANUP_MCP_MESSAGES=0
call PM "день"

# Expected: All messages visible in Telegram
# Service messages AND final output preserved
```

### Unit Tests

```bash
# Test cleanup logic
pytest app/tests/test_mcp_hook_cleanup.py -v
```

**Test cases:**
- ✅ Cleanup enabled by default
- ✅ Cleanup respects TG_CLEANUP_MCP_MESSAGES=0
- ✅ Cleanup handles empty message list
- ✅ Cleanup handles Telegram API errors gracefully
- ✅ Cleanup doesn't interrupt agent execution on failure

## Common Issues

### Issue 1: Service Messages Not Deleted

**Symptom**: Intermediate MCP tool messages remain visible after agent completes

**Possible causes:**
1. `TG_CLEANUP_MCP_MESSAGES=0` set in environment
2. Telegram API rate limiting
3. Bot lacks permissions to delete messages in chat
4. Messages older than 48 hours (Telegram limitation)

**Solution:**
1. Check `.env`: `TG_CLEANUP_MCP_MESSAGES=1`
2. Verify bot has "Delete Messages" permission in chat
3. Check bot logs for deletion errors
4. Consider manual cleanup if messages are old

### Issue 2: Final Output Also Deleted

**Symptom**: Both service messages and final output disappear

**Cause**: Final output message incorrectly tracked as service message

**Solution**: Verify that final output is posted via `send_digest_notification()` or `send_telegram_welcome_message()`, not via `MCPServerStdioHook.__send_message()`

**Check code**: Final output should NOT go through `MCPServerStdioHook` methods

### Issue 3: Cleanup Errors Break Agent Execution

**Symptom**: Agent fails when cleanup encounters errors

**Expected behavior**: This should NEVER happen - cleanup is wrapped in try/except

**Solution**: If this occurs, it's a bug. Check:
1. `cleanup_service_messages()` has proper exception handling
2. Caller wraps cleanup in try/except block
3. Report issue with full error trace

## Related Code Locations

- `app/call.py:2756-2761` — `__service_message_ids` initialization
- `app/call.py:2990-2991` — Service message tracking
- `app/call.py:3255-3270` — `cleanup_service_messages()` method
- `app/call.py:4072-4078` — Cleanup trigger in agent context manager
- `call/.env:22-23` — `TG_CLEANUP_MCP_MESSAGES` configuration

## Best Practices

1. **Keep cleanup enabled in production**: Default `TG_CLEANUP_MCP_MESSAGES=1` keeps chats clean
2. **Disable for debugging**: Set `TG_CLEANUP_MCP_MESSAGES=0` when troubleshooting
3. **Don't track final outputs**: Only intermediate debug messages should be tracked
4. **Handle errors gracefully**: Never let cleanup failures interrupt agent execution
5. **Document behavior**: Make it clear to users that intermediate messages will be deleted

## Design Rationale

### Why Auto-Cleanup?

**Problem**: Without cleanup, Telegram chats become cluttered with intermediate debug messages:
- Dozens of MCP tool invocation messages per agent run
- Multiple agent-as-tools chain messages
- Hard to find actual results among noise
- Poor user experience

**Solution**: Track intermediate messages and delete after completion:
- Clean chat history
- Easy to find final results
- Preserve debugging capability (configurable)
- No manual cleanup needed

### Why Track Service Messages?

**Alternative approaches:**
1. ❌ Delete all messages from bot → Would delete final outputs too
2. ❌ Don't post intermediate messages → Lose debugging visibility
3. ✅ Track and selectively delete → Best of both worlds

### Why Default Enabled?

**Reasoning:**
- Production use case: Clean chats, no clutter
- Development use case: Can easily disable via environment variable
- Most users prefer clean output by default
- Power users can enable full trace when needed

## Summary

The MCP service message cleanup feature:

- **Automatically** removes intermediate debug messages after agent completes
- **Preserves** final output and results
- **Configurable** via `TG_CLEANUP_MCP_MESSAGES` environment variable
- **Fail-safe** with comprehensive error handling
- **Enabled by default** for clean Telegram chat experience

**User experience:**
- **With cleanup** (default): See only final results, clean chat
- **Without cleanup**: See full execution trace, all intermediate steps

This provides the best of both worlds: clean output for production use and full debugging visibility when needed.
