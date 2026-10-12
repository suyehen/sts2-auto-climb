import test from 'node:test';
import assert from 'node:assert/strict';
import {combatForecastFacts} from '../public/combat-forecast.js';
test('human combat forecast distinguishes modeled turns and generated pile placement',()=>{
 const forecast={horizon:2,enemy_model:{mode:'local_behavior_graph'},candidates:[{group:'candidates',index:0,scenarios:[{result:{covered_probability:1,conditional_metrics:{hp_loss:4.5,remaining_enemy_hp:20}},enemy_turns:[{turn:1,name:'史莱姆',move_title:'黏液',next_move_title:'冲撞',next_attacks:[9],probability:1,generated_cards:[{name:'黏液',amount:2,pile:'discard'}]}]}]}]};
 const before=JSON.stringify(forecast),lines=combatForecastFacts(forecast,{state:{player:{turn:1}}}).join('\n');
 assert.match(lines,/预计失血 4.5/);assert.match(lines,/下一招攻击 9/);assert.match(lines,/2 张黏液，置入弃牌堆/);assert.match(lines,/不代表整场战斗胜率/);
 assert.equal(JSON.stringify(forecast),before);
});
test('missing and partial predictions never imply zero damage or complete safety',()=>{
 const forecast={horizon:2,unmodeled:['unknown'],candidates:[{group:'candidates',index:0,scenarios:[{result:null}]},{group:'candidates',index:1,scenarios:[{result:{covered_probability:.25,conditional_metrics:{hp_loss:3,remaining_enemy_hp:10}}}]}]};
 const text=combatForecastFacts(forecast).join('\n');assert.match(text,/后续结果未知/);assert.match(text,/25%/);assert.match(text,/只适用于已计算部分/);assert.match(text,/没有按零伤害计算/);assert.doesNotMatch(text,/预计失血 0/);
});
