import test from 'node:test';
import assert from 'node:assert/strict';
import {geometry,coordinate,mapIcon} from '../public/map.js';
import {reason,metricValue,gameText} from '../public/presentation.js';

test('map icons use extracted game textures and never invented boss or ancient artwork',()=>{
 assert.equal(mapIcon({type:'Monster'}),'/assets/map/MAP.MAP_MONSTER.webp');
 assert.equal(mapIcon({type:'RestSite'}),'/assets/map/MAP.MAP_REST.webp');
 assert.equal(mapIcon({type:'Boss'}),null);
 assert.equal(mapIcon({type:'Ancient',icon_asset:'MAP.ANCIENT_NODE_NEOW'}),'/assets/map/MAP.ANCIENT_NODE_NEOW.webp');
 assert.equal(mapIcon({type:'Boss',icon_asset:'../outside'}),null);
});

test('native layout preserves game offsets instead of inventing grid positions',()=>{
 const nodes=[{coordinate:{column:0,row:0},visual_position:{x:20,y:200}},{coordinate:{column:1,row:1},visual_position:{x:160,y:-20}}];
 const g=geometry(nodes);assert.equal(g.native,true);assert.equal(g.xy(nodes[1])[0]-g.xy(nodes[0])[0],140);assert.ok(g.xy(nodes[1])[1]<g.xy(nodes[0])[1]);
});
test('older archives fall back to actual graph grid coordinates',()=>{
 const nodes=[{coordinate:{column:0,row:0}},{coordinate:{column:2,row:2}}];
 const g=geometry(nodes);assert.equal(g.native,false);assert.equal(g.xy(nodes[1])[0]-g.xy(nodes[0])[0],168);assert.equal(coordinate(nodes[1].coordinate),'2:2');
});
test('partly missing native positions do not produce invalid SVG coordinates',()=>{
 const nodes=[{coordinate:{column:1,row:0},visual_position:{x:20,y:20}},{coordinate:{column:2,row:2}}];
 const g=geometry(nodes);assert.equal(g.native,false);assert.ok(nodes.flatMap(g.xy).every(Number.isFinite));
});
test('human labels and probabilities stay distinct from technical identifiers and scores',()=>{
 assert.equal(reason('PLAN_COMPLETED'),'动作计划已完成');assert.ok(!reason('NEW_TECHNICAL_CODE').includes('NEW_TECHNICAL_CODE'));
 assert.equal(metricValue('fatal_probability',0.2),'20.0%');assert.equal(metricValue('heuristic_score',0.2),'0.20');
});
test('game text displays energy and stars without resource URLs',()=>{
 const energy='[img]res://images/packed/sprite_fonts/ironclad_energy_icon.png[/img]';
 assert.equal(gameText('获得'+energy+energy+'。'),'获得2点能量。');
 assert.equal(gameText('0'+energy+'牌'),'0点能量牌');
 assert.equal(gameText('[gold]格挡[/gold]'),'格挡');
});
