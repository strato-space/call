import importlib


def test_list_hierarchical(monkeypatch):
    mod = importlib.import_module("call.lib.api")

    # Fake projects.yaml index and scans
    monkeypatch.setattr(mod, "load_projects_index", lambda: ["AgentFab", "UxFab"])  # two projects

    def _scan(dirpath):
        if str(dirpath).endswith("AgentFab"):
            return [
                {"type": "agent", "id": "", "name": "AgentFab", "aliases": ["Factory"], "prompts": ["Default"], "path": "/p/AgentFab/agent.yaml"}
            ]
        return [
            {"type": "agent", "id": "", "name": "NewsAggr", "aliases": ["NA"], "prompts": ["Daily", "Weekly"], "path": "/p/UxFab/NewsAggr/agent.yaml"},
            {"type": "agent", "id": "", "name": "DialogSummary", "aliases": [], "prompts": [], "path": "/p/UxFab/DialogSummary/agent.yaml"},
        ]

    monkeypatch.setattr(mod, "scan_project_agents", _scan)

    tree = mod.list()
    assert isinstance(tree, list)
    proj_names = {n["name"] for n in tree}
    assert {"AgentFab", "UxFab"}.issubset(proj_names)
    ux = next(n for n in tree if n["name"] == "UxFab")
    ag_names = {a["name"] for a in ux["agents"]}
    assert {"NewsAggr", "DialogSummary"}.issubset(ag_names)


def test_list_filters_and_wildcards(monkeypatch):
    mod = importlib.import_module("call.lib.api")

    monkeypatch.setattr(mod, "load_projects_index", lambda: ["UxFab"])  # one project
    monkeypatch.setattr(mod, "_scan_project_agents", lambda _: [
        {"type": "agent", "id": "", "name": "NewsAggr", "aliases": ["NA"], "prompts": ["Daily", "Weekly"], "path": "/p/UxFab/NewsAggr/agent.yaml"},
        {"type": "agent", "id": "", "name": "DialogSummary", "aliases": [], "prompts": ["Short"], "path": "/p/UxFab/DialogSummary/agent.yaml"},
    ])

    # Filter by agent wildcard
    tree = mod.list(project="UxFab", agent="News*")
    assert len(tree) == 1
    agents = tree[0]["agents"]
    assert len(agents) == 1 and agents[0]["name"] == "NewsAggr"

    # Filter by prompt wildcard
    tree2 = mod.list(project="UxFab", prompt="Week*")
    assert any(a["name"] == "NewsAggr" for a in tree2[0]["agents"])


def test_resolve_agent(monkeypatch):
    mod = importlib.import_module("call.lib.api")
    monkeypatch.setattr(mod, "load_projects_index", lambda: ["UxFab"])  # one project
    monkeypatch.setattr(mod, "scan_project_agents", lambda _: [
        {"type": "agent", "id": "", "name": "NewsAggr", "aliases": ["NA"], "prompts": ["Daily", "Weekly"], "path": "/p/UxFab/NewsAggr/agent.yaml"},
    ])

    ok = mod.resolve_agent(project="UxFab", agent="NewsAggr")
    assert ok.get("ok") is True
    resolved = ok.get("resolved") or {}
    assert resolved.get("project") == "UxFab"
    assert resolved.get("name") == "NewsAggr"
