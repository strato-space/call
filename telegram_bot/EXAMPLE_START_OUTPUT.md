# Example: /start Command Output for Specialized Bots

## For Specialized Bot (e.g., StratoProjectBot)

When user sends `/start` to **StratoProjectBot**, the response will be:

```
🎯 StratoProject

Головной orchestrator для PM-пакета. Вызывает PM-Router для построения routing-item массива,
определяет нужный PM-агент (1-11) и итерирует по каждому элементу (по одному за раз).

---

call-bot

Commands:
- /call [--echo] @Name <input>
- /call [--echo] Name <input>  (equivalent to @Name)
- /agents [--aliases] [--q "filter"]
- /clear [@Name]  (clear conversation session for current chat/thread; all agents if name omitted)
- /reload  (rescan repositories and rebuild repo index)

Startup options:
- --bot-name Name  (token lookup: TELEGRAM_TOKEN.Name in env/.env; if --bot-name is not provided, falls back to TELEGRAM_TOKEN)

Plain text (no slash):
- In private chat: 
  - "@Name <input>" is equivalent to "/call @Name <input>"
  - "plain text" is equivalent to "/call <text>" (input-only)
  - For specialized bots: plain text invokes project.md as default target
- In groups: only explicit "@Name <input>" or "@BotName <input>" is handled.
- Bot mention without target: "@BotName <input>" invokes project.md for specialized bots.

Special cases:
- If this bot is AgentFabBot, default agent is AgentFab when no name is specified (e.g., "@ <input>").
    
Notes:
- /agents lists one name per line as @Name.
- With --aliases, alias lines are indented with two spaces before @ (e.g., "  @Alias").
```

---

## For Universal Bot (StratoSpaceAiBot)

When user sends `/start` to **StratoSpaceAiBot**, the response will be:

```
call-bot

Commands:
- /call [--echo] @Name <input>
- /call [--echo] Name <input>  (equivalent to @Name)
- /agents [--aliases] [--q "filter"]
- /clear [@Name]  (clear conversation session for current chat/thread; all agents if name omitted)
- /reload  (rescan repositories and rebuild repo index)

Startup options:
- --bot-name Name  (token lookup: TELEGRAM_TOKEN.Name in env/.env; if --bot-name is not provided, falls back to TELEGRAM_TOKEN)

Plain text (no slash):
- In private chat: 
  - "@Name <input>" is equivalent to "/call @Name <input>"
  - "plain text" is equivalent to "/call <text>" (input-only)
  - For specialized bots: plain text invokes project.md as default target
- In groups: only explicit "@Name <input>" or "@BotName <input>" is handled.
- Bot mention without target: "@BotName <input>" invokes project.md for specialized bots.

Special cases:
- If this bot is AgentFabBot, default agent is AgentFab when no name is specified (e.g., "@ <input>").
    
Notes:
- /agents lists one name per line as @Name.
- With --aliases, alias lines are indented with two spaces before @ (e.g., "  @Alias").
```

---

## Implementation Details

### Goal Extraction Logic

**Code location:** `telegram_bot/bot.py:651-670`

**Process:**
1. Check if `PROJECT_NAME` exists (specialized bot)
2. Load project via `call_api.list(project=PROJECT_NAME)`
3. Read project card via `call_api.read(PROJECT_NAME)`
4. Extract `goal` from YAML metadata using regex:
   - Pattern: `goal:\s*[|>-]\s*\n((?:\s+.+\n?)+)`
   - Captures multi-line YAML literal block
5. Format output: `🎯 {PROJECT_NAME}\n\n{goal_text}\n\n---\n`

### Error Handling

- **Project not found**: Silently skip goal display, show only base help
- **Card read failure**: Log debug message, continue with base help
- **Goal parsing failure**: Skip goal section, show base help

### Benefits

✅ **Contextual introduction**: Users immediately see what the bot does
✅ **Goal from source**: No hardcoded descriptions, always in sync with project card
✅ **Universal compatibility**: Works for any specialized bot with project.md
✅ **Graceful degradation**: Falls back to base help if goal unavailable
