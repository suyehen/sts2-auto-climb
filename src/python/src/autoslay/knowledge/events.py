"""Versioned event observations. Observing a page never invents a transition rule."""
import json
from datetime import datetime,timezone
from pathlib import Path
from threading import RLock
from autoslay.paths import ROOT
from autoslay.decision.cache import digest

DIRECTORY=ROOT/'data/knowledge/events/observations'
LOCK=RLock()

def observe(snapshot,directory=None):
    state=snapshot.get('state') or {}; event=state.get('event_screen') or {}
    if state.get('phase')!='event' or not event.get('entity_id'): return dict(recorded=False,reason='not_event')
    if not snapshot.get('connected',False) or snapshot.get('error'): return dict(recorded=False,reason='not_live')
    player=state.get('player') or {}
    public=dict(event=event,player={k:player.get(k) for k in ('creature','gold','relics','deck','potions')},
                source={k:snapshot.get(k) for k in ('game_version','session_epoch','state_version')},
                run_id=state.get('run_id'),floor=state.get('floor'))
    identity=digest(public)
    path=Path(directory or DIRECTORY)/(identity+'.json')
    with LOCK:
        if not path.exists():
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix('.tmp')
            temp.write_text(json.dumps(dict(schema_version='event-observation/1.0',observed_at=datetime.now(timezone.utc).isoformat(),
                evidence='public_state_observation; not proof of selected action or universal transition',**public),ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(path)
    return dict(recorded=True,observation_id=identity)

def rules(path=None):
    path=Path(path or ROOT/'data/knowledge/events/rules.json')
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if data.get('schema_version')!='events/1.0': raise ValueError('Invalid event rules')
    return data

def resource_hook_warnings(player,inventory_path=None):
    """Source-backed event gate. No assumptions that every relic affects event HP."""
    inventory=json.loads(Path(inventory_path or ROOT/'data/knowledge/coverage/source-inventory.json').read_text(encoding='utf-8-sig'))
    by_id={e['entity_id']:e for e in inventory['entries']}
    sensitive={'AfterCurrentHpChanged','AfterDamageReceived','AfterDeath','AfterDiedToDoom',
               'AfterModifyingHpLostAfterOsty','AfterPreventingDeath','ModifyHpLostAfterOsty',
               'ModifyHpLostAfterOstyLate','ShouldDieLate','ModifyDamageAdditive','ModifyDamageMultiplicative'}
    sensitive |= {'AfterGoldGained','AfterModifyingGoldGained','ModifyGoldGained',
                  'OnEventFinished','AfterEventStarted','BeforeEventStarted',
                  'OnUpgrade','BeforeCardRemoved','AfterPotionProcured','ShouldProcurePotion',
                  'ModifyRestSiteHealAmount','TryModifyRestSiteHealRewards'}
    warnings=[]
    for relic in player.get('relics') or []:
        row=by_id.get(relic['entity_id'])
        if not row: warnings.append('UNKNOWN_RELIC_SOURCE:'+relic['entity_id'])
        elif sensitive.intersection(row['hooks']): warnings.append('EVENT_RESOURCE_HOOK:'+relic['entity_id'])
    if (player.get('creature') or {}).get('powers'): warnings.append('EVENT_PLAYER_POWER_HOOKS_NOT_VERIFIED')
    return warnings
