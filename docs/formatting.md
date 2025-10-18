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
   - Only in non-DEBUG_MODE
   - Truncate at `max_len - 3` and append `...`
   - Strip trailing newlines

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
```

Look for:

- ✅ Long strings remain intact
- ✅ Multiline content uses `|` block scalar
- ✅ No unexpected line breaks
- ✅ Proper indentation preserved

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

## Related Code Locations

- `app/call.py:64-109` — `_LiteralYamlDumper` and `_dump_yaml_literal()`
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

This approach balances human readability with machine parseability and makes debug logs easy to understand and copy-paste.
