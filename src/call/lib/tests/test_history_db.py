import mcp.types as mcp_types

from call.lib.history_db import clear_history, load_history, save_history
from call.lib.history_models import HistoryMessage


def test_history_db_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "call.db"
    monkeypatch.setenv("CALL_DB", str(db_path))

    messages = [
        HistoryMessage(
            role="user",
            content=[mcp_types.TextContent(type="text", text="hi")],
        ),
        HistoryMessage(
            role="assistant",
            content=[mcp_types.TextContent(type="text", text="hello")],
            stop_reason="stop",
        ),
    ]

    save_history("c1", "AgentA", "fast-agent", messages)
    loaded = load_history("c1", "AgentA")

    assert [m.model_dump(mode="json") for m in loaded] == [
        m.model_dump(mode="json") for m in messages
    ]


def test_history_db_scopes_per_agent(tmp_path, monkeypatch):
    db_path = tmp_path / "call.db"
    monkeypatch.setenv("CALL_DB", str(db_path))

    save_history(
        "c1",
        "AgentA",
        "fast-agent",
        [HistoryMessage(role="user", content=[mcp_types.TextContent(type="text", text="a")])],
    )
    save_history(
        "c1",
        "AgentB",
        "fast-agent",
        [HistoryMessage(role="user", content=[mcp_types.TextContent(type="text", text="b")])],
    )

    a = load_history("c1", "AgentA")
    b = load_history("c1", "AgentB")

    assert a and a[0].content[0].text == "a"
    assert b and b[0].content[0].text == "b"

    clear_history("c1", "AgentA")
    assert load_history("c1", "AgentA") == []
    assert load_history("c1", "AgentB") != []

