#!/usr/bin/env python3
"""Test YAML literal style for very long strings."""

import yaml

class _LiteralYamlDumper(yaml.SafeDumper):
    """YAML dumper that renders multiline strings as block scalars."""
    
    def choose_scalar_style(self):
        """Override to honor representer style hints."""
        style = super().choose_scalar_style()
        
        # If representer explicitly requested literal (|) or folded (>), honor it
        if self.event.style in ('|', '>'):
            return self.event.style
        
        return style


def _literal_yaml_str_representer(dumper, data):
    if "\n" in data:
        # Strip trailing spaces from each line
        cleaned = "\n".join(line.rstrip() for line in data.split("\n"))
        return dumper.represent_scalar("tag:yaml.org,2002:str", cleaned, style="|")
    
    if len(data) > 100:
        modified_data = data + "\n"
        return dumper.represent_scalar("tag:yaml.org,2002:str", modified_data, style="|")
    
    if data and data[0] in "#-:>|&*![]{}?@`":
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'")
    
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=None)


_LiteralYamlDumper.add_representer(str, _literal_yaml_str_representer)


# Test with very long multiline string (>4000 chars like in the user's case)
long_text = """# PM Input/Output Format Specification

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
      {"chat_id": "123456789", "name": "Chat Name"}
    ],
    "projects": [
      {"project_id": "672315cb537994d86e1c68bf", "name": "Project Name"}
    ],
    "voice_sessions": ["session_id1", "session_id2"],
    "filter_label": "AiDevOps"
  },
  "output": {
    "mcp": "tgbot",
    "tool": "send_message",
    "chat_id": "-100...",
    "thread_id": "123",
    "topic_name": "Название топика"
  }
}
```

### Поля Input

- **input** — оригинальный запрос пользователя (опционально)
- **context** — дополнительный контекст (опционально)
- **replay** — контекст предыдущего диалога (опционально)
- **topic** — название топика из маппинга
- **interval** — временной интервал для анализа
- **sources** — источники данных
- **output** — параметры доставки результата

## Output Format

Используй параметры из полученного атрибута `output` для доставки результатов.
""" * 3  # Multiply to make it >4000 chars

data = {
    "meta": None,
    "content": [
        {
            "type": "text",
            "text": long_text
        }
    ]
}

result = yaml.dump(
    data,
    Dumper=_LiteralYamlDumper,
    allow_unicode=True,
    sort_keys=False,
    default_flow_style=False,
    width=999999,
    line_break="\n",
)

print("=" * 80)
print("YAML OUTPUT:")
print("=" * 80)
print(result[:500])
print("...")
print("=" * 80)

# Check if literal style was used
has_literal = '|' in result[:200] or '|-' in result[:200]
has_quoted = '"' in result[:200] and '\\n' in result[:200]

print(f"Length: {len(result)}")
print(f"Has literal (|): {has_literal}")
print(f"Has quoted with \\n: {has_quoted}")
print()

if has_literal and not has_quoted:
    print("✅ SUCCESS: Literal style used for long multiline string!")
else:
    print("❌ FAIL: Still using quoted style")
