"""Refresh local event/monster/intent references from the installed game, offline."""
import argparse,hashlib,json,os,re,subprocess
from datetime import datetime,timezone
from pathlib import Path
from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import read_json, dataset_path
from autoslay.knowledge_base.game_pack import GamePack
from autoslay.knowledge_base.behavior import save,load
from autoslay.knowledge_base.catalog import CATALOG_PATH
from autoslay.knowledge_base.source_audit import body
from autoslay.knowledge_base.monster_values import parameters,thresholds,resolve,numeric_moves,arguments,call_end
from autoslay.knowledge_base.behavior_graph import graph as state_graph, methods as all_methods, properties, card_links, dynamic_vars, expand_helpers
from autoslay.knowledge_base.event_model import extract as event_model,render_template,event_methods


def identity(name):return re.sub(r'(?<!^)(?=[A-Z][a-z]|(?<=[a-z0-9])[A-Z])','_',name).upper()


def source_folder(assembly,digest):
    folder=ROOT/'.runtime/cache'/('behavior-source-'+digest[:16]);marker=folder/'source-manifest.json'
    if marker.exists() and json.loads(marker.read_text()).get('game_assembly_sha256')==digest:return folder
    env=dict(os.environ,DOTNET_ROOT=str(ROOT/'.runtime/tools/dotnet'),DOTNET_ROLL_FORWARD='Major')
    subprocess.run([str(ROOT/'.runtime/tools/ilspy/ilspycmd.exe'),'-p','-o',str(folder),str(assembly)],env=env,check=True,timeout=600,capture_output=True)
    marker.write_text(json.dumps(dict(game_assembly_sha256=digest,tool='ILSpy 9.1')),encoding='utf-8')
    return folder


