"""Action-model downgrades; local selection remains available after a mismatch."""
import json
from autoslay.paths import ROOT
from autoslay.archive.repository import atomic


def path(root=None):return (root or ROOT)/'.runtime/local/agent/combat-batch-models.json'


def load(root=None):
    try:return json.loads(path(root).read_text(encoding='utf-8'))
    except FileNotFoundError:return dict(entities={})
    except (OSError,ValueError):return dict(entities={'*':{'reason':'model downgrade record unreadable'}})


def downgrade(entity,evidence,root=None):
    data=load(root);data['entities'][entity]=evidence;atomic(path(root),data)
