"""Integration tests for MCP initialization in real execution flow."""
import pytest
from call.app import call as call_module
from call.lib.api import RunnableConfig


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset MCP state before and after each test."""
    call_module._reset_mcp_state()
    yield
    call_module._reset_mcp_state()


@pytest.mark.anyio
async def test_build_and_run_agent_initializes_mcp_first(monkeypatch, tmp_path):
    """Verify that build_and_run_agent calls MCP initialization before any other operations."""
    
    # Track call order
    call_order = []
    
    # Mock MCP initialization
    original_prepare = call_module._prepare_mcp_servers
    async def tracked_prepare(astack=None):
        call_order.append("mcp_prepare")
        # Return empty servers to avoid needing real MCP setup
        return [], None
    monkeypatch.setattr(call_module, "_prepare_mcp_servers", tracked_prepare)
    
    # Mock git pull
    original_git_pull = call_module._git_pull_prompt_repo
    async def tracked_git_pull():
        call_order.append("git_pull")
    monkeypatch.setattr(call_module, "_git_pull_prompt_repo", tracked_git_pull)
    
    # Mock build_tools_for_cfg
    original_build_tools = call_module.build_tools_for_cfg
    async def tracked_build_tools(cfg):
        call_order.append("build_tools")
        return []
    monkeypatch.setattr(call_module, "build_tools_for_cfg", tracked_build_tools)
    
    # Create a minimal config
    cfg = RunnableConfig(
        id="test-prompt",
        type="prompt",
        project="test",
        agent=None,
        prompt="test-prompt",
        path="test/test.md",
        url="http://test",
        goal="test",
        instructions="test instructions",
        model="gpt-4",
        attributes={}
    )
    
    # Mock remaining dependencies to avoid full execution
    monkeypatch.setattr(call_module, "process_user_input", lambda x: type('obj', (), {
        'sanitized': x,
        'normalized': {'input': x},
        'embedded': x
    })())
    monkeypatch.setattr(call_module, "_init_bot_safe", lambda **kw: None)
    monkeypatch.setattr(call_module, "_send_welcome_banner", lambda **kw: None)
    monkeypatch.setattr(call_module, "_create_session_if_any", lambda *a: None)
    
    # Mock Runner.run to prevent actual agent execution
    class MockResult:
        final_output = "test output"
    
    async def mock_run(*args, **kwargs):
        call_order.append("runner_run")
        return MockResult()
    
    monkeypatch.setattr(call_module.Runner, "run", mock_run)
    
    # Execute build_and_run_agent
    try:
        async with call_module.build_and_run_agent(cfg, "test input"):
            pass
    except Exception as e:
        # Capture any errors but still check call order
        print(f"Error during execution: {e}")
    
    # Verify MCP was called FIRST, before git pull or tool building
    assert len(call_order) >= 1, f"No calls recorded. Call order: {call_order}"
    assert call_order[0] == "mcp_prepare", f"MCP should be first, but got: {call_order}"
    
    # Verify order: mcp_prepare -> git_pull -> build_tools
    if len(call_order) >= 3:
        assert call_order.index("mcp_prepare") < call_order.index("git_pull"), \
            f"MCP should come before git pull. Order: {call_order}"
        assert call_order.index("git_pull") < call_order.index("build_tools"), \
            f"Git pull should come before build_tools. Order: {call_order}"


@pytest.mark.anyio
async def test_mcp_init_failure_prevents_execution(monkeypatch, tmp_path):
    """Verify that MCP initialization failure prevents agent execution."""
    
    call_order = []
    
    # Mock MCP initialization to fail
    async def failing_prepare(astack=None):
        call_order.append("mcp_prepare_failed")
        raise call_module.MCPInitializationError("Test MCP failure")
    monkeypatch.setattr(call_module, "_prepare_mcp_servers", failing_prepare)
    
    # Mock git pull - this should NOT be called if MCP fails
    async def tracked_git_pull():
        call_order.append("git_pull")
    monkeypatch.setattr(call_module, "_git_pull_prompt_repo", tracked_git_pull)
    
    cfg = RunnableConfig(
        id="test",
        type="prompt",
        project="test",
        agent=None,
        prompt="test",
        path="test.md",
        url="http://test",
        goal="test",
        instructions="test",
        model="gpt-4",
        attributes={}
    )
    
    # Verify that MCPInitializationError is raised
    with pytest.raises(call_module.MCPInitializationError, match="Test MCP failure"):
        async with call_module.build_and_run_agent(cfg, "test"):
            pass
    
    # Verify git_pull was never called
    assert call_order == ["mcp_prepare_failed"], \
        f"Only MCP prepare should have been called, got: {call_order}"
    assert "git_pull" not in call_order, "Git pull should not be called after MCP failure"


def test_cli_does_not_preinit_mcp():
    """Verify CLI does NOT pre-initialize MCP servers (to avoid stream closure across event loops)."""
    import inspect
    from call.cli import main as cli_main
    
    # Get the source code of main()
    source = inspect.getsource(cli_main.main)
    
    # Verify CLI does NOT call preinitialize_mcp_servers_sync
    assert "preinitialize_mcp_servers_sync" not in source, \
        "CLI main() should NOT pre-initialize MCP servers - this causes stream closure " \
        "when the event loop exits. MCP init should happen lazily in build_and_run_agent."
