import asyncio
import fcntl
import os

from .config import HOME

HOME.mkdir(exist_ok=True)
lock = open(HOME / "engine.lock", "w")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    raise SystemExit("cosmo is already running")
(HOME / "cosmo.pid").write_text(str(os.getpid()))

from .loop import run

try:
    asyncio.run(run())
except KeyboardInterrupt:
    pass
