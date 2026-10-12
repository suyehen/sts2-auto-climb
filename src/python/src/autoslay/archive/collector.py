"""Non-executing adapter with durable spool on archival failure."""
import logging
import sqlite3
from autoslay.archive.repository import for_legacy

def store(payload,directory=None):
    repo=for_legacy(directory)
    try:return repo.store(payload)
    except (OSError,ValueError,sqlite3.Error):
        logging.exception('Archive write failed; retaining pending evidence')
        repo.pending_store(payload)
        return dict(recorded=False,pending=True)