def refresh(game):
    assembly=game/'data_sts2_windows_x86_64/sts2.dll';digest=hashlib.sha256(assembly.read_bytes()).hexdigest()
    catalog=read_json(CATALOG_PATH, fresh=True)
    if digest!=catalog['game_assembly_sha256']:raise ValueError('Refresh game encyclopedia first; assembly mismatch')
    pack=GamePack(game/'SlayTheSpire2.pck');folder=source_folder(assembly,digest);entries=[]
    levels=thresholds((folder/'MegaCrit.Sts2.Core.Entities.Ascension/AscensionLevel.cs').read_text(encoding='utf-8-sig'))
    powers=json.loads(pack.read('localization/zhs/powers.json'))
    enchantments=json.loads(pack.read('localization/zhs/enchantments.json'))
    from autoslay.knowledge_base.events import rules as event_rules
    rules=event_rules()
    for category,table_name,directory in [('event','events','MegaCrit.Sts2.Core.Models.Events'),('monster','monsters','MegaCrit.Sts2.Core.Models.Monsters'),('intent','intents','MegaCrit.Sts2.Core.MonsterMoves.Intents')]:
        resource=f'localization/zhs/{table_name}.json';localization=json.loads(pack.read(resource))
        if category=='intent':
            for key,text in localization.items():
                if not key.endswith('.title'):continue
                stem=key.split('.')[0];entry=dict(entity_id='INTENT.'+stem,category=category,name=text,description=localization.get(stem+'.description',''),
                    calculation_status='reference_only',provenance=dict(kind='local_game_pack',resource=resource,game_assembly_sha256=digest))
                entries.append(entry)
            continue
        for path in sorted((folder/directory).glob('*.cs')):
            own_text=path.read_text(encoding='utf-8-sig')
            if category=='monster' and re.search(r'public abstract class\s+',own_text):continue
            chain=[(path,own_text)]
            if category=='monster':
                while True:
                    parent=re.search(r'public (?:sealed )?class \w+\s*:\s*(\w+)',chain[-1][1])
                    parent_path=path.parent/(parent[1]+'.cs') if parent else None
                    if not parent_path or not parent_path.exists() or parent_path in [p for p,_ in chain]:break
                    chain.append((parent_path,parent_path.read_text(encoding='utf-8-sig')))
            text='\n'.join(t for _,t in chain);stem=identity(path.stem);prefix=stem+'.'
            alias=re.search(r'L10NMonsterLookup\("([A-Z0-9_]+)\.name"\)',text) if category=='monster' else None
            local_stem=alias[1] if alias else stem
            prefix=local_stem+'.'
            rows={k:v for k,v in localization.items() if k.startswith(prefix) and not re.search(r'multiplayer|coop|co_op',k,re.I)}
            title=rows.get(local_stem+('.name' if category=='monster' else '.title'))
            if not title or 'Deprecated' in path.stem or 'Mock' in path.stem:continue
            entity=category.upper()+'.'+stem
            variables={m[1]:m[2] for m in re.finditer(r'new (\w+)Var\(([^;\n]*?)\)',text)}
            expressions={}
            for _,source_text in reversed(chain):expressions.update(parameters(source_text))
            methods=all_methods(text)
            for method in re.findall(r'(?:private|protected|public)\s+(?:override\s+)?(?:async\s+)?(?:Task(?:<[^>]+>)?|MonsterMoveStateMachine|IReadOnlyList<EventOption>)\s+(\w+)\(',text):
                if method in {'GenerateAnimator','OnEnqueuePlayVfx'}:continue
                code=body(text,method)
                if code and any(token in code for token in ['Cmd.','Cmd<','MoveState','EventOption','SetEvent']):methods[method]=code.strip()
            provenance=dict(kind='local_game_source_and_localization',resource=resource,game_assembly_sha256=digest,
                            source_file=path.relative_to(folder).as_posix(),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            if len(chain)>1:provenance['inherited_sources']=[dict(file=p.relative_to(folder).as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p,_ in chain[1:]]
            entry=dict(entity_id=entity,category=category,name=title,description=rows.get(stem+'.pages.INITIAL.description',''),
                calculation_status='source_extracted_unreviewed',provenance=provenance,localization=rows,method_evidence=methods,
                parameter_expressions=expressions,dynamic_variable_expressions=variables,
                limitations=['Source excerpts and expressions are reference evidence, not executed C# or fully compiled rules.'])
            if category=='event':
                entry['placeholder_entities'],entry['referenced_cards']=card_links(text,identity,catalog)
                related={}
                for kind,name in re.findall(r'ModelDb\.(Enchantment|Power)<([\w.]+)>',text):
                    short=name.split('.')[-1];table=enchantments if kind=='Enchantment' else powers;stem_id=identity(short)
                    source_path=folder/('MegaCrit.Sts2.Core.Models.'+('Enchantments' if kind=='Enchantment' else 'Powers'))/(short+'.cs')
                    localized={k:v for k,v in table.items() if k.startswith(stem_id+'.')}
                    related[kind.upper()+'.'+stem_id]=dict(entity_id=kind.upper()+'.'+stem_id,name=localized.get(stem_id+'.title',short),
                        description=localized.get(stem_id+'.description',''),localization=localized,
                        methods=all_methods(source_path.read_text(encoding='utf-8-sig')) if source_path.exists() else {})
                for m in re.finditer(r'new StringVar\("([^"]+)",\s*ModelDb\.(Enchantment|Power)<([\w.]+)>',text):
                    linked=related.get(m[2].upper()+'.'+identity(m[3].split('.')[-1]))
                    if linked:entry['placeholder_entities'][m[1]]={k:linked[k] for k in ('entity_id','name')}
                entry['related_mechanics']=related
                options=[]
                for key,value in rows.items():
                    if '.options.' in key and key.endswith('.title'):
                        base=key[:-6];options.append(dict(text_key=base,title=value,description=rows.get(base+'.description','')))
                for option in options:
                    option['description_template']=option['description']
                    for key,linked in entry['placeholder_entities'].items():
                        option['description']=re.sub(r'\{'+re.escape(key)+r'(?:[^}]*)\}',linked['name'],option['description'])
                    mentioned=[linked for key,linked in entry['placeholder_entities'].items() if '{'+key in option['description_template']]
                    option['referenced_cards']=[c for c in mentioned if c['entity_id'].startswith('CARD.')]
                    option['referenced_entities']=mentioned
                    option_key=option['text_key'].split('.options.')[-1]
                    for match in re.finditer(r'new EventOption\(',text):
                        args=arguments(text[match.end():call_end(text,match.end()-1)])
                        if len(args)>2 and '"'+option_key+'"' in args[2]:
                            option['handler']=args[1];option['execution_evidence']=methods.get(args[1],'')
                entry['options']=options
                entry['action_bindings']=[dict(handler=m[1],text_key=m[2]) for m in re.finditer(r'new EventOption\(this,\s*(\w+),\s*"([^"]+)"',text)]
                entry['flow_reference']=dict(repeat_possible=bool(re.search(r'new EventOption\(this,\s*(Linger|Repeat|Continue)',text)),
                    finish_calls=len(re.findall(r'SetEventFinished\(',text)),requires_live_stage=True,
                    note='Repeat flags are source syntax hints; option state and costs must be read live.')
                entry['event_model']=event_model(text,entry,levels,catalog,identity)
                if 'HealRestSiteOption.GetHealAmount' in text:
                    helper=folder/'MegaCrit.Sts2.Core.Entities.RestSite/HealRestSiteOption.cs'
                    definitions=event_methods(helper.read_text(encoding='utf-8-sig'))
                    entry['event_model']['external_helpers']={'HealRestSiteOption':{k:v for k,v in definitions.items() if k in ('GetHealAmount','GetBaseHealAmount')}}
                if entity in rules['entries'] and rules.get('game_assembly_sha256')==digest:
                    entry['calculation_status']='reviewed_existing_rule';entry['existing_rule']=rules['entries'][entity]
            else:
                graph=body(text,'GenerateMoveStateMachine') or ''
                entry['state_graph']=state_graph(text)
                initial_values=expressions|properties(text)|{f['name']:f['initial_expression'] for f in entry['state_graph']['state_fields']}
                _,entry['referenced_cards']=card_links(text,identity,catalog)
                moves=[]
                for node in entry['state_graph']['nodes']:
                    if node['kind']!='MoveState':continue
                    state=node['state_id'];handler=node['handler'];intent=node['intent_expression']
                    moves.append(dict(variable=node['variable'],state_id=state,handler=handler,intent_expression=intent,
                        title=rows.get(local_stem+'.moves.'+state.removesuffix('_MOVE')+'.title',state),
                        numeric_variants=numeric_moves(intent,methods.get(handler,''),expressions,levels),
                        execution_evidence=methods.get(handler,'')))
                    for band in moves[-1]['numeric_variants']:
                        for effect in band['effects']:
                            if effect.get('power'):effect['name']=powers.get(identity(effect['power'])+'.title','状态效果')
                entry['moves']=moves
                entry['health_variants']=[]
                for level in range(max(levels.values())+1):
                    row=dict(ascension_min=level,ascension_max=level,
                        min_hp=resolve(expressions.get('MinInitialHp',''),initial_values,level,levels),
                        max_hp=resolve(expressions.get('MaxInitialHp',''),initial_values,level,levels))
                    previous=(entry['health_variants'] or [None])[-1]
                    if previous and (previous['min_hp'],previous['max_hp'])==(row['min_hp'],row['max_hp']):previous['ascension_max']=level
                    else:entry['health_variants'].append(row)
                entry['numeric_note']='基础行动数值按游戏进阶条件分组；力量、虚弱、阶段计数及目标状态以战斗实时值为准。无法静态求值的参数标为动态，不以零代替。'
                entry['transitions']=entry['state_graph']['edges']
                entry['health_parameter_expressions']={k:initial_values[k] for k in ('MinInitialHp','MaxInitialHp','RespawnMaxHpBonus','RespawnCount','StockAmount') if k in initial_values}
                entry['initial_effects']=numeric_moves('',expand_helpers(methods.get('AfterAddedToRoom',''),methods),initial_values,levels)
                for band in entry['initial_effects']:
                    for effect in band['effects']:
                        if effect.get('power'):effect['name']=powers.get(identity(effect['power'])+'.title','状态效果')
                entry['mechanics']={k:v for k,v in methods.items() if k!='GenerateMoveStateMachine' and k not in {m['handler'] for m in moves} and not any(word in k for word in ('Anim','Vfx','Sfx','Sound','Visual'))}
                entry['special_attributes']=properties(text)
                entry['runtime_state_changes']=[dict(method=k,target=m[1],evidence=v) for k,v in methods.items()
                    for m in re.finditer(r'(?:SetMoveImmediate|ForceCurrentState)\(([^,)]+)',v)]
                for move in moves:
                    move['state_updates']=[dict(variable=m[1],value_expression=m[2].strip()) for m in re.finditer(r'\b([A-Z_]\w*)\s*=\s*([^;]+);',move['execution_evidence'])]
                entry['powers']=[]
                for power in sorted(set(re.findall(r'(?:Apply|HasPower|GetPower|FromPower)<(\w+Power)>',text))):
                    power_path=folder/'MegaCrit.Sts2.Core.Models.Powers'/(power+'.cs')
                    power_text=power_path.read_text(encoding='utf-8-sig') if power_path.exists() else ''
                    power_id=identity(power)
                    localized={k:v for k,v in powers.items() if k.startswith(power_id+'.')}
                    power_vars=dynamic_vars(power_text);power_parameters=parameters(power_text)
                    entry['powers'].append(dict(entity_id='POWER.'+power_id,name=powers.get(power_id+'.title',power),localization=localized,
                        description=localized.get(power_id+'.description',''),hooks=all_methods(power_text),attributes=properties(power_text),
                        power_type=(re.search(r'PowerType\.([A-Za-z]+)',power_text) or [None,None])[1],
                        stack_type=(re.search(r'PowerStackType\.([A-Za-z]+)',power_text) or [None,None])[1],
                        variable_expressions=power_vars,variable_values={k:resolve(v,power_parameters,0,levels) for k,v in power_vars.items()}))
                entry['initial_state_expression']=(re.search(r'new MonsterMoveStateMachine\([^;]+?,\s*(\w+)\)',graph) or [None,None])[1]
                entry['behavior_summary']='行动图与意图来自当前游戏代码；条件分支、随机权重和阶段变化需结合方法证据及实时状态核实。'
            entries.append(entry)
    by_id={e['entity_id']:e for e in entries}
    for entry in entries:
        if entry['category']!='event':continue
        for variable in entry['event_model']['variables'].values():
            match=re.search(r'ModelDb.Monster<(\w+)>\(\)\.MinInitialHp',variable['expression'])
            if not match:continue
            monster=by_id.get('MONSTER.'+identity(match[1]))
            if not monster:continue
            variable['monster_reference']=dict(entity_id=monster['entity_id'],name=monster['name'],health_variants=monster['health_variants'])
            hp={v['min_hp'] for v in monster['health_variants']}
            if len(hp)==1 and None not in hp:
                variable['singleplayer_preview']=next(iter(hp));variable['display_rule']='单人模式初始生命 '+str(variable['singleplayer_preview'])
        for option in entry['options']:
            option['description'],_=render_template(option['description_template'],entry['event_model']['variables']|option.get('preview_variables',{}),entry['placeholder_entities'])
    data=dict(schema_version='behavior/1.0',game_version=catalog['game_version'],game_assembly_sha256=digest,
        exported_at=datetime.now(timezone.utc).isoformat(),source_policy='local_game_only',ascension_thresholds=levels,entries=entries)
    result=save(data);result['counts']={c:sum(e['category']==c for e in entries) for c in ('event','monster','intent')};print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--game-path',type=Path,default=Path('E:/steam/steamapps/common/Slay the Spire 2'))
    refresh(parser.parse_args().game_path)
