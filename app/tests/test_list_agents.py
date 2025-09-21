import importlib


def test_list_hierarchical(monkeypatch):
    mod = importlib.import_module("call.lib.api")
    repo_mod = importlib.import_module("call.lib.repo_db")

    # Mock DB-backed listing directly
    monkeypatch.setattr(
        repo_mod,
        "list",
        lambda **kwargs: [
            {"name": "AgentFab", "agents": [
                {"type": "agent", "id": "", "name": "AgentFab", "aliases": [], "prompts": ["Default"], "path": "/p/AgentFab/agent.yaml"}
            ]},
            {"name": "UxFab", "agents": [
                {"type": "agent", "id": "", "name": "NewsAggr", "aliases": [], "prompts": ["Daily", "Weekly"], "path": "/p/UxFab/NewsAggr/agent.yaml"},
                {"type": "agent", "id": "", "name": "DialogSummary", "aliases": [], "prompts": [], "path": "/p/UxFab/DialogSummary/agent.yaml"},
            ]},
        ],
        raising=True,
    )

    tree = mod.list()
    assert isinstance(tree, list)
    proj_names = {n["name"] for n in tree}
    assert {"AgentFab", "UxFab"}.issubset(proj_names)
    ux = next(n for n in tree if n["name"] == "UxFab")
    ag_names = {a["name"] for a in ux["agents"]}
    assert {"NewsAggr", "DialogSummary"}.issubset(ag_names)


def test_list_filters_and_wildcards(monkeypatch):
    mod = importlib.import_module("call.lib.api")
    repo_mod = importlib.import_module("call.lib.repo_db")

    def _list(**kwargs):
        agents = [
            {"type": "agent", "id": "", "name": "NewsAggr", "aliases": [], "prompts": ["Daily", "Weekly"], "path": "/p/UxFab/NewsAggr/agent.yaml"},
            {"type": "agent", "id": "", "name": "DialogSummary", "aliases": [], "prompts": ["Short"], "path": "/p/UxFab/DialogSummary/agent.yaml"},
        ]
        agent_pat = (kwargs.get("agent") or "").strip()
        prompt_pat = (kwargs.get("prompt") or "").strip()
        def _match(name: str, pat: str) -> bool:
            if not pat:
                return True
            if "*" in pat:
                import fnmatch
                return fnmatch.fnmatch(name, pat)
            return name == pat
        # apply agent filter
        if agent_pat:
            agents = [a for a in agents if _match(a["name"], agent_pat)]
        # apply prompt filter
        if prompt_pat:
            agents = [a for a in agents if any(_match(p, prompt_pat) for p in (a.get("prompts") or []))]
        return [{"name": "UxFab", "agents": agents}]

    monkeypatch.setattr(repo_mod, "list", _list, raising=True)

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
    repo_mod = importlib.import_module("call.lib.repo_db")
    monkeypatch.setattr(repo_mod, "find_agents", lambda **kw: [
        {"project": "UxFab", "agent": "NewsAggr", "path": "/p/UxFab/NewsAggr/agent.yaml"}
    ])

    ok = mod.resolve_agent(project="UxFab", agent="NewsAggr")
    assert ok.get("ok") is True
    resolved = ok.get("resolved") or {}
    assert resolved.get("project") == "UxFab"
    assert resolved.get("name") == "NewsAggr"


def test_resolve_agent_across_projects_when_project_none(monkeypatch):
    mod = importlib.import_module("call.lib.api")
    repo_mod = importlib.import_module("call.lib.repo_db")

    def _find_agents(**kw):
        p = kw.get("project")
        a = kw.get("agent")
        if (p in (None, "AgentFab")) and a == "BusinessAnalyticAgent":
            return [{"project": "AgentFab", "agent": "BusinessAnalyticAgent", "path": "/p/AgentFab/BusinessAnalyticAgent/agent.yaml"}]
        if (p in (None, "UxFab")) and a == "NewsAggr":
            return [{"project": "UxFab", "agent": "NewsAggr", "path": "/p/UxFab/NewsAggr/agent.yaml"}]
        return []

    monkeypatch.setattr(repo_mod, "find_agents", _find_agents)

    ok = mod.resolve_agent(project=None, agent="BusinessAnalyticAgent")
    assert ok.get("ok") is True
    resolved = ok.get("resolved") or {}
    assert resolved.get("project") == "AgentFab"
    assert resolved.get("name") == "BusinessAnalyticAgent"


def test_list_agentfab_contains_core_agents(monkeypatch):
    import importlib
    mod = importlib.import_module("call.lib.api")
    repo_mod = importlib.import_module("call.lib.repo_db")

    # Force only AgentFab project and return a deterministic agent list
    monkeypatch.setattr(
        repo_mod,
        "list",
        lambda **kwargs: [
            {"name": "AgentFab", "agents": [
                {"type": "agent", "id": "", "name": "AgentFab", "aliases": [], "prompts": ["Default"], "path": "/p/AgentFab/agent.yaml"},
                {"type": "agent", "id": "", "name": "BusinessAnalyticAgent", "aliases": [], "prompts": ["Default"], "path": "/p/AgentFab/BusinessAnalyticAgent/agent.yaml"},
                {"type": "agent", "id": "", "name": "SelfReflection", "aliases": [], "prompts": ["Default"], "path": "/p/AgentFab/SelfReflection/agent.yaml"},
                {"type": "agent", "id": "", "name": "DiscoveryAgent", "aliases": [], "prompts": ["Default"], "path": "/p/AgentFab/DiscoveryAgent/agent.yaml"},
                {"type": "agent", "id": "", "name": "StratoFormater", "aliases": [], "prompts": ["Default"], "path": "/p/AgentFab/StratoFormater/agent.yaml"},
            ]}
        ],
        raising=True,
    )

    tree = mod.list(project="AgentFab")
    assert len(tree) == 1
    agents = {a["name"] for a in (tree[0].get("agents") or [])}
    expected = {"BusinessAnalyticAgent", "SelfReflection", "DiscoveryAgent", "StratoFormater"}
    # At least 4 required agents present
    assert expected.issubset(agents)
