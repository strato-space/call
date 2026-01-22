import pytest

from call.lib.utils import parse_metadata_and_prompt


def test_parse_markdown_metadata_and_prompt_blocks():
    text = """\
<!-- METADATA: START -->
```yaml
id: Demo
model: gpt-5
```
<!-- METADATA: END -->

<!-- PROMPT: START -->
Hello world
<!-- PROMPT: END -->
"""

    meta = parse_metadata_and_prompt(text)

    assert meta["id"] == "Demo"
    assert meta["model"] == "gpt-5"
    assert meta["prompt"] == "Hello world"


def test_parse_yaml_card_returns_mapping_without_prompt():
    text = """\
name: PlainYAML
model: gpt-4.1
"""

    meta = parse_metadata_and_prompt(text)

    assert meta == {"name": "PlainYAML", "model": "gpt-4.1"}
    assert "prompt" not in meta


def test_parse_markdown_metadata_without_prompt_falls_back_to_text():
    text = """\
<!-- METADATA: START -->
```yaml
id: Demo
```
<!-- METADATA: END -->

Body after metadata
"""

    meta = parse_metadata_and_prompt(text)

    assert meta["id"] == "Demo"
    assert meta["prompt"] == "Body after metadata"


def test_parse_markdown_with_prompt_only_builds_prompt():
    text = """\
<!-- PROMPT: START -->
Only prompt block
<!-- PROMPT: END -->
"""

    meta = parse_metadata_and_prompt(text)

    assert meta == {"prompt": "Only prompt block"}


def test_parse_plain_text_fallback_uses_entire_body_as_prompt():
    text = "Plain text without markers"

    meta = parse_metadata_and_prompt(text)

    assert meta == {"prompt": "Plain text without markers"}


def test_parse_metadata_missing_yaml_block_raises():
    text = """\
<!-- METADATA: START -->
No fenced yaml
<!-- METADATA: END -->
"""

    with pytest.raises(ValueError):
        parse_metadata_and_prompt(text)
