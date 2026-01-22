import asyncio


def test_init_openai_client_exists_and_noop():
    # Lazy import to avoid side effects at module import time
    from call.app import call as app_call

    assert hasattr(app_call, "init_openai_client")
    res = asyncio.run(app_call.init_openai_client())
    assert res is None
