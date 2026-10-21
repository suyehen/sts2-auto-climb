"""Conservative mechanism coverage and instance-scoped observations, never rule execution."""
from collections import Counter, OrderedDict
from threading import RLock
from autoslay.knowledge_base.behavior import load
from autoslay.rules.registry import mechanism_status
from autoslay.knowledge_base.templates import index
from autoslay.knowledge_base.events import rules as event_rules

VERSION = '2.0.0'


class StateTracker:
    """No inference from initial defaults or announced moves. Reconnect starts a new scope."""
    def __init__(self):
        self.lock = RLock()
        self.scopes = OrderedDict()

    def observe(self, snapshot, enemies):
        state = snapshot.get('state') or {}
        scope = (snapshot.get('session_epoch'), state.get('run_id') or state.get('command_context_id'), state.get('combat_instance_id'))
        version = snapshot.get('state_version')
        if not all(scope) or type(version) is not int or not snapshot.get('connected'):
            return dict(status='unscoped', changes=[], reason='缺少战斗实例标识，未跨快照推断状态')
        values = {e['instance_id']: {f['name']: f['value'] for f in e['fields'] if f['status']=='observed'}
                  for e in enemies if e.get('instance_id')}
        with self.lock:
            previous = self.scopes.get(scope)
            if previous and version <= previous['version']:
                return dict(status='duplicate' if version == previous['version'] else 'stale', changes=[])
            changes = []
            if previous:
                for identity, fields in values.items():
                    old = previous['values'].get(identity, {})
                    for name, value in fields.items():
                        if name in old and old[name] != value:
                            changes.append(dict(instance_id=identity, name=name, before=old[name], after=value,
                                                from_state_version=previous['version'], to_state_version=version))
            self.scopes[scope] = dict(version=version, values=values)
            self.scopes.move_to_end(scope)
            while len(self.scopes)>128: self.scopes.popitem(last=False)
        return dict(status='observed', changes=changes, scope=list(scope))


TRACKER = StateTracker()


def inventory():
    """All entities audited against native definitions; boundaries remain explicit."""
    references = index()[0]
    behavior = load()
    rows = [dict(entity_id=i, category=e['category'], name=e.get('name'),
                 tier='defined' if mechanism_status(i)=='defined' else 'ai_required',
                 reason=mechanism_status(i)) for i,e in references.items()]
    for e in behavior['entries']:
        rows.append(dict(entity_id=e['entity_id'], category=e['category'], name=e['name'],
                         tier='reference_only', reason='行为资料尚未编译为通用执行规则'))
    return dict(module_version=VERSION, entries=rows,
                counts=dict(Counter(r['tier'] for r in rows)),
                exactness='defined means a native mechanic definition exists; execution boundaries remain explicit',
                game_version=behavior.get('game_version'), game_assembly_sha256=behavior.get('game_assembly_sha256'))


