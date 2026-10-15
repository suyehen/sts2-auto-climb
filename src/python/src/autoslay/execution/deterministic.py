"""One fresh local decision, one durable action. No action retries or control changes."""
import asyncio
import json
import re
from autoslay.paths import ROOT
from autoslay.bridge.client import fetch_state,submit_action,get_action_status,ActionRequest
from autoslay.decision.deterministic import assess,source
from autoslay.execution.lock import dispatch_lock
from autoslay.execution.sequence import save
from autoslay.telemetry import runs,decisions
from uuid import uuid4
from autoslay.knowledge_base.repository import scoped
from time import perf_counter


@scoped
def qualify(snapshot, bounded=False):
    proof=assess({},snapshot)
    from autoslay.decision.deterministic import load_policy
    batch=snapshot.get('state',{}).get('phase')=='combat' and load_policy().get('combat_batches_enabled',False)
    if (bounded or batch) and not proof['eligible'] and proof['mode']!='wait':
        from autoslay.decision.unified import analyze_decision
        proof=assess(analyze_decision(snapshot),snapshot)
    return proof

class DeterministicExecutor:
    def __init__(self,directory=None,fetch=fetch_state,submit=submit_action,record=True,status=get_action_status):
        self.directory=directory or ROOT/'.runtime/logs/deterministic'
        self.fetch,self.submit,self.record=fetch,submit,record
        self.status=status
        self.lock=asyncio.Lock()

    async def execute(self,request_id,*,bounded=False):
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',request_id):
            raise ValueError('Invalid deterministic request ID')
        async with self.lock:
            # Same global dispatch lock as plans; concurrent clients cannot prove against stale state.
            with dispatch_lock(ROOT/'.runtime/logs/plans') as acquired:
                if not acquired: return dict(status='rejected',reason='EXECUTOR_BUSY')
                path=self.directory/(request_id+'.json')
                if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
                # An interrupted dispatch blocks later IDs until the bridge confirms settlement.
                for old_path in self.directory.glob('*.json'):
                    old=json.loads(old_path.read_text(encoding='utf-8'))
                    if old.get('status') not in {'completed','failed','rejected'}:
                        receipt=await self.status(old['request']['request_id'])
                        if receipt.get('status')=='awaiting_choice':
                            choice_snapshot=await self.fetch();choice_state=choice_snapshot.get('state') or {}
                            if choice_state.get('phase')=='card_selection' and (choice_state.get('pending_combat_action') or {}).get('request_id')==old['request']['request_id']:
                                continue  # A bound child choice does not resubmit the parent.
                        if receipt.get('status') not in {'completed','failed','rejected'}:
                            return dict(status='unresolved',reason='PREVIOUS_ACTION_UNRESOLVED',request=old['request'])
                        old.update(status=receipt['status'],receipt=receipt,recovered_by_status_query=True);save(old_path,old)
                before=await self.fetch();started=perf_counter();proof=await asyncio.to_thread(qualify,before,bounded)
                analysis_ms=(perf_counter()-started)*1000
                if not proof['eligible']: return dict(status=proof['mode'],autonomy=proof)
                # Re-evaluate, rather than accepting a proof/candidate supplied by callers.
                fresh=await self.fetch()
                if source(fresh)!=proof['source'] or not fresh.get('ready') or fresh.get('busy'):
                    return dict(status='rejected',reason='STATE_CHANGED')
                started=perf_counter();proof=await asyncio.to_thread(qualify,fresh,bounded)
                analysis_ms+=(perf_counter()-started)*1000
                if not proof['eligible']: return dict(status=proof['mode'],autonomy=proof)
                if proof.get('decision_basis')=='local_combat_batch':
                    return await self.execute_batch(path,request_id,proof,fresh,analysis_ms)
                step=proof['selected_steps'][0];identity=uuid4().hex
                report=dict(analysis_id=identity,source=proof['source'],phase=fresh['state']['phase'],
                    request_elapsed_ms=analysis_ms,
                    autonomy=proof,execution_authorized=False,ai_called=False,shortlist=[],
                    limitations=['bounded strategy; not a global outcome proof' if proof.get('decision_basis') in {'bounded_combat_strategy','calibrated_combat_strategy'} else 'proof applies only to this fresh state'])
                if self.record:
                    await asyncio.to_thread(decisions.archive,report,fresh)
                    await asyncio.to_thread(runs.stage,'local_choice',fresh,dict(analysis_id=identity,reason=proof['reason'],
                        rule_id=proof['rule_id'],decision_basis=proof.get('decision_basis'),evidence=proof['evidence'],steps=[step],ai_called=False))
                request=ActionRequest(request_id='det-'+request_id,run_id=proof['source']['run_id'],
                    session_epoch=proof['source']['session_epoch'],expected_state_version=proof['source']['state_version'],**step)
                result=dict(status='dispatch_pending',request=request.model_dump(),autonomy=proof,analysis_id=identity)
                save(path,result) # Persist BEFORE dispatch; timeout/restart never resubmits.
                receipt=await self.submit(request)
                result.update(status=receipt.get('status','unresolved'),receipt=receipt)
                save(path,result)
                after=await self.fetch();result['snapshot']=after;save(path,result)
                if proof.get('decision_basis')=='calibrated_combat_strategy' and result['status']=='completed':
                    from autoslay.decision.normalize import normalize
                    from autoslay.simulation.engine import CombatSimulator
                    from autoslay.decision.contracts import Operation
                    from autoslay.telemetry.verification import compare_transition
                    try:
                        initial=normalize(fresh);transition=CombatSimulator(initial).apply(initial,Operation(**step))
                        if not transition.boundary and not transition.state.combat_won and after.get('state',{}).get('phase')=='combat':
                            comparison=compare_transition(transition,after)
                            result['prediction_comparison']=comparison
                            if not comparison['numeric_fields_match']:
                                from autoslay.decision.combat_circuit import disable
                                disable(proof['evidence']['policy_identity']['policy_fingerprint'],comparison,ROOT)
                                result['new_strategy_disabled']=True
                                save(path,result)
                    except (ValueError,KeyError,TypeError) as exc:
                        result['prediction_comparison_unavailable']=type(exc).__name__
                from autoslay.telemetry.effects import observe
                if self.record: await asyncio.to_thread(runs.stage,'local_outcome',after,
                    dict(analysis_id=identity,request_id=request.request_id,status=result['status'],rule_id=proof['rule_id'],
                        observed_effects=observe(fresh,receipt.get('snapshot') or after,result['status'],request.request_id),
                        predicted=proof['evidence'],actual=dict(phase=(after.get('state') or {}).get('phase'),
                        hp=(((after.get('state') or {}).get('player') or {}).get('creature') or {}).get('hp'))))
                return result

    async def execute_batch(self,path,request_id,proof,before,analysis_ms):
        """Called under the existing dispatch lock. Persist every action before submission."""
        from autoslay.planning.combat_batches import forecast_state
        from autoslay.decision.normalize import normalize
        from autoslay.decision.contracts import Operation
        from autoslay.simulation.engine import CombatSimulator
        from autoslay.execution.sequence import projection
        from autoslay.telemetry.effects import observe
        identity=uuid4().hex;origin=source(before);initial=before
        report=dict(analysis_id=identity,source=origin,phase='combat',request_elapsed_ms=analysis_ms,
            autonomy=proof,combat=dict(batches=dict(selected_batch=proof['evidence']['selected_batch'],
                top_batches=proof['evidence'].get('top_batches'),deck_capabilities=proof['evidence'].get('deck_capabilities'),
                run_resources=proof['evidence'].get('run_resources'))),execution_authorized=False,ai_called=False)
        if self.record:
            await asyncio.to_thread(decisions.archive,report,before)
            await asyncio.to_thread(runs.stage,'local_choice',before,dict(analysis_id=identity,reason=proof['reason'],
                rule_id=proof['rule_id'],decision_basis='local_combat_batch',evidence=proof['evidence'],
                steps=proof['selected_steps'],ai_called=False))
        result=dict(status='running',autonomy=proof,analysis_id=identity,steps=[],decision_unit='operation_batch')
        def finish(reason,status='completed'):
            result.update(status=status,stop_reason=reason,snapshot=before,redecision_required=True)
            if status=='rejected':result['reason']='STATE_CHANGED'
            save(path,result);return result
        for index,step in enumerate(proof['selected_steps']):
            state=before.get('state') or {}
            if not before.get('connected') or not before.get('ready') or before.get('busy') or before.get('error') or state.get('control_mode')=='paused':
                return finish('state_not_ready','completed' if result['steps'] else 'rejected')
            src=source(before)
            if src['session_epoch']!=origin['session_epoch'] or src['run_id']!=origin['run_id'] or state.get('phase')!='combat' or state.get('combat_instance_id')!=initial['state'].get('combat_instance_id') or any(before.get(k)!=initial.get(k) for k in ('game_version','game_assembly_sha256')):
                return finish('context_changed')
            legal=next((a for a in state.get('legal_actions') or [] if a['action_id']==step['action_id']),None)
            if not legal or (step.get('target_id') not in legal.get('allowed_target_ids',[]) if step.get('target_id') else not legal.get('allows_no_target')):
                return finish('legal_actions_changed')
            transition=None;warnings=()
            previous_relics=state.get('player',{}).get('relics')
            try:
                current=normalize(before);warnings=current.warnings;model=forecast_state(current)
                sim=CombatSimulator(model);action=Operation(**step)
                card=next((c for c in model.hand if action.action_id=='play_'+c.instance_id),None)
                if card and card.target_type in {'Self','None','AllEnemies'}:action=Operation(action.action_id)
                transition=sim.apply(model,action)
            except (ValueError,KeyError,TypeError):pass
            request=ActionRequest(request_id=f'det-{request_id}-{index}',run_id=origin['run_id'],
                session_epoch=origin['session_epoch'],expected_state_version=before['state_version'],**step)
            entry=dict(request=request.model_dump(),status='dispatch_pending');result['steps'].append(entry)
            result.update(status='dispatch_pending',request=request.model_dump());save(path,result)
            receipt=await self.submit(request);entry.update(status=receipt.get('status','unresolved'),receipt=receipt)
            result['status']=entry['status'];save(path,result)
            after=await self.fetch();effects=observe(before,receipt.get('snapshot') or after,entry['status'],request.request_id)
            entry['observed_effects']=effects
            if self.record:await asyncio.to_thread(runs.stage,'local_outcome',after,dict(analysis_id=identity,
                request_id=request.request_id,status=entry['status'],rule_id=proof['rule_id'],batch_step=index+1,
                observed_effects=effects,predicted=proof['evidence'],actual=dict(phase=after.get('state',{}).get('phase'))))
            before=after
            if entry['status'] not in {'completed','awaiting_choice'}:return finish('action_'+entry['status'],entry['status'])
            if entry['status']=='awaiting_choice':return finish('card_selection','awaiting_choice')
            if before.get('state',{}).get('phase')!='combat':return finish('combat_or_phase_finished')
            if step['action_id']=='end_turn':return finish('next_player_turn')
            if transition is None:return finish('unmodeled_effect_observation')
            if transition.boundary:return finish(transition.boundary)
            try:
                actual=normalize(before)
                from autoslay.telemetry.verification import compare_transition
                comparison=compare_transition(transition,before)
                matched=(projection(forecast_state(actual))==projection(transition.state) and actual.warnings==warnings
                    and comparison['numeric_fields_match'] and actual.periodic_relics==transition.state.periodic_relics
                    and before['state']['player'].get('relics')==previous_relics
                    and tuple(e.runtime_fields for e in actual.enemies)==tuple(e.runtime_fields for e in transition.state.enemies))
                entry['prediction_comparison']=comparison
            except (ValueError,KeyError,TypeError):matched=False
            entry['prediction_matched']=matched;save(path,result)
            if not matched:
                from autoslay.execution.batch_models import downgrade
                entity=card.entity_id if card else next((p.entity_id for p in current.potions if p.action_id==step['action_id']),'UNKNOWN')
                downgrade(entity,dict(reason='prediction_mismatch',analysis_id=identity,request_id=request.request_id),ROOT)
                return finish('prediction_mismatch')
            def hand_observation(hand):
                return {c.instance_id:(c.description,c.enchantment_id,c.enchantment_amount,c.costs_x,c.costs_star_x,c.variant_key) for c in hand}
            if actual.max_hp!=transition.state.max_hp or hand_observation(actual.hand)!=hand_observation(transition.state.hand):
                return finish('hand_or_persistent_resources_changed')
        return finish(proof['evidence']['stop_reason'])
