import test from 'node:test';
import assert from 'node:assert/strict';
import {renderDecision} from '../public/presentation.js';

class Element{
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.open=false;}
 append(...children){this.children.push(...children)}
 replaceChildren(...children){this.children=children}
 querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tag===tag?[c]:[]),...c.querySelectorAll(tag)])}
 querySelector(tag){return this.querySelectorAll(tag)[0]}
}
const text=node=>[node.textContent,...node.children.map(text)].join(' ');
const visible=node=>[node.textContent,...node.children.filter(c=>node.tag!=='details'||node.open||c.tag==='summary').map(visible)].join(' ');

test('live decisions foreground predictions and hide duplicate game resources and detailed metrics',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const host=new Element('div'),record={snapshot:{state:{player:{creature:{hp:73,max_hp:80},energy:3,gold:129}}},report:{combat:{candidates:[{title:'攻击并防御',metrics:{effective_hp_damage:16,projected_hp_loss:2}}]},mechanism_audit:{classification:'estimated',entities:[],enemies:[]}}};
  const before=JSON.stringify(record);renderDecision(host,record,'metrics',null,null,{live:true});
  assert.match(visible(host),/有效伤害/);assert.match(visible(host),/预计失血/);assert.doesNotMatch(visible(host),/当时生命|当时能量|当时金币/);
  assert.ok(host.querySelectorAll('details').every(d=>!d.open));assert.equal(JSON.stringify(record),before);
  renderDecision(host,record);assert.match(text(host),/当时生命/);
 }finally{globalThis.document=old}
});

test('polling preserves expanded calculation details',()=>{
 const old=globalThis.document;globalThis.document={createElement:tag=>new Element(tag)};
 try{
  const host=new Element('div'),record={report:{},snapshot:{state:{}}};
  renderDecision(host,record,'summary',null,null,{live:true});
  const details=host.querySelectorAll('details').find(d=>d.querySelector('summary')?.textContent==='计算细节与评分权重');details.open=true;
  renderDecision(host,record,'summary',null,null,{live:true});
  assert.equal(host.querySelectorAll('details').find(d=>d.querySelector('summary')?.textContent==='计算细节与评分权重').open,true);
 }finally{globalThis.document=old}
});
