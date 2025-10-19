#!/usr/bin/env python3
"""Test YAML formatting for read_text_file-like content."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from call.app.call import _dump_yaml_literal

# Simulate what read_text_file returns
# This is what comes from MCP - already has real newlines
file_content_real_newlines = """# PM Status Report Mapping Configuration
# Матрица соответствия для автоматических отчётов
# Последнее обновление: 2025-10-16
# Источник данных: MCP tg-ro (list_chats, list_topics), voice (projects)
# Инструкции: prompt/draft/220-mapping-instructions.yaml

# ============================================================================
# ЧАТЫ ДЛЯ ОТЧЁТОВ
# ============================================================================

report_chats:
  # ==========================================================================
  # PRODUCTION: STR | Oper Auto
  # ==========================================================================
  - name: STR | Oper Auto
    chat_id: 3100424032
    mode: production
    description: Основной production чат для операционных отчётов
    
    topics:
      - name: General
        thread_id: 1
        description: Общие операционные вопросы
        excluded: true  # Топик не участвует в обработке
        projects: []
        source_chats: []
"""

# Test 1: MCP-like structure with real newlines
print("=" * 80)
print("Test 1: Content with REAL newlines (like from read_text_file)")
print("=" * 80)

mcp_result = {
    "meta": None,
    "content": [
        {
            "type": "text",
            "text": file_content_real_newlines
        }
    ]
}

print(f"String length: {len(file_content_real_newlines)}")
print(f"Real newlines (chr(10)): {file_content_real_newlines.count(chr(10))}")
print(f"Escaped \\n sequences: {file_content_real_newlines.count(chr(92) + 'n')}")
print()

result = _dump_yaml_literal(mcp_result)
print("YAML output:")
print(result)
print()

# Check if output is correct
if "text: |" in result or "text: |-" in result:
    print("✅ SUCCESS: Using literal block scalar style")
else:
    print("❌ FAIL: Not using literal block scalar style")
    if 'text: "' in result:
        print("   Using quoted string style (BAD)")

print()
print("=" * 80)

# Test 2: Simulate JSON double-encoded string (for comparison)
import json
json_encoded = json.dumps(file_content_real_newlines)
print(f"Test 2: JSON-encoded representation: {json_encoded[:100]}...")
print(f"Has backslash-n: {('\\n' in json_encoded)}")