def inspect(snapshot, report=None, tracker=None):
    state = snapshot.get('state') or {}
    data = load()
    matching = bool(snapshot.get('game_assembly_sha256')) and data.get('game_assembly_sha256')==snapshot.get('game_assembly_sha256') and data.get('game_version')==snapshot.get('game_version')
    by_id = {e['entity_id']:e for e in data['entries']}
    enemies = []
    for raw in state.get('enemies') or []:
        entry = by_id.get(raw.get('entity_id'), {})
        graph = entry.get('state_graph') or {}
        runtime = raw.get('runtime_state') or {}
        observed = runtime.get('fields') or {}
        fields = []
        for field in graph.get('state_fields', []):
            name = field['name']
            value = observed.get(name)
            declared=field.get('type','').rstrip('?')
            valid=(type(value) is bool if declared=='bool' else type(value) in (int,float)) if value is not None else field.get('type','').endswith('?')
            available = matching and name in observed and valid
            fields.append(dict(name=name, value=value if available else None,
                               status='observed' if available else 'unknown',
                               source='game_instance_field' if available else 'not_observed',
                               initial_expression=field.get('initial_expression')))
        aliases = {name:expr for name,expr in graph.get('definitions', {}).items() if expr in {f['name'] for f in fields}}
        enemies.append(dict(entity_id=raw.get('entity_id'), instance_id=raw.get('instance_id'), name=raw.get('name',entry.get('name')),
                            fields=fields, field_aliases=aliases, reference_matches=matching,
                            branch_count=sum(len(n.get('branches',[])) for n in graph.get('nodes',[])),
                            branch_calculation='ai_required', reference_present=bool(entry)))
    references = index()[0]
    entities = {}
    from autoslay.rules.mechanism_scope import observed_pickup
    pickup = observed_pickup(snapshot) if state.get('phase') == 'combat' else {}
    def walk(value):
        if isinstance(value, dict):
            identity=value.get('entity_id')
            if isinstance(identity,str) and identity.startswith(('CARD.','POTION.','RELIC.','POWER.')):
                status=mechanism_status(identity,snapshot.get('game_version'))
                if identity in pickup: status='observed_in_snapshot'
                entities[identity]=dict(entity_id=identity,name=value.get('name') or (references.get(identity) or {}).get('name',identity),
                                       tier='observed_in_snapshot' if identity in pickup else 'defined' if status=='defined' else 'ai_required',reason=status)
            for child in value.values(): walk(child)
        elif isinstance(value,list):
            for child in value: walk(child)
    # The active hand, potions and owned relics/powers; deck-only cards do not block this turn.
    player=state.get('player') or {}
    walk({k:player.get(k) for k in ('hand','potions','relics','creature')})
    walk(state.get('enemies') or [])
    for creature in [player.get('creature') or {}, *(state.get('enemies') or [])]:
        for identity in creature.get('hidden_power_ids') or []:
            entities.setdefault(identity,dict(entity_id=identity,name=identity,tier='ai_required',reason='hidden_power_not_modeled'))
    unknown=sum(f['status']=='unknown' for e in enemies for f in e['fields'])
    blockers=[]
    if enemies and not matching: blockers.append('怪物资料与当前游戏构建未核对，禁止精确推演分支')
    if unknown: blockers.append(f'{unknown} 项怪物动态状态尚未观测')
    if any(e['tier']=='ai_required' for e in entities.values()): blockers.append('当前包含未建模的效果或交互')
    if enemies: blockers.append('怪物后续行为尚未编译为完整战斗推演规则')
    combat=(report or {}).get('combat') or {}
    blockers.extend(combat.get('limitations') or [])
    strategy=(report or {}).get('autonomy',{}).get('decision_basis')=='bounded_combat_strategy'
    local=bool((report or {}).get('autonomy',{}).get('eligible')) and not strategy
    estimated=bool(combat.get('candidates'))
    event=state.get('event_screen') or {}
    event_info=None
    if state.get('phase')=='event':
        rules=event_rules()
        event_info=dict(entity_id=event.get('entity_id'), tier='ai_required',
                        repeat_state='live_options_only', rule_build_matches=rules.get('game_assembly_sha256')==snapshot.get('game_assembly_sha256'))
        reference=by_id.get(event.get('entity_id'),{}).get('event_model') or {}
        event_info.update(source_model_present=bool(reference),source_model_matches=matching,
                          unresolved=reference.get('unresolved',[]),symbolic_rule_count=sum(v['status']!='fixed' for v in reference.get('variables',{}).values()))
        blockers.append('事件当前数值以游戏为准；后续源码流程供 AI 判断，未启用通用自动推演')
    result=dict(module_version=VERSION, classification='exact' if local else 'estimated' if estimated or strategy else 'ai_required',
                classification_scope='current local execution proof' if local else 'bounded current-turn local policy; not a global proof' if strategy else 'candidate scoring only',
                execution_authorized=False, ai_required=not local, entities=list(entities.values()), enemies=enemies, event=event_info,
                unknown_state_count=unknown, blockers=list(dict.fromkeys(blockers)),
                metric_scopes=dict(visible_hp='observed',visible_block='observed',visible_intents='observed',
                                   candidate_damage='bounded_model' if estimated else 'not_calculated',
                                   current_turn_hp_loss='estimated' if estimated else 'unknown',future_hp_loss='unknown',
                                   enemy_branches='ai_required',win_probability='not_calibrated'))
    result['tracking']=(tracker or TRACKER).observe(snapshot,enemies)
    return result


def attach(report, snapshot):
    report['mechanism_audit']=inspect(snapshot,report)
    return report
