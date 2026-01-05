# Fast-Agent programmatic integration (AgentCards + streaming)

This note documents how to embed fast-agent in code. It mirrors:
`fast-agent go --card <dir> --watch` plus streaming and direct agent calls.

## End-to-end example (load + watch + stream + call)

```python
import asyncio
from dataclasses import dataclass
from pathlib import Path

from fast_agent import FastAgent
from fast_agent.llm.stream_types import StreamChunk


class AgentSession:
    def __init__(self, app, on_chunk, on_tool) -> None:
        self._app = app
        self._on_chunk = on_chunk
        self._on_tool = on_tool
        self._active_name = None
        self._remove_stream = None
        self._remove_tool = None

    def switch(self, agent_name: str) -> None:
        if agent_name == self._active_name:
            return
        if self._remove_stream:
            self._remove_stream()
            self._remove_stream = None
        if self._remove_tool:
            self._remove_tool()
            self._remove_tool = None

        self._active_name = agent_name
        active_agent = self._app[agent_name]
        self._remove_stream = active_agent.add_stream_listener(self._on_chunk)
        self._remove_tool = active_agent.add_tool_stream_listener(self._on_tool)

    async def send(self, message: str) -> str:
        if not self._active_name:
            raise RuntimeError("No active agent selected; call switch() first.")
        return await self._app.send(message, agent_name=self._active_name)

    def close(self) -> None:
        if self._remove_stream:
            self._remove_stream()
            self._remove_stream = None
        if self._remove_tool:
            self._remove_tool()
            self._remove_tool = None


@dataclass
class ChatSessionState:
    chat_id: int
    session: AgentSession
    current_agent: str
    task: asyncio.Task | None = None


class ChatSessionManager:
    def __init__(self, app, on_chunk_factory, on_tool_factory) -> None:
        self._app = app
        self._on_chunk_factory = on_chunk_factory
        self._on_tool_factory = on_tool_factory
        self._sessions: dict[int, ChatSessionState] = {}
        self._lock = asyncio.Lock()
        self._active_chats: set[int] = set()
        self._tasks: dict[int, asyncio.Task] = {}

    async def get_or_create(self, chat_id: int) -> ChatSessionState:
        async with self._lock:
            state = self._sessions.get(chat_id)
            if state:
                return state
            session = AgentSession(
                self._app,
                self._on_chunk_factory(chat_id),
                self._on_tool_factory(chat_id),
            )
            state = ChatSessionState(
                chat_id=chat_id,
                session=session,
                current_agent="default",
            )
            state.session.switch(state.current_agent)
            self._sessions[chat_id] = state
            return state

    async def send(
        self, chat_id: int, message: str, *, agent_name: str | None = None
    ) -> str:
        state = await self.get_or_create(chat_id)
        if chat_id in self._active_chats:
            raise RuntimeError(f"Chat {chat_id} already has an active request.")
        if agent_name and agent_name != state.current_agent:
            state.current_agent = agent_name
            state.session.switch(agent_name)

        self._active_chats.add(chat_id)
        task = asyncio.create_task(state.session.send(message))
        self._tasks[chat_id] = task
        state.task = task
        try:
            return await task
        finally:
            self._active_chats.discard(chat_id)
            self._tasks.pop(chat_id, None)
            state.task = None

    async def cancel(self, chat_id: int) -> None:
        async with self._lock:
            task = self._tasks.get(chat_id)
        if task:
            task.cancel()

    def close_all(self) -> None:
        for state in self._sessions.values():
            state.session.close()


async def main() -> None:
    cards_dir = Path("/path/to/agents")  # folder with .md/.yaml AgentCards

    fast = FastAgent(
        name="Embedded FastAgent",
        parse_cli_args=False,  # avoid argparse conflicts in host apps
        quiet=True,
    )

    # Equivalent to: --card <dir>
    fast.load_agents(cards_dir)

    # Equivalent to: --watch (reloads on change while run() is active)
    fast.args.watch = True

    async with fast.run() as agent:
        def make_on_chunk(chat_id: int):
            def on_chunk(chunk: StreamChunk) -> None:
                if chunk.text:
                    print(f"[chat {chat_id}] {chunk.text}", end="", flush=True)

            return on_chunk

        def make_on_tool(chat_id: int):
            def on_tool(event: str, payload: dict | None) -> None:
                print(f"\n[chat {chat_id}][tool:{event}] {payload}")

            return on_tool

        # Replace with your real agent names.
        manager = ChatSessionManager(agent, make_on_chunk, make_on_tool)

        chat_ids = list(range(1, 11))
        for chat_id in chat_ids:
            await manager.get_or_create(chat_id)

        active_chat_id = 5
        try:
            response = await manager.send(active_chat_id, "Hello from the default agent.")
            print("\nresponse:", response)

            report = await manager.send(
                active_chat_id,
                "Draft a status update.",
                agent_name="reporter",
            )
            print("reporter:", report)
        finally:
            manager.close_all()


if __name__ == "__main__":
    asyncio.run(main())
```

Notes:
- Watching is active only while `fast.run()` is open.
- If `watchfiles` is missing, fast-agent falls back to polling.
- Use concrete agent names when attaching listeners, even if a card sets `default: true`.
- ChatSessionManager mirrors the ACP rule: one active prompt per session (overlaps raise).

## ACP protocol example (server + client flow)

This mirrors ACP session semantics: one session_id per client, current_mode per session,
and only one active prompt per session at a time.

### Server: run fast-agent in ACP mode

```python
import asyncio
from pathlib import Path

from fast_agent import FastAgent


async def main() -> None:
    cards_dir = Path("/path/to/agents")

    fast = FastAgent(
        name="ACP Server",
        parse_cli_args=False,
        quiet=True,
    )
    fast.load_agents(cards_dir)

    # ACP over stdio. instance_scope controls per-session isolation.
    await fast.start_server(
        transport="acp",
        instance_scope="connection",
        permissions_enabled=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
```

### Client: ACP session flow (protocol-level)

```python
# PSEUDO CODE: use an ACP client library or your own JSON-RPC wrapper.
client = AcpClient.connect_stdio(["python", "acp_server.py"])

session = client.new_session(cwd=".", mcp_servers=[])
session.set_mode("default")

for update in session.prompt("Hello from ACP"):
    if update.text:
        print(update.text, end="")
```

ACP notes:
- new_session returns session_id and available modes (agents).
- set_mode changes the active agent for the session.
- A session can have only one in-flight prompt; concurrent prompts should be rejected.
