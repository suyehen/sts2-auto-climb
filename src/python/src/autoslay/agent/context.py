"""Bounded visible state and relevant local references, never the full encyclopedia."""
import json
from copy import deepcopy
from autoslay.paths import ROOT
from autoslay.decision.deterministic import source
from autoslay.telemetry.decisions import public_snapshot, packet as report_packet


def build(snapshot, report, recent=(), max_bytes=180_000):
    state = deepcopy(public_snapshot(snapshot))
    ids = set()
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get('entity_id'), str): ids.add(value['entity_id'])
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(state)
    # Narrative is useful for the encyclopedia; decisions use options and hover tips.
    event=(state.get('state') or {}).get('event_screen')
    if event:event.pop('description',None)
    from autoslay.knowledge.behavior import relevant
    behavior=relevant(snapshot)
    visit(behavior)
    path = ROOT / 'data/knowledge/catalog/encyclopedia.json'
    catalog = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    entries = [e for e in catalog.get('entries', []) if e['entity_id'] in ids]
    from autoslay.knowledge.behavior import relevant
    result = dict(state_token=source(snapshot)['state_sha256'], source=source(snapshot),
                  visible_state=state, calculator=report_packet(report) if report else {},
                  recent_results=list(recent)[-4:], reference_entries=entries,
                  reference_version=catalog.get('game_version'),
                  reference_version_matches=catalog.get('game_version') == snapshot.get('game_version'),
                  behavior_references=behavior,
                  mechanism_audit=(report or {}).get('mechanism_audit'),
                  limitations=['Reference coverage does not imply simulation support.'])
    size = lambda: len(json.dumps(result, ensure_ascii=False).encode())
    if size() > max_bytes:
        result['behavior_references']['entries']=[]
        result['behavior_references']['power_references']={}
        result['limitations'].append('Behavior references omitted to fit context budget.')
    if size() > max_bytes:
        result['reference_entries'] = []
        result['limitations'].append('Reference entries omitted to fit context budget.')
    if size() > max_bytes:
        result['calculator'] = {}
        result['limitations'].append('Calculator report omitted to fit context budget.')
    # Never cut off the legal actions or visible game state mid-JSON.
    if size() > max_bytes: raise ValueError('VISIBLE_STATE_EXCEEDS_CONTEXT_BUDGET')
    return result
