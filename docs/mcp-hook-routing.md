# MCP Hook Telegram Routing

## Overview

The MCP Hook system implements **dual-channel message routing** for Telegram notifications. This architecture separates debug/diagnostic messages from user-facing results, enabling centralized MCP operation monitoring without cluttering conversation threads.

## Architecture

### Message Routing Strategy

```text
┌─────────────────────────────────────────────────────────┐
│                   Agent Execution                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  User Request      MCP Hook Debug         Agent Result │
│  (any chat)        Messages               & Status     │
│       │                 │                      │        │
│       ▼                 ▼                      ▼        │
│  ┌─────────┐      ┌──────────┐         ┌─────────┐     │
│  │ Welcome │      │ Tool Args│         │ Result  │     │
│  │ Message │      │ Tool Exec│         │ Message │     │
│  └────┬────┘      │ Progress │         └────┬────┘     │
│       │           └────┬─────┘              │          │
│       │                │                    │          │
│       ▼                ▼                    ▼          │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐   │
│  │selected_chat│  │ debug_chat_id│  │selected_chat│   │
│  │   (origin)  │  │   (from .env)│  │   (origin)  │   │
│  └─────────────┘  └──────────────┘  └─────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### Message Types and Routing

| Message Type | Destination | Variable | Purpose |
|--------------|-------------|----------|---------|
| **Welcome Banner** | Origin chat | `selected_chat_id` | Notify user about agent start |
| **MCP Tool Arguments** | Debug chat | `debug_chat_id` | Log tool invocations with YAML args |
| **MCP Tool Results** | Debug chat | `debug_chat_id` | Log tool execution results |
| **Sequential Thinking Progress** | Debug chat | `debug_chat_id` | Show reasoning progress bars |
| **Typing Status** | Origin chat | `selected_chat_id` | User feedback during execution |
| **Final Result** | Origin chat | `selected_chat_id` | Agent output delivery |
| **Error Notifications** | Origin chat | `selected_chat_id` | Failure alerts |

## Implementation

### Global Variables (`app/call.py`)

```python
# User request routing (dynamic, set by Telegram bot or API caller)
selected_chat_id: Optional[int] = None
selected_thread_id: Optional[int] = None

# MCP debug routing (static, always from .env)
debug_chat_id: Optional[int] = None
debug_thread_id: Optional[int] = None
```

### Initialization

```python
# Load from environment at module import
TELEGRAM_CHAT_ID = get_telegram_chat_id("TELEGRAM_CHAT_ID")
TELEGRAM_THREAD_ID = get_telegram_chat_id("TELEGRAM_THREAD_ID", "")

# Initialize debug routing (immutable throughout execution)
debug_chat_id = TELEGRAM_CHAT_ID
debug_thread_id = TELEGRAM_THREAD_ID or None

# Initialize request routing (overridden by bot/API)
selected_chat_id = TELEGRAM_CHAT_ID
selected_thread_id = TELEGRAM_THREAD_ID or None
```

### MCPServerStdioHook Class

The `MCPServerStdioHook` wrapper intercepts MCP tool calls and sends formatted messages to Telegram:

```python
class MCPServerStdioHook(MCPServerStdio):
    """Wrapper for MCPServerStdio that writes per-instance logs to Telegram.
    
    Debug messages are sent to debug_chat_id (from TELEGRAM_CHAT_ID in .env),
    while typing status is sent to selected_chat_id (original request chat).
    """
    
    async def __send_message(self, text: str) -> Message:
        """Send MCP debug message to debug_chat_id."""
        msg = await safe_send_message(
            chat_id=debug_chat_id,  # ← Always use debug channel
            message_thread_id=debug_thread_id,
            text=cleaned,
            parse_mode=ParseMode.HTML,
            disable_notification=True,
        )
        # Track for cleanup if TG_CLEANUP_MCP_MESSAGES=1
        self.__service_message_ids.append((msg.chat_id, msg.message_id))
        return msg
    
    async def call_tool(self, tool_name: str, arguments: dict) -> CallToolResult:
        # Log arguments (sent to debug_chat_id via __send_message)
        await self.__send_message(f"🛠️ {tool_name}\n\n{yaml_args}")
        
        # Execute tool
        result = await parent_call_tool(tool_name, arguments)
        
        # Log result (sent to debug_chat_id via __edit_message_text)
        await self.__edit_message_text(f"✅ {tool_name}\n\n{result_text}")
        
        # Send typing indicator to origin chat (selected_chat_id)
        if selected_chat_id is not None:
            await bot.send_chat_action(
                chat_id=selected_chat_id,  # ← User's original chat
                message_thread_id=selected_thread_id,
                action=ChatAction.TYPING,
            )
        
        return result
```

## Configuration

### Environment Variables (`.env`)

```env
# Debug message destination (centralized monitoring)
TELEGRAM_CHAT_ID=-1002710557620

# Optional thread for debug messages
TELEGRAM_THREAD_ID=0

# Enable/disable MCP message cleanup after agent completion
TG_CLEANUP_MCP_MESSAGES=1
```

### Behavior

- **Multi-user scenario**: User A triggers agent from Chat X → debug messages go to `TELEGRAM_CHAT_ID` (monitoring chat), result goes to Chat X
- **Multi-chat scenario**: Agent runs in Chat A, B, C → all debug messages centralized in `TELEGRAM_CHAT_ID`, results delivered to respective origin chats
- **Cleanup**: When `TG_CLEANUP_MCP_MESSAGES=1`, intermediate MCP messages in `debug_chat_id` are deleted after agent completion

## Use Cases

### Centralized MCP Monitoring

All MCP tool invocations (filesystem operations, sequential thinking, API calls) logged in one place for:
- **Debugging**: Track tool usage patterns across agents
- **Auditing**: Review MCP operations chronologically
- **Performance**: Identify slow/failing tool calls

### Clean User Experience

Users see:
- Welcome banner with agent context
- Typing indicators during execution
- Final structured results
- Error messages if failures occur

Without seeing:
- Raw MCP tool arguments
- Sequential thinking internals
- File system operation details
- Intermediate retry attempts

## Related Features

- [MCP Message Cleanup](mcp-message-cleanup.md) — Auto-delete service messages
- [YAML Formatting](formatting.md) — Readable MCP hook output format
- [MCP Config](mcp_config.md) — External MCP server configuration

## Developer Notes

When modifying message routing:

1. **Never modify `debug_chat_id`** after initialization — it must remain constant throughout execution
2. **`selected_chat_id` is dynamic** — Telegram bot updates it per incoming message
3. **Typing actions must use `selected_chat_id`** — user feedback should appear in origin chat
4. **Service messages tracked in `__service_message_ids`** — enables cleanup after run completion
5. **Error handling swallows Telegram failures** — MCP execution must never crash due to notification errors

---

**Last updated**: 2025-01-22
