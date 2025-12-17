# YAML Formatting in Call MCP Hooks

## Overview

This document describes the YAML formatting strategy used in Call's MCP hook debug output and tool result logging. The goal is to provide readable, unbroken YAML output that preserves long strings, multiline content, and complex nested structures.

## Core Principles

1. **No artificial line breaks**: Long strings should never be broken mid-line
2. **Preserve multiline content**: Strings with newlines use literal block scalar (`|`) style
3. **Escape handling**: Properly convert JSON escape sequences (`\n`, `\t`, etc.) to actual characters
4. **Redaction**: Remove verbose `content` fields when `structuredContent` is present
5. **Consistent width**: Use very large width limit (999999) to prevent wrapping

## Implementation

### 1. Custom YAML Dumper

```python
class _LiteralYamlDumper(yaml.SafeDumper):
    """YAML dumper that renders multiline strings as block scalars."""
```

**String representation logic:**

- Multiline strings (`\n` present) → literal block scalar style (`|`)
- Long single-line strings (>100 chars) → converted to literal style by adding trailing newline
- Strings starting with YAML special chars (`#-:>|&*![]{}?@`\`) → single-quoted style (`'`)
- Short strings → plain style (no quotes)

**Configuration:**

```python
yaml.dump(
    obj,
    Dumper=_LiteralYamlDumper,
    allow_unicode=True,      # Support non-ASCII characters
    sort_keys=False,         # Preserve insertion order
    default_flow_style=False, # Use block style, not inline
    width=999999,            # Prevent line wrapping
    line_break="\n",         # Unix line endings
)
```

### 2. Main Formatting Function

#### `_dump_yaml_literal(obj, *, width=999999) -> str`

**Purpose**: Convert Python objects to readable YAML

**Fallback chain:**

1. Try custom `_LiteralYamlDumper`
2. Fall back to `yaml.safe_dump` with same settings
3. Fall back to `json.dumps` with indent=2
4. Fall back to `str(obj)`

**Width parameter**: Default is 999999 to prevent any line wrapping. This is critical for preserving long strings intact.

### 3. Tool Result Formatting

#### `_format_tool_result(result, *, max_len=4000) -> str`

**Processing pipeline:**

1. **Pydantic model conversion** (`_dump_like_mapping`):
   - Recursively convert models using `.model_dump()` or `.dict()`
   - Handle nested collections (dict, list, tuple, set)

2. **Unescape strings** (`_unescape_strings`):
   - Convert `\\n` → newline
   - Convert `\\t` → tab
   - Convert `\\r` → carriage return
   - Convert `\\"` → `"`
   - Convert `\\'` → `'`
   - Preserve literal backslashes (`\\\\` → `\`)

3. **Redact verbose content** (`_redact_structured_content`):
   - When `structuredContent` field exists and is non-empty
   - Remove sibling `content` field to reduce verbosity
   - Recursively process nested structures

4. **Serialize to YAML**:
   - Use `_dump_yaml_literal()` for dicts, lists, tuples, sets
   - Fall back to `json.dumps` if YAML fails
   - For bytes/strings, decode/pass through directly

5. **Collapse whitespace**:
   - Normalize line endings: `\r\n` → `\n`
   - Collapse multiple consecutive newlines to single newline: `\n{2,}` → `\n`

6. **Truncate if needed**:
   - Only when `CALL_DEBUG=0` (for display in Telegram/logs)
   - Truncate at `max_len - 3` and append `...`
   - Strip trailing newlines
   - **Important**: Truncation affects ONLY display, NOT agent pipeline data

### 4. Tool Arguments Formatting

#### `_to_yaml_text(obj) -> str` (nested in `call_tool`)

**Purpose**: Format MCP tool arguments for debug output

**Processing:**

1. **Unescape arguments** (`_deep_unescape`):
   - Recursively process strings, lists, tuples, sets, dicts
   - Convert escape sequences to actual characters

2. **Serialize to YAML**:
   - First attempt: `_dump_yaml_literal(prepared)` with default width
   - Second attempt: Round-trip through JSON, then YAML
   - Third attempt: JSON with manual escape replacement
   - Final fallback: `str(obj)`

**Used in two places:**

- Debug print: `[MCP Hook] Arguments (YAML):\n...`
- Telegram service message: `🛠️ {tool_name}\n\n{yaml_text}`

### 5. Agent-as-Tool Invocation Messages

#### `_wrapped_on_invoke` (nested in `_wrap_agent_tool_debug`)

**Purpose**: Format agent-as-tool invocation payloads for Telegram debug messages

**Context**: When a parent agent calls a child agent through the agents-as-tools mechanism, a debug message is sent to Telegram showing the invocation details.

**Processing:**

1. **Parse input**: Convert JSON string input to Python dict
2. **Format as YAML**:
   - First attempt: `_dump_yaml_literal(js, width=10000)` for YAML output
   - Fallback: `json.dumps(js, ensure_ascii=False, indent=2)` if YAML fails
   - Language tag in output adjusted accordingly (`yaml` or `json`)
3. **Truncate if needed**: Limit to 1500 characters with `...` suffix
4. **Wrap in HTML**: Use Telegram HTML format with syntax highlighting

**Code location**: `app/call.py:1154-1179`

**Telegram message format:**
```
🛠️ {child_agent_name}
from {parent_agent_name}

<pre><code class="language-yaml">
{yaml_payload}
</code></pre>
```

**Why YAML instead of JSON:**

1. **Better readability**: Multiline strings displayed with proper line breaks
2. **Consistency**: Matches MCP tool debug output format
3. **Reduced visual noise**: No JSON escape sequences (`\n`, `\t`, etc.)
4. **Easier debugging**: Can copy-paste YAML directly for testing
5. **User preference**: Request from user to standardize on YAML for all debug output

**Example output:**

```yaml
input: день
context: null
replay: null
```

vs the old JSON format:

```json
{
  "input": "день",
  "context": null,
  "replay": null
}
```

**Fallback behavior**: If YAML formatting fails (e.g., PyYAML not installed or serialization error), automatically falls back to JSON with proper error handling, ensuring debug messages always reach Telegram.

## Debug Output Examples

### Good: Properly Formatted Long String

```yaml
[MCP Hook][fs] Tool read_text_file returned:
meta: null
content:
- type: text
  text: |
    # PM Input/Output Format Specification
    
    **Source of Truth для всех PM-агентов (PM-1..PM-11)**
    
    ---
    
    ## Input Format
    
    Каждый PM-агент получает routing context от **PM.md** (головной orchestrator):
    
    ```json
    {
      "input": "исходный user input",
      "context": "исходный context",
      "replay": "исходный replay",
      "topic": "Название топика",
      "interval": {
        "from": "YYYY-MM-DD",
        "to": "YYYY-MM-DD"
      },
      "sources": {
        "chats": [
          {"id": "chat_id", "name": "chat_name"}
        ]
      }
    }
    ```
```

### Bad: Broken String (Old Behavior with width=10000)

```yaml
[MCP Hook][fs] Tool read_text_file returned:
meta: null
content:
- type: text
  text: "# PM Input/Output Format Specification\n\n**Source of Truth для всех PM-агентов (PM-1..PM-11)**\n\n---\n\n## Input Format\n\nКаждый PM-агент получает routing context от **PM.md** (головной orchestrator):\n\n```json\n{\n  \"input\": \"исходный user input\",\n  \"context\": \"исходный context\",  \n  \"replay\": \"исходный replay\",\n  \"topic\": \"Название топика\",\n  \"interval\": {\n    \"from\": \"YYYY-MM-DD\",\n    \"to\": \"YYYY-MM-DD\"\n  },\n  \"sources\": {\n    \"chats\": [\n  
    {\"id\": \"chat_id\", \"name\":"
```

**Problem**: String exceeds width limit and breaks mid-line with awkward continuation.

## Configuration Changes

### Before (Broken)

```python
def _dump_yaml_literal(obj: Any, *, width: int = 10000) -> str:
    # ...
    return yaml.dump(..., width=width, ...)

def _to_yaml_text(obj) -> str:
    # ...
    return _dump_yaml_literal(prepared, width=10000)  # ❌ Hardcoded!
```

### After (Fixed)

```python
def _dump_yaml_literal(obj: Any, *, width: int = 999999) -> str:
    # ...
    return yaml.dump(..., width=width, ...)

def _to_yaml_text(obj) -> str:
    # ...
    return _dump_yaml_literal(prepared)  # ✅ Uses default 999999
```

## Why 999999 Width?

- PyYAML's `width` parameter controls when to break long scalars
- Setting to a very large value (999999) effectively disables line wrapping
- Still allows proper indentation and structure
- Preserves readability while preventing mid-string breaks

## Long Single-Line String Handling

**Problem**: PyYAML's default behavior can wrap long plain scalars at arbitrary points, even with large width settings, making output unreadable.

**Solution**: Force literal block scalar style (`|`) for strings longer than 100 characters by artificially adding a trailing newline:

```python
if len(data) > 100:
    # Add newline to force literal style
    # Dumper automatically uses |- which strips trailing newlines on parse
    modified_data = data + "\n"
    return dumper.represent_scalar("tag:yaml.org,2002:str", modified_data, style="|")
```

**Benefits**:

- Long strings never wrap mid-line
- Preserves exact content (trailing newline is stripped by `|-` style)
- Consistent with multiline string handling
- Improves readability for file contents, JSON payloads, etc.

**Example**:

```yaml
# Before (plain scalar, may wrap):
chat_id: -1002710557620

# After (literal scalar, never wraps):
chat_id: |
  -1002710557620
```

## Testing

After changes, verify with:

```bash
# Run agents-as-tools tests
pytest app/tests/test_agents_tool_wrapper.py -v

# Enable debug mode and watch MCP hook output
CALL_DEBUG=1 python -m call.telegram_bot.bot --bot-name TestBot

# Test agent-as-tool YAML formatting specifically
python test_read_file_yaml.py
```

Look for:

- ✅ Long strings remain intact
- ✅ Multiline content uses `|` block scalar
- ✅ No unexpected line breaks
- ✅ Proper indentation preserved
- ✅ Agent-as-tool messages in Telegram show YAML format (not JSON)
- ✅ Fallback to JSON works when YAML fails

## Common Issues

### Issue 1: String Breaks Mid-Line

**Symptom**: Output shows broken string with continuation indent

**Cause**: `width` parameter too small or hardcoded

**Solution**: Ensure all `_dump_yaml_literal()` calls use default width (999999)

### Issue 2: Escaped Characters Not Rendered

**Symptom**: Output shows literal `\n`, `\t` instead of newlines/tabs

**Cause**: Missing `_unescape_strings()` processing

**Solution**: Apply unescape before YAML serialization

### Issue 3: Verbose Nested Content

**Symptom**: Large `content` arrays when `structuredContent` present

**Cause**: No redaction logic

**Solution**: Use `_redact_structured_content()` to filter verbose fields

### Issue 4: Agent-as-Tool Messages Still Show JSON

**Symptom**: Telegram debug messages for agent-as-tool invocations display JSON instead of YAML

**Cause**: Code not updated or fallback to JSON triggered

**Solution**: 
1. Verify `_dump_yaml_literal()` is called in `_wrapped_on_invoke()` at `app/call.py:1166`
2. Check that PyYAML is installed: `pip list | grep -i yaml`
3. Review error logs for YAML serialization failures
4. Verify `lang` variable is set to `"yaml"` when YAML succeeds

### Issue 5: MCP Hook Output Shows Quoted Strings with Line Breaks

**Symptom**: MCP Hook debug output shows quoted strings (`"string\n..."`) with mid-line breaks instead of literal block scalars (`|`)

**Example from real output (2025-10-19, fs:read_text_file):**
```yaml
# ❌ WRONG - current behavior
text: "# PM Status Report Mapping Configuration\n# Матрица соответствия для 
автоматических отчётов\n# Последнее обновление...
```

**Expected behavior:**
```yaml
# ✅ CORRECT - should use literal block scalar
text: |
  # PM Status Report Mapping Configuration
  # Матрица соответствия для автоматических отчётов
  # Последнее обновление: 2025-10-16
  ...
```

**Cause**: PyYAML Emitter may fall back to quoted style for very long strings even when representer specifies `style="|"`, especially if:
1. String contains trailing whitespace on some lines
2. String has very long lines that exceed internal buffer limits
3. Representer `represent_scalar()` call raises an exception

**Debug indicators** (added in latest version):
- `[YAML Representer] Using literal style` — Representer chose literal block scalar
- `[YAML Representer] Literal style failed: ...` — `represent_scalar()` call raised exception
- `[YAML Dump Result] has_literal=False, has_quoted=True` — Final output is quoted, not literal
- `[YAML Dump] _LiteralYamlDumper failed: ...` — Entire `yaml.dump()` failed, falling back to `safe_dump`

**Solution** (fixed 2025-10-19):
1. Override `choose_scalar_style()` method in `_LiteralYamlDumper` to force literal style
2. PyYAML Emitter has hardcoded heuristics that ignore style hints for strings >1024 chars
3. By overriding `choose_scalar_style()`, we bypass these heuristics
4. Strip trailing whitespace from each line before serialization (helps PyYAML accept literal style)
5. Result: literal block scalar (`|`) is now always used for multiline strings, regardless of length

**Previous workarounds** (no longer needed):
- ~~Verify PyYAML version~~ — issue was in Emitter logic, not version
- ~~Check for trailing whitespace~~ — now stripped automatically
- ~~Enable debug mode~~ — still useful for diagnostics

**Fixed bugs** (2025-10-19):
- **Critical**: PyYAML Emitter ignored style hints for strings >1024 chars
  - Root cause: Emitter has hardcoded heuristics that override representer style hints
  - Solution: Override `choose_scalar_style()` in `_LiteralYamlDumper` to force literal style
  - Result: All multiline strings now use literal block scalar (`|`) regardless of length
- Strip trailing whitespace from each line to help PyYAML accept literal style
- Added exception handling in `_literal_yaml_str_representer()` to catch and log failures
- Added result validation in `_dump_yaml_literal()` to detect quoted vs literal output
- Added debug logging for YAML formatting diagnostics

## Related Code Locations

- `app/call.py:64-109` — `_LiteralYamlDumper` and `_dump_yaml_literal()`
- `app/call.py:1154-1179` — `_wrapped_on_invoke()` for agent-as-tool debug messages
- `app/call.py:2770-2898` — `_format_tool_result()` with escape/redact pipeline
- `app/call.py:3003-3022` — `_to_yaml_text()` for tool arguments
- `app/call.py:3024-3025` — Debug print of arguments
- `app/call.py:3050-3055` — Debug print of tool results

## Best Practices

1. **Always use default width**: Never hardcode `width=10000` or similar
2. **Preserve order**: Keep `sort_keys=False` to match original structure
3. **Handle escapes**: Always unescape JSON escape sequences before YAML dump
4. **Test with long strings**: Verify formatting with file contents, markdown, JSON payloads
5. **Monitor logs**: Check debug output regularly during development

## Summary

The YAML formatting system in Call ensures:

- Readable, properly indented YAML output
- No artificial line breaks in long strings (both multiline and single-line >100 chars)
- Multiline content uses literal block scalars (`|` or `|-`)
- Long single-line strings (>100 chars) converted to literal style to prevent wrapping
- Escape sequences properly converted
- Verbose content redacted when appropriate
- Consistent width (999999) prevents wrapping issues
- All strings roundtrip correctly (trailing newlines stripped by `|-`)

**Application points:**

1. **MCP Hook Debug Output** — Tool arguments and results logging
2. **Agent-as-Tool Messages** — Telegram debug messages when parent agent calls child agent
3. **Tool Result Formatting** — Processing and displaying MCP tool responses
4. **Telegram Bot Payloads** — `[bot][PAYLOAD]` debug messages that dump the
   `{target, replay, input, context}` envelope. `context` may contain Telegram
   attachment descriptors (e.g. `type: resource_link` items with Telegram file
   URLs). See *Telegram Context Extraction Details* in `README.md` and
   `tg-user-guide*.md` for concrete examples.

This approach balances human readability with machine parseability and makes debug logs easy to understand and copy-paste. The consistent use of YAML across all debug output (MCP tools and agents-as-tools) provides a unified debugging experience.

## Automatic JSON-to-YAML Conversion for Agent Output

### Auto-Format Overview

When an agent returns JSON arrays or objects as the final output (identified by first and last characters being `[]` or `{}`), the system automatically converts them to YAML format for improved readability in Telegram messages.

### Detection Logic

**Trigger conditions:**
1. Output text is non-empty string
2. After stripping whitespace, first character is `[` or `{`
3. After stripping whitespace, last character is `]` or `}`

**Example inputs that trigger conversion:**
```json
[{"id": 1, "name": "Item"}]
{"status": "ok", "data": [...]}
  [1, 2, 3]  
```

### Conversion Process

Located in `send_digest_notification()` at `app/call.py`:

```python
# Auto-format JSON arrays/objects as YAML for readability
stripped = text.strip()
if stripped and ((stripped[0] == '[' and stripped[-1] == ']') or 
                  (stripped[0] == '{' and stripped[-1] == '}')):
    parsed = json.loads(stripped)
    yaml_text = _dump_yaml_literal(parsed)
    text = f"<pre><code class=\"language-yaml\">{yaml_text}</code></pre>"
```

### Output Format

**Before (raw JSON):**
```json
[{"type":"routing-item","topic":"VoiceBot UI","sources":[{"project":{"project_id":"123","name":"CRM Voice"}}]}]
```

**After (YAML in code block):**
```yaml
- type: routing-item
  topic: VoiceBot UI
  sources:
  - project:
      project_id: '123'
      name: CRM Voice
```

### Benefits

1. **Readability**: YAML indentation makes nested structures easier to scan
2. **Telegram rendering**: Code blocks with `language-yaml` provide syntax highlighting
3. **Copy-paste friendly**: YAML format is easier to read and edit
4. **Automatic**: No agent modification needed - works for any JSON output

### Error Handling

If JSON parsing fails, the original text is preserved:
- Invalid JSON syntax → send as-is
- Conversion error → log debug message, send original
- Preserves robustness for edge cases

### Implementation Notes

- Uses existing `_dump_yaml_literal()` function (same as MCP tool formatting)
- Logged when conversion succeeds: `[format] Converted JSON array/object to YAML format`
- Errors logged at debug level to avoid noise

## Agent Input/Output Formatting Rules

### Agent-as-Tool Invocations

**Input format expectation:**
- Agents receive JSON objects with structured fields at the **top level**
- Example correct input structure:
  ```json
  {
    "input": "user query",
    "context": "additional context",
    "topic": "Project Name",
    "interval": {"from": "2025-10-17", "to": "2025-10-17"},
    "sources": {...},
    "output": {...}
  }
  ```

**Telegram debug message format:**
- Displays the input JSON object (parsed from string parameter)
- Uses YAML formatting for readability (with JSON fallback)
- Shows structured fields at top level, NOT wrapped in `{"input": "JSON string"}`

**Common mistake:**
```json
❌ WRONG:
{
  "input": "{\"input\":\"query\",\"context\":\"\",\"topic\":\"...\",...}"
}
```
This indicates double serialization - the entire payload was stringified and wrapped in an `input` field.

**Correct display:**
```yaml
✅ CORRECT:
input: user query
context: additional context
topic: Project Name
interval:
  from: 2025-10-17
  to: 2025-10-17
sources:
  chats:
    - chat_id: "123456789"
      name: Chat Name
```

**Code implementation:**
- `_wrapped_on_invoke()` at `app/call.py:1164` parses `input` string parameter: `json.loads(input)`
- Result is formatted as YAML (line 1199) or JSON fallback (line 1203)
- Telegram message shows the **parsed object**, not the raw string

### MCP Tool Results

**Verified correct formatting (as of 2025-10-19):**

- ✅ **`time:get_current_time`** — Returns YAML-formatted object with `timezone`, `datetime`, `day_of_week`, `is_dst`
- ✅ **`tg-ro:list_messages`** — Returns YAML with `meta`, `content`, `annotations`, `structuredContent`  
- ✅ **`fs:read_text_file`** — Returns YAML with `meta`, `content[].text` using literal block scalar `|` for file contents

These tools demonstrate proper YAML literal formatting for multiline content and structured data.
