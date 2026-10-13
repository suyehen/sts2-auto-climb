import test from 'node:test';
import assert from 'node:assert/strict';
import {decisionSourceStats,renderDecisionStats} from '../public/presentation.js';

test('decision rates use recorded choices rather than analyses, retries or actions',()=>{
 assert.deepEqual(decisionSourceStats({counts:{local_choice:3,ai_choice:1,decision:12,agent_request:8,action_result:6}}),{local:3,ai:1,total:4,localRate:.75,aiRate:.25});
});
test('single-source, zero choices and missing archives are distinct',()=>{
 assert.equal(decisionSourceStats({counts:{ai_choice:2}}).aiRate,1);
 assert.equal(decisionSourceStats({counts:{local_choice:2}}).localRate,1);
 assert.equal(decisionSourceStats({counts:{}}).total,0);
 assert.equal(decisionSourceStats({counts:{}}).localRate,null);
 assert.equal(decisionSourceStats(null).total,null);
});
test('stats show counts and rates and reset when changing runs',()=>{
 const old=globalThis.document;
 globalThis.document={createElement:()=>({children:[],textContent:'',append(...nodes){this.children.push(...nodes)},replaceChildren(...nodes){this.children=nodes}})};
 const text=n=>[n.textContent,...n.children.map(text)].join(' ');
 try{
  const host=document.createElement('div');renderDecisionStats(host,{counts:{local_choice:3,ai_choice:1}});
  assert.match(text(host),/本地决策率 75.0% 3 次.*AI 决策率 25.0% 1 次.*4 次选择/);
  renderDecisionStats(host,{counts:{}});assert.match(text(host),/尚未记录决策选择/);assert.doesNotMatch(text(host),/75.0%|3 次|NaN/);
  renderDecisionStats(host,null);assert.match(text(host),/数量未记录/);
 }finally{globalThis.document=old}
});
