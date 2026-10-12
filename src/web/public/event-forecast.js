/* Read-only human projection shared by live and historical decisions. */
export function eventForecastFacts(candidate){
 const labels={immediate_utility:'本次选择收益',future_utility:'后续选择收益',downside_penalty:'风险扣分',expected_hp_change:'本次生命变化期望',expected_max_hp_change:'本次生命上限变化期望',expected_gold_change:'本次金币变化期望'};
 const facts=[];for(const [key,label]of Object.entries(labels)){const v=candidate[key];if(typeof v==='number')facts.push(label+'：'+v.toFixed(2));}
 if(typeof candidate.fatal_probability==='number')facts.push('模型内致死概率：'+(100*candidate.fatal_probability).toFixed(2)+'%');
 if(candidate.known===false)facts.push('存在未知效果或后续选项，总收益尚不能确定。');
 if(candidate.repeats!=null)facts.push('继续选择 '+candidate.repeats+' 次后离开；最终生命变化 '+candidate.hp_change+'，生命上限变化 '+candidate.max_hp_gain+'。');
 return facts;
}

export function eventBranchFacts(candidate,limit=100){
 const facts=[];let visited=0;
 function walk(c,prefix,depth){
  if(depth>8||visited>=limit){if(!facts.includes('展示已折叠到预算上限，完整数据见程序员视图。'))facts.push('展示已折叠到预算上限，完整数据见程序员视图。');return;}
  for(const b of c.branches||[]){if(visited++>=limit)break;const probability=(b.probability*100).toFixed(2)+'%',r=b.immediate;
   facts.push(prefix+'分支 '+probability+(r?'：生命 '+r.hp_after+'，上限 '+r.max_hp_after+'，金币 '+r.gold_after+(r.fatal?'（死亡）':''):'：'+(b.reason||'结果未知')));
   if(b.continuation){for(const next of b.continuation.candidates||[])walk(next,prefix+'后续「'+(next.title||({Decipher:'继续解读',GiveUp:'放弃',Smash:'砸碎',ReachDeeper:'深入探索',ExtractCurrentPrize:'采集花蜜',ExtractInstead:'采集花蜜',ObtainPollinousCore:'抵达核心'})[next.option_id]||'选项')+'」→ ',depth+1);}
  }
 }
 walk(candidate,'',0);return facts;
}
