# Package marker for call.app
import asyncio

def main() -> None:
    """Synchronous CLI entrypoint: runs the async call.main() in a new event loop.
    Lazy-import the submodule to avoid runpy double-import warnings.
    """
    from . import call as _call
    asyncio.run(_call.main())

async def main_async() -> None:
    """Asynchronous entrypoint: awaitable version of main for embedding.
    Lazy-import the submodule to avoid premature import during package init.
    """
    from . import call as _call
    await _call.main()

# Optionally expose other important items at package level
__all__ = ['main', 'main_async']