"""Start the local Agent service in paused mode."""
import asyncio
from autoslay.agent.server import run
if __name__ == '__main__': asyncio.run(run())
