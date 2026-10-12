"""Start the loopback execution service; no gameplay starts on launch."""
import asyncio
from autoslay.execution.resident import serve

if __name__ == "__main__":
    asyncio.run(serve())
