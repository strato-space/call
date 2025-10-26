# MCP Hook Data Integrity Fix

## Problem

**Critical architectural bug**: `MCPServerStdioHook` was truncating MCP tool output in the agent pipeline.

### Root Cause

```python
# ❌ BEFORE (buggy)
result_text = self._format_tool_result(result)  # Truncates to 4KB when DEBUG_MODE=0
return CallToolResult(result=result_text)       # Returns truncated data to agent!
```

This violated separation of concerns:
- **Display layer** (Telegram/logs) needs truncation for readability
- **Pipeline layer** (Agent processing) needs FULL data

### Impact

- Large MCP responses (>4KB) were silently truncated
- Agents saw incomplete data (e.g., 10 items instead of 21)
- Workaround required `DEBUG_MODE=1`, causing verbose Telegram logs

## Solution

### 1. Separate Display and Pipeline Data Flows

```python
# ✅ AFTER (fixed)
result_text_for_display = self._format_tool_result(result)  # For Telegram/logs
await self.__edit_message_text(result_text_for_display)     # Display: may truncate
return result                                                # Pipeline: always full!
```

**Architecture:**
```
MCP Tool → Raw Result
    ↓
    ├─→ [Display Path] → _format_tool_result() → Telegram/Logs (truncated)
    └─→ [Pipeline Path] → Agent (full, untouched)
```

### 2. Unified Configuration

- Replaced `DEBUG_MODE` with existing `CALL_DEBUG`
- Updated documentation to clarify truncation only affects display

### 3. Comprehensive Tests

Created `test_mcp_hook_integrity.py`:
- **test_mcp_hook_preserves_large_data**: Verifies 10KB+ data preserved
- **test_mcp_hook_display_truncation_doesnt_affect_pipeline**: Confirms display truncation doesn't affect pipeline
- **test_mcp_hook_json_array_integrity**: Tests specific case (21-element JSON array)

## Changes

### Files Modified

1. **`call/app/call.py`**
   - Line 3257: Return `result` directly instead of re-wrapping
   - Line 3397: Same for alternative code path
   - Line 3074: Changed `DEBUG_MODE` → `CALL_DEBUG`

2. **`call/.env`**
   - Removed `DEBUG_MODE` variable
   - Updated `CALL_DEBUG` comment to clarify scope

3. **`call/docs/formatting.md`**
   - Updated truncation documentation
   - Clarified display-only impact

### Files Created

4. **`call/app/tests/test_mcp_hook_integrity.py`**
   - New test suite for data integrity
   - 3 test cases covering edge cases

5. **`call/docs/BUGFIX-MCP-HOOK-INTEGRITY.md`** (this file)
   - Complete documentation of the fix

## Verification

### Before Fix
```bash
# Required DEBUG_MODE=1 to see all 21 items
CALL_DEBUG=0  # → Agent sees 10 items (truncated at 4KB)
CALL_DEBUG=1  # → Agent sees 21 items, but verbose Telegram logs
```

### After Fix
```bash
# Works correctly with any CALL_DEBUG value
CALL_DEBUG=0  # → Agent sees 21 items (full), Telegram clean (truncated)
CALL_DEBUG=1  # → Agent sees 21 items (full), Telegram verbose (full)
```

### Run Tests
```bash
cd call
pytest app/tests/test_mcp_hook_integrity.py -v
```

Expected output:
```
test_mcp_hook_preserves_large_data PASSED
test_mcp_hook_display_truncation_doesnt_affect_pipeline PASSED
test_mcp_hook_json_array_integrity PASSED
```

## Benefits

1. ✅ **Data Integrity**: Agents always receive complete MCP tool output
2. ✅ **Clean Logs**: Telegram remains readable (truncated display)
3. ✅ **No Workarounds**: Works correctly out-of-the-box
4. ✅ **Proper Architecture**: Clear separation display vs. pipeline
5. ✅ **Tested**: Comprehensive test coverage prevents regression

## Related Issues

- Original discovery: PM-Router processing only 10/21 routing templates
- Root cause: MCP `fs:read_text_file` returning truncated JSON
- Impact: All MCP tools returning >4KB data were affected

## Migration

No migration needed. Existing code continues to work:
- Remove `DEBUG_MODE` from `.env` if set
- Use `CALL_DEBUG` for debug control
- Tests pass with default configuration

---

**Date**: 2025-10-24  
**Author**: Fixed via Cascade AI session  
**Severity**: Critical (data loss in production)  
**Status**: ✅ Fixed and tested
