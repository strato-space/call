# MCP configuration (mcp_config.yaml)

This repository keeps a sample MCP configuration in `mcp_config.yaml`.
It is intended for external clients (e.g., Claude Desktop) and for MCP bridges and runners, not for the Call runtime API.

- Location: `mcp_config.yaml`
- Shape:
  - Top-level `mcpServers:` mapping.
  - Each entry may define either:
    - A local process: `command`, `args[]`, optional `env{}`.
    - A remote server: `serverUrl` and optional `bridge{ command, args[], env{} }`.
  - Optional tool exposure controls:
    - `tools`: allowlist (string or list). If string, it may be comma/whitespace-separated.
    - `blocked_tool_names` / `blocked_tools`: blocklist (string or list).
- Example (local process):

```yaml
mcpServers:
  fs:
    enabled: true
    command: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
    env: {}
```

- Example (remote with bridge):

```yaml
mcpServers:
  sheets:
    enabled: true
    serverUrl: "https://gsh-mcp.example.com"
    bridge:
      command: uvx
      args: ["--env-file", ".env", "mcp-google-sheets"]
```

## Usage policy

- The Call runtime (CLI, Actions, Telegram bot, and MCP server) now **does** load and interpret `mcp_config.yaml` via `src/call/app/call.py` to initialize both local stdio MCP servers (filesystem, sequential thinking, Telegram routing, voice, time, etc.) and remote Streamable HTTP servers (for example, Google Sheets `gsh`).
- External tools (e.g., Claude Desktop) can still copy enabled entries to their own config; treat `mcp_config.yaml` as the single source of truth for which MCP servers should be active.
- Tests include a basic check to ensure the file exists and is parseable to keep the artifact stable.

## Environment

- You may keep credentials in `.env` and reference them through the `args` or `env` blocks where appropriate.
- Do not hardcode secrets in the YAML; use environment variables or process-level `.env` files.
