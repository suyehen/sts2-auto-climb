import test from 'node:test';
import assert from 'node:assert/strict';
import {observedEffectsPanel} from '../public/presentation.js';

test('actual benefits include resource changes and confirmed enchantment text',()=>{
 const previous=globalThis.document;
 globalThis.document={createElement:()=>({children:[],textContent:'',append(...children){this.children.push(...children)}})};
 try{
  const panel=observedEffectsPanel([{status:'observed',resources:{gold:{before:139,after:203,change:64}},collections:{deck:{modified:[{name:'痛击',changes:{description:{before:'造成8点伤害。',after:'造成10点伤害。'}}}]}},creatures:[{instance_id:'enemy',name:'同族随从',hp_before:50,hp_after:38,powers:[]}],notes:[]}]);
  const text=n=>[n.textContent,...n.children.map(text)].join(' ');
  assert.match(text(panel),/139 → 203（\+64）/);
  assert.match(text(panel),/造成8点伤害。 → 造成10点伤害。/);
  assert.match(text(panel),/同族随从 · 生命：50 → 38/);
 }finally{globalThis.document=previous}
});
