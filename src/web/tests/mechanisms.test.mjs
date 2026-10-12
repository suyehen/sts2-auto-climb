import test from 'node:test';
import assert from 'node:assert/strict';
import {mechanismPanel} from '../public/presentation.js';

test('human panel shows unknown flags without substituting defaults or claiming win probability',()=>{
 const previous=globalThis.document;
 globalThis.document={createElement:()=>({children:[],textContent:'',append(...children){this.children.push(...children)}})};
 try{
  const audit={classification:'estimated',unknown_state_count:1,enemies:[{name:'青蛙骑士',fields:[{name:'_hasBeetleCharged',status:'unknown',value:null}],field_aliases:{HasBeetleCharged:'_hasBeetleCharged'}}],entities:[],tracking:{changes:[]}};
  const before=JSON.stringify(audit),panel=mechanismPanel(audit);
  const text=node=>[node.textContent,...node.children.map(text)].join(' ');
  const visible=text(panel);
  assert.match(visible,/有条件估算/);assert.match(visible,/已使用甲虫冲锋/);
  assert.match(visible,/未知（未读取，不能按初始值计算）/);assert.match(visible,/收益分尚未校准为胜率/);
  assert.equal(JSON.stringify(audit),before);
 }finally{globalThis.document=previous}
});
