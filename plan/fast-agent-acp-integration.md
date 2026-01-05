# Fast-Agent ACP integration (spec + client example)

This note summarizes how ACP works inside fast-agent and shows a minimal ACP client.
Sources: `fast-agent/src/fast_agent/acp/*`, `fast-agent/src/fast_agent/acp/server/agent_acp_server.py`,
`fast-agent/docs/ACP_IMPLEMENTATION_OVERVIEW.md`, `fast-agent/docs/ACP_TESTING.md`,
`fast-agent/docs/ACP_TOOL_CALLS.md`, `fast-agent/docs/ACP_TERMINAL_SUPPORT.md`.

## What fast-agent provides in ACP mode

Fast-agent runs as an ACP server over stdio and exposes agents as ACP "modes".
Use either CLI or programmatic startup:

```bash
fast-agent serve --transport acp --card /path/to/agents --model gpt-4.1-mini
```

```python
from pathlib import Path

fast.load_agents(Path("/path/to/agents"))
await fast.start_server(transport="acp", instance_scope="connection")
```

## Session model (per ACP spec, as implemented)

- `newSession` creates a session_id and chooses an `AgentInstance` based on `instance_scope`.
  - `shared`: all sessions share one instance (shared history and tools).
  - `connection`: new instance per session (isolated history and tools).
  - `request`: new instance per session (similar isolation; created per session by ACP).
- Session state is tracked in `ACPSessionState`:
  - `session_id`, `instance`, `current_agent_name`
  - `prompt_context`, `resolved_instructions`
  - `progress_manager`, `permission_handler`
  - `terminal_runtime`, `filesystem_runtime`
  - `slash_handler`, `acp_context`
- `setSessionMode` updates `current_agent_name` for the session.
- `prompt` routes to the current agent for that session.
- Only one in-flight prompt is allowed per session (`_active_prompts` guard).

## Tool calls, permissions, runtimes

- Tool call updates are sent via `ACPToolProgressManager` (tool_call + tool_call_update).
- Optional tool permissions via `ACPToolPermissionManager` using `session/request_permission`.
- If the client advertises capabilities, fast-agent injects:
  - `ACPTerminalRuntime` for shell execution.
  - `ACPFilesystemRuntime` for read/write.
- ACP streaming is sent via `sessionUpdate` notifications.

## Slash commands and ACP-aware agents

- `SlashCommandHandler` exposes base commands, plus agent-specific commands if the
  active agent implements `ACPAwareMixin`.
- ACP-aware agents can add `acp_commands` and report `acp_mode_info`.
  See `fast-agent/examples/acp/acp_aware_agent.py`.

## Minimal ACP client example (Python)

This is a trimmed version of `fast-agent/docs/ACP_TESTING.md`.

```python
#!/usr/bin/env python3
import asyncio
from acp import InitializeRequest, NewSessionRequest, PromptRequest
from acp.schema import ClientCapabilities, ClientInfo
from acp.stdio import spawn_agent_process
from acp.helpers import text_block


class SimpleClient:
    def __init__(self, conn):
        self.conn = conn

    async def sessionUpdate(self, params):
        # Handle streaming updates (text chunks, tool_call updates, etc.)
        update = params.get("update")
        if update and "text" in update:
            print(update["text"], end="")


async def main() -> None:
    async with spawn_agent_process(
        lambda agent: SimpleClient(agent),
        "fast-agent",
        "serve",
        "--transport",
        "acp",
        "--card",
        "/path/to/agents",
        "--model",
        "gpt-4.1-mini",
    ) as (connection, _process):
        init = await connection.initialize(
            InitializeRequest(
                protocolVersion=1,
                clientCapabilities=ClientCapabilities(
                    fs={"readTextFile": False, "writeTextFile": False},
                    terminal=False,
                ),
                clientInfo=ClientInfo(name="test-client", version="0.1.0"),
            )
        )
        print(f"initialized: {init.agentInfo.name}")

        session = await connection.newSession(NewSessionRequest(mcpServers=[]))
        session_id = session.sessionId
        print(f"session: {session_id}")

        # Optional: change active mode (agent) for this session
        # await connection.setSessionMode(sessionId=session_id, modeId="reporter")

        await connection.prompt(
            PromptRequest(
                sessionId=session_id,
                prompt=[text_block("Hello from ACP")],
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
```

## ACP client example with chat ids [1..10]

```python
#!/usr/bin/env python3
import asyncio
from acp import InitializeRequest, NewSessionRequest, PromptRequest
from acp.schema import ClientCapabilities, ClientInfo
from acp.stdio import spawn_agent_process
from acp.helpers import text_block


class ChatClient:
    def __init__(self, conn):
        self.conn = conn
        self.session_to_chat = {}

    async def sessionUpdate(self, params):
        session_id = params.get("sessionId")
        chat_id = self.session_to_chat.get(session_id, "unknown")
        update = params.get("update")
        if update and "text" in update:
            print(f"[chat {chat_id}] {update['text']}", end="")


async def main() -> None:
    client_holder = {}

    def make_client(agent):
        client = ChatClient(agent)
        client_holder["client"] = client
        return client

    async with spawn_agent_process(
        make_client,
        "fast-agent",
        "serve",
        "--transport",
        "acp",
        "--card",
        "/path/to/agents",
    ) as (connection, _process):
        client = client_holder["client"]

        await connection.initialize(
            InitializeRequest(
                protocolVersion=1,
                clientCapabilities=ClientCapabilities(
                    fs={"readTextFile": False, "writeTextFile": False},
                    terminal=False,
                ),
                clientInfo=ClientInfo(name="chat-client", version="0.1.0"),
            )
        )

        chat_ids = list(range(1, 11))
        chat_sessions = {}
        for chat_id in chat_ids:
            session = await connection.newSession(NewSessionRequest(mcpServers=[]))
            chat_sessions[chat_id] = session.sessionId
            client.session_to_chat[session.sessionId] = chat_id

        active_chat_id = 5
        session_id = chat_sessions[active_chat_id]
        await connection.setSessionMode(sessionId=session_id, modeId="reporter")
        await connection.prompt(
            PromptRequest(
                sessionId=session_id,
                prompt=[text_block("Draft a status update.")],
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
```

## Client responsibilities (if you build your own)

- Call `initialize` with capabilities and client info.
- Create a session with `newSession`.
- Track `session_id` and current mode; call `setSessionMode` to switch agents.
- Send `prompt` requests; handle `sessionUpdate` for streaming content/tool updates.
- Respect the one-in-flight prompt rule per session; reject or queue overlap.
