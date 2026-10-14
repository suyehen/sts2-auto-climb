"""Durable, policy-specific rollback after an observed deterministic mismatch."""
from autoslay.paths import ROOT
from autoslay.archive.repository import atomic


def path(root=None):return (root or ROOT)/'.runtime/local/agent/combat-strategy-disabled.json'


def blocked(fingerprint,root=None):
    import json
    try:state=json.loads(path(root).read_text(encoding='utf-8'))
    except FileNotFoundError:return False
    except (OSError,ValueError):return True
    return fingerprint in state.get('policies',{})


def disable(fingerprint,evidence,root=None):
    import json
    target=path(root)
    try:state=json.loads(target.read_text(encoding='utf-8'))
    except (OSError,ValueError):state=dict(schema_version='combat-circuit/1.0',policies={})
    state['policies'][fingerprint]=evidence
    atomic(target,state)
