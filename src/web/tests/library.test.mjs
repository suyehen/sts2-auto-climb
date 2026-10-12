import test from 'node:test';
import assert from 'node:assert/strict';
import {upgradeChanges,formatMonsterEffect,branchPaths,powerDescription} from '../public/library.js';
test('conditional destinations expose every path and integer health threshold',()=>{
 const entry={moves:[{variable:'tongue',title:'吐舌'},{variable:'charge',title:'甲虫冲锋'}],state_graph:{definitions:{},nodes:[{variable:'branch',kind:'ConditionalBranchState',branches:[{target:'tongue',condition_expression:'HasBeetleCharged || base.Creature.CurrentHp >= base.Creature.MaxHp / 2'},{target:'charge',condition_expression:'!HasBeetleCharged && base.Creature.CurrentHp < base.Creature.MaxHp / 2'}]}]}};
 const paths=branchPaths(entry,'branch');assert.equal(paths.length,2);assert.match(paths[0],/向下取整/);assert.match(paths[1],/甲虫冲锋/);assert.match(paths[1],/非 已经使用甲虫冲锋/);
});
test('power text never hardcodes the first ascension band and leaves source immutable',()=>{
 const power={entity_id:'POWER.PLATING_POWER',description:'覆甲',variable_values:{Decrement:1},localization:{'PLATING_POWER.smartDescription':'获得{Amount}点格挡，减少{Decrement}层。'}};
 const entry={name:'青蛙骑士',initial_effects:[{effects:[{power:'PlatingPower',amount:15}]},{effects:[{power:'PlatingPower',amount:19}]}]};const original=JSON.stringify(power);
 assert.equal(powerDescription(entry,power),'获得当前层数点格挡，减少1层。');assert.equal(JSON.stringify(power),original);
});
const highlighted=result=>result.parts.filter(p=>p.changed).map(p=>p.text).join('');
test('monster base numbers and unresolved state have distinct human labels',()=>{
 assert.equal(formatMonsterEffect({kind:'attack',damage:4,repeats:3,total_damage:12}),'伤害 4 × 3 = 12');
 assert.equal(formatMonsterEffect({kind:'attack',damage:null,repeats:2,total_damage:null}),'伤害 动态 × 2');
 assert.equal(formatMonsterEffect({kind:'block',amount:15}),'格挡 15');
 assert.equal(formatMonsterEffect({kind:'power',name:'力量',amount:3}),'力量 3 层');
});
test('upgraded numbers are highlighted as complete values',()=>{
 const diff=upgradeChanges('造成10点伤害。','造成12点伤害。');
 assert.equal(highlighted(diff),'12');assert.equal(diff.parts.map(p=>p.text).join(''),'造成12点伤害。');
});
test('added keywords and replacement targets are visible without coloring unchanged text',()=>{
 assert.equal(highlighted(upgradeChanges('抽2张牌。','固有。\n抽2张牌。')),'固有。\n');
 assert.equal(highlighted(upgradeChanges('升级一张牌。','升级所有牌。')),'所有');
});
test('removed effects get a separate note and markup is normalized before comparison',()=>{
 assert.deepEqual(upgradeChanges('虚无。\n获得1层无实体。','获得1层无实体。').removed,['虚无。']);
 assert.equal(highlighted(upgradeChanges('[gold]抽3张牌。[/gold]','抽4张牌。')),'4');
 assert.equal(highlighted(upgradeChanges('效果相同','效果相同')),'');
});
