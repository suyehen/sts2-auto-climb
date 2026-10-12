"""One real inference on a synthetic state. Never imports or invokes game dispatch."""
import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from uuid import uuid4
from autoslay.paths import ROOT
from autoslay.agent.config import Store, atomic
from autoslay.agent.providers import create, ProviderError
from autoslay.agent.contracts import validate
from autoslay.decision.deterministic import source


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect-provider', required=True)
    args = parser.parse_args()
    store = Store()
    if store.active != args.expect_provider:
        raise SystemExit('ACTIVE_PROVIDER_CHANGED: probe cancelled')
    profile = store.profiles[store.active]
    snapshot = dict(schema_version='1.0', connected=True, ready=True, busy=False,
        session_epoch='offline-probe', state_version=1, game_version='synthetic-test',
        state=dict(run_id='offline-probe', phase='combat', control_mode='auto',
            player=dict(hp=50, energy=1),
            enemies=[dict(instance_id='test-enemy', hp=2, block=0, incoming_damage=10)],
            hand=[dict(action_id='test-attack', description='Deal 6 damage. Costs 1 energy. No other effects.')],
            legal_actions=[dict(action_id='test-attack', type='play_card',
                                allows_no_target=False, allowed_target_ids=['test-enemy']),
                           dict(action_id='end_turn', type='end_turn',
                                allows_no_target=True, allowed_target_ids=[])]))
    packet = dict(state_token=source(snapshot)['state_sha256'], visible_state=snapshot,
        limitations=['Synthetic transport test, no real run. No relics, death triggers or permanent growth effects.'])
    result = dict(kind='offline_backend_probe', provider=profile.id, configured_model=profile.model,
        recorded_at=datetime.now(timezone.utc).isoformat(), game_action_dispatched=False,
        packet=packet, success=False)
    started = time.monotonic()
    try:
        raw, usage = await create(profile, store.key(profile)).decide(packet)
        decision = validate(raw, snapshot)
        result.update(success=True, decision=decision.model_dump(), usage=usage,
                      expected_attack_selected=decision.action_id == 'test-attack')
    except ProviderError as exc:
        result['error'] = str(exc)
    except ValueError:
        result['error'] = 'MODEL_OUTPUT_VALIDATION_FAILED'
    finally:
        result['elapsed_ms'] = round((time.monotonic()-started)*1000)
        path = ROOT/'.runtime/logs/agent-probes'/(uuid4().hex+'.json')
        atomic(path, result)
        print(json.dumps({k: v for k, v in result.items() if k != 'packet'}, ensure_ascii=False))
        print('record:', path)
    if not result['success']: raise SystemExit(1)


if __name__ == '__main__': asyncio.run(main())
