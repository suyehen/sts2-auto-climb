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


@scoped
def qualify(snapshot, bounded=False):
    proof=assess({},snapshot)
    if bounded and not proof['eligible'] and proof['mode']!='wait':
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
                        if receipt.get('status') not in {'completed','failed','rejected'}:
                            return dict(status='unresolved',reason='PREVIOUS_ACTION_UNRESOLVED',request=old['request'])
                        old.update(status=receipt['status'],receipt=receipt,recovered_by_status_query=True);save(old_path,old)
                before=await self.fetch();proof=await asyncio.to_thread(qualify,before,bounded)
                if not proof['eligible']: return dict(status=proof['mode'],autonomy=proof)
                # Re-evaluate, rather than accepting a proof/candidate supplied by callers.
                fresh=await self.fetch()
                if source(fresh)!=proof['source'] or not fresh.get('ready') or fresh.get('busy'):
                    return dict(status='rejected',reason='STATE_CHANGED')
                proof=await asyncio.to_thread(qualify,fresh,bounded)
                if not proof['eligible']: return dict(status=proof['mode'],autonomy=proof)
                step=proof['selected_steps'][0];identity=uuid4().hex
                report=dict(analysis_id=identity,source=proof['source'],phase=fresh['state']['phase'],
                    autonomy=proof,execution_authorized=False,ai_called=False,shortlist=[],
                    limitations=['bounded current-turn policy; not a global outcome proof' if proof.get('decision_basis')=='bounded_combat_strategy' else 'proof applies only to this fresh state'])
                if self.record:
                    await asyncio.to_thread(decisions.archive,report,fresh)
                    await asyncio.to_thread(runs.stage,'local_choice',fresh,dict(analysis_id=identity,reason=proof['reason'],
                        rule_id=proof['rule_id'],evidence=proof['evidence'],steps=[step],ai_called=False))
                request=ActionRequest(request_id='det-'+request_id,run_id=proof['source']['run_id'],
                    session_epoch=proof['source']['session_epoch'],expected_state_version=proof['source']['state_version'],**step)
                result=dict(status='dispatch_pending',request=request.model_dump(),autonomy=proof,analysis_id=identity)
                save(path,result) # Persist BEFORE dispatch; timeout/restart never resubmits.
                receipt=await self.submit(request)
                result.update(status=receipt.get('status','unresolved'),receipt=receipt)
                save(path,result)
                after=await self.fetch();result['snapshot']=after;save(path,result)
                if self.record: await asyncio.to_thread(runs.stage,'local_outcome',after,
                    dict(analysis_id=identity,request_id=request.request_id,status=result['status'],rule_id=proof['rule_id'],
                        predicted=proof['evidence'],actual=dict(phase=(after.get('state') or {}).get('phase'),
                        hp=(((after.get('state') or {}).get('player') or {}).get('creature') or {}).get('hp'))))
                return result
