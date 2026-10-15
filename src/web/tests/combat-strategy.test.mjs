import test from 'node:test';
import assert from 'node:assert/strict';
import {renderCombatStrategy,renderCombatBatches} from '../public/sidebar-combat.js';
import {renderCombatMetrics} from '../public/presentation.js';

class Element{
 constructor(){this.children=[];this.textContent='';}
 append(...children){this.children.push(...children)}
}
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
function render(fn){const old=globalThis.document;globalThis.document={createElement:()=>new Element()};
 try{const host=new Element();fn(host);return text(host)}finally{globalThis.document=old}}

test('shadow recommendation and missing calibration never imply execution or exact bounds',()=>{
 const strategy={mode:'shadow',horizon:2,search_complete:false,calibration_reasons:['missing'],
  first_actions:[{first_step:{action_id:'end_turn',target_id:null},Q_hat:10,lower:null,upper:null,
   bound_kind:'uncalibrated_model',utility_components:{self_hp_loss:-3,healing:2}}]};
 const value=render(host=>renderCombatStrategy(host,strategy,{state:{}}));
 assert.match(value,/影子观测.*不执行.*不计入自主率/);
 assert.match(value,/尚未通过.*校准/);assert.match(value,/尚无有效授权区间/);
 assert.match(value,/自伤 -3.*治疗 2/);
});

test('combat statistics separate active choices and model requests and preserve unknown',()=>{
 const value=render(host=>renderCombatMetrics(host,{choices:{local_rate:.5,total:4},
  active_choices:{local_rate:0,total:2},model_requests:{count:null},execution:{success_rate:null}}));
 assert.match(value,/战斗自主率 50.0%.*主动决策自主率 0.0%/);
 assert.match(value,/实际模型请求数 未记录/);assert.match(value,/缺少请求或质量证据时显示未知/);
});

test('batch display shows whole operations and the next decision boundary without a calibration gate',()=>{
 const value=render(host=>renderCombatBatches(host,{selected_batch:{batch_id:'a'},top_batches:[
  {batch_id:'a',steps:[{action_id:'play_a'},{action_id:'end_turn'}],score:12,stop_reason:'next_turn_requires_observation',
   approximation_count:1,utility_components:{run_hp_value:-6}}],limits:['time_limit'],assumptions:['unknown relic']},{state:{}}));
 assert.match(value,/本地操作批次.*不要求最优或校准/);
 assert.match(value,/已选择.*下一玩家回合/);
 assert.match(value,/整局生命价值变化 -6/);
 assert.match(value,/仍由本地选择/);
});
