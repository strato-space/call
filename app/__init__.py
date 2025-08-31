# Package marker for call.app
from . import call as _call
import asyncio

def main() -> None:
    """Synchronous CLI entrypoint: runs the async call.main() in a new event loop."""
    asyncio.run(_call.main())

async def main_async() -> None:
    """Asynchronous entrypoint: awaitable version of main for embedding."""
    await _call.main()

# Optionally expose other important items at package level
__all__ = ['main', 'main_async']