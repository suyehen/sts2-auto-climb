import test from 'node:test';
import assert from 'node:assert/strict';
import {eventForecastFacts,eventBranchFacts} from '../public/event-forecast.js';
test('event forecast separates immediate and future utility and preserves unknown',()=>{
 const c={immediate_utility:2,future_utility:5,expected_hp_change:-3,known:false};const before=JSON.stringify(c);
 assert.ok(eventForecastFacts(c).some(v=>v.includes('后续选择收益：5.00')));
 assert.ok(eventForecastFacts(c).some(v=>v.includes('尚不能确定')));assert.equal(JSON.stringify(c),before);
});
test('branch viewer keeps probabilities and unknown results without an invented score',()=>{
 const facts=eventBranchFacts({branches:[{probability:.25,immediate:{hp_after:20,max_hp_after:80,gold_after:10}},{probability:.75,reason:'规则未知'}]});
 assert.ok(facts[0].includes('25.00%'));assert.ok(facts[1].includes('75.00%')&&facts[1].includes('规则未知'));
});
