import types

import pytest

from call.lib import api as call_api


@pytest.mark.asyncio
async def test_reload_clears_agent_cache_on_success(monkeypatch):
    """call.lib.api.reload() should clear AGENT_CACHE after a successful rescan.

    This ensures that cached agents and sub-agents pick up updated instructions
    from the refreshed repo index on the next run.
    """

    # Arrange: fake repo_fs.reload to always succeed
    def fake_reload(repos=None, full_form=True):  # pragma: no cover - simple stub
        return {"ok": True, "scanned": 1, "repos": []}

    monkeypatch.setattr(call_api.repo_fs, "reload", fake_reload, raising=True)

    # Ensure AGENT_CACHE contains at least one entry before reload()
    from call.app import call as app_call

    app_call.AGENT_CACHE.clear()
    app_call.AGENT_CACHE["TestAgent"] = object()
    assert app_call.AGENT_CACHE  # precondition: cache is not empty

    # Act: call the library-level reload helper
    res = call_api.reload()

    # Assert: reload succeeded and AGENT_CACHE was cleared
    assert isinstance(res, dict)
    assert res.get("ok") is True
    assert app_call.AGENT_CACHE == {}


def test_reload_does_not_clear_agent_cache_on_failure(monkeypatch):
    """AGENT_CACHE should not be cleared when reload() reports failure.

    This avoids dropping cached agents when the filesystem scan or DB update
    fails, leaving callers free to handle the error explicitly.
    """

    from call.app import call as app_call

    app_call.AGENT_CACHE.clear()
    app_call.AGENT_CACHE["TestAgent"] = object()
    assert app_call.AGENT_CACHE  # precondition

    def fake_reload_fail(repos=None, full_form=True):  # pragma: no cover - stub
        return {"ok": False, "error_code": 500, "description": "fail"}

    monkeypatch.setattr(call_api.repo_fs, "reload", fake_reload_fail, raising=True)

    res = call_api.reload()

    assert isinstance(res, dict)
    assert not res.get("ok")
    # Cache should remain untouched on failure
    assert app_call.AGENT_CACHE
