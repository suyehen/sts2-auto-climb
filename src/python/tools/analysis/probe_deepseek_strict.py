"""One synthetic DeepSeek decision request; never connects to the game or dispatches actions."""
import asyncio
import json
import time

from autoslay.agent.config import Store, atomic
from autoslay.agent.contracts import DecisionPlan
from autoslay.agent.providers import APIProvider, ProviderError
from autoslay.paths import ROOT


async def probe():
    store=Store()
    current=store.profiles['deepseek']
    profile=current.model_copy(update=dict(output_mode='deepseek_strict',max_tokens=4096,timeout_seconds=60))
    # Validate endpoint even though model_copy itself bypasses validation.
    profile=type(current).model_validate(profile.model_dump())
    packet=dict(state_token='synthetic-transport-probe',visible_state=dict(state=dict(
        phase='reward',legal_actions=[dict(action_id='skip_reward',type='skip_reward',
            allows_no_target=True,allowed_target_ids=[])])),
        instruction='Synthetic transport test. Choose skip_reward with no target. No game action will be executed.')
    result=dict(model=profile.model,occurred_at=time.time(),game_actions_executed=0)
    provider=APIProvider(profile,store.key(current))
    try:
        raw,usage=await provider.decide(packet)
        plan=DecisionPlan.model_validate_json(raw)
        valid=(plan.state_token==packet['state_token'] and len(plan.steps)==1
               and plan.steps[0].action_id=='skip_reward' and plan.steps[0].target_id is None)
        result.update(status='passed' if valid else 'invalid_decision',
            strict_supported=not provider.strict_fallback,usage=usage)
    except ProviderError as exc:
        result.update(status='failed',code=str(exc))
    except ValueError:
        result.update(status='failed',code='MODEL_INVALID_DECISION')
    atomic(ROOT/'.runtime/logs/agent/deepseek-strict-probe.json',result)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    asyncio.run(probe())
