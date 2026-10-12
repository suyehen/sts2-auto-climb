"""Nonblocking OS lock shared by resident and MCP processes; released on process exit."""
from contextlib import contextmanager
import os

@contextmanager
def dispatch_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "dispatch.lock").open("a+b") as handle:
        # Windows byte-range locks also forbid reading the locked byte from another handle.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        try:
            yield acquired
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
