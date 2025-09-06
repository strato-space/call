import types
import importlib


def test_list_flat_and_filtered(monkeypatch):
    mod = importlib.import_module("call.lib.api")

    # Fake indices
    fake = {
        "agents": {"NewsAggr": "/p/agents/NewsAggr/agent.yaml", "DialogSummary": "/p/agents/DialogSummary/agent.yaml"},
        "aliases": {"NA": "/p/agents/NewsAggr/agent.yaml"},
        "agents_af": {"AgentFabOne": "/p/AgentFab/AgentFabOne/agent.yaml"},
        "agents_ag": {"NewsAggr": "/p/agents/NewsAggr/agent.yaml", "DialogSummary": "/p/agents/DialogSummary/agent.yaml"},
    }

    monkeypatch.setattr(mod, "_read_indices", lambda: fake)

    # Flat default
    items = mod.list()
    assert isinstance(items, list)
    names = [x["name"] for x in items]
    assert "AgentFabOne" in names
    assert "NewsAggr" in names
    assert "DialogSummary" in names

    # Filter query
    filtered = mod.list(query="news")
    assert all("news" in x["name"].lower() or "news" in x["path"].lower() for x in filtered)
    assert any(x["name"] == "NewsAggr" for x in filtered)

    # Include aliases adds aliases field
    with_aliases = mod.list(include_aliases=True)
    for x in with_aliases:
        assert "aliases" in x
        if x["name"] == "NewsAggr":
            assert "NA" in x["aliases"]


def test_list_grouped(monkeypatch):
    mod = importlib.import_module("call.lib.api")

    fake = {
        "agents": {"NewsAggr": "/p/agents/NewsAggr/agent.yaml"},
        "aliases": {},
        "agents_af": {"AgentFabOne": "/p/AgentFab/AgentFabOne/agent.yaml"},
        "agents_ag": {"NewsAggr": "/p/agents/NewsAggr/agent.yaml"},
    }

    monkeypatch.setattr(mod, "_read_indices", lambda: fake)

    grouped = mod.list(grouped=True)
    assert isinstance(grouped, dict)
    assert set(grouped.keys()) == {"AgentFab", "agents"}
    af = grouped["AgentFab"]
    ag = grouped["agents"]
    assert any(x["name"] == "AgentFabOne" for x in af)
    assert any(x["name"] == "NewsAggr" for x in ag)
