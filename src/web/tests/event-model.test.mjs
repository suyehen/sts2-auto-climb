import test from 'node:test';
import assert from 'node:assert/strict';
import {eventOptionFacts,eventVariableText,eventFormula} from '../public/event-model.js';
test('future costs expose every stage and do not flatten mutable damage to an initial number',()=>{
 const v={status:'dynamic',value:3,updates:[{operation:'+=',value:{expression:'1m'}}]};
 assert.match(eventVariableText(v),/初始 3/);assert.match(eventVariableText(v),/每次增加 1/);
 const cost={status:'dynamic',value:3,future_values:[{conditions:['DecipherCount == 1'],expression:'6'},{conditions:['DecipherCount == 4'],expression:'owner.Creature.MaxHp - 1'}]};
 assert.match(eventVariableText(cost),/解读次数 = 1 → 6/);assert.match(eventVariableText(cost),/当前生命上限 - 1/);
 assert.equal(eventFormula('_decipherCount == 4'),'已解读次数 = 4');
});
test('conditional effects, stage arrays and actual RNG probabilities survive human projection',()=>{
 const entry={event_model:{variables:{},methods:{choose:{effects:[{kind:'lose_hp',amount:{indexed_values:['5','6','7']},conditions:['NumberOfDigs < 2']}],returns:[],transitions:[],random:[{distribution:'uniform_integer',min_inclusive:0,max_exclusive:3,probability_each:1/3}]} }}};
 const before=JSON.stringify(entry),facts=eventOptionFacts(entry,{reachable_methods:['choose']});
 assert.ok(facts.some(f=>f.includes('深入次数 < 2')&&f.includes('5 / 6 / 7')));
 assert.ok(facts.some(f=>f.includes('33.33%')));assert.equal(JSON.stringify(entry),before);
});
