import asyncio

from .loop import run

try:
    asyncio.run(run())
except KeyboardInterrupt:
    pass
