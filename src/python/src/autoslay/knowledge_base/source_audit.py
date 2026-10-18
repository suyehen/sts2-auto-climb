"""Complete source coverage audit and conservative straight-line rule extraction.

This recognizes a restricted grammar, not arbitrary C#. Unknown code is retained
as a coverage gap. A source-recognized rule is not a live-validated rule.
"""
import hashlib
import json
import re
from pathlib import Path
from autoslay.paths import ROOT
from autoslay.knowledge_base.rulebook import entry_fingerprint,validate_rulebook

SOURCE=ROOT/'.runtime/cache/game-source'

def body(text,method):
    match=re.search(r'\b'+re.escape(method)+r'\([^\n]*\)\s*\{',text)
    if not match: return None
    start=match.end(); depth=1
    for index in range(start,len(text)):
        if text[index]=='{': depth+=1
        elif text[index]=='}':
            depth-=1
            if depth==0: return text[start:index]
    raise ValueError('Unbalanced source method')

def recognize_card(text):
    code=body(text,'OnPlay')
    if code is None: return None,'no_OnPlay_or_inherited_behavior'
    hooks=set(re.findall(r'override\s+(?:async\s+)?[\w<>?]+\s+(\w+)\s*\(',text))
    unknown_hooks=hooks-{'OnPlay','OnUpgrade','OnEnqueuePlayVfx'}
    if unknown_hooks: return None,'other_behavior_hooks:'+','.join(sorted(unknown_hooks))
    keywords=set(re.findall(r'CardKeyword\.(\w+)',text))
    if keywords-{'Exhaust'}: return None,'keyword_lifecycle:'+','.join(sorted(keywords-{'Exhaust'}))
    if re.search(r'override\s+[\w<>?]+\s+(IsPlayable|EnergyCost|CurrentStarCost|HasStarCostX|TargetType)\b',text):
        return None,'dynamic_playability_or_resource_property'
    if 'CardType.Power' in text:
        return None,'power_card_lifecycle_and_after_power_play_hooks_required'
    if re.search(r'\b(if|for|foreach|while|switch|return|throw)\b',code): return None,'conditional_or_iterative_behavior'
    effects=[]
    for statement in code.split(';'):
        statement=' '.join(statement.split())
        if not statement: continue
        if re.fullmatch(r'ArgumentNullException\.ThrowIfNull\(cardPlay.Target, "cardPlay.Target"\)',statement): continue
        if re.fullmatch(r'await CreatureCmd\.GainBlock\(base.Owner.Creature, base.DynamicVars.Block, cardPlay\)',statement):
            effects.append(dict(operation='block',amount=dict(visible='block',invert_legion=True))); continue
        if re.fullmatch(r'await CardPileCmd\.Draw\(choiceContext, base.DynamicVars.Cards.(?:BaseValue|IntValue), base.Owner\)',statement):
            effects.append(dict(operation='draw',amount=dict(visible='draw'))); continue
        if re.fullmatch(r'await CreatureCmd\.TriggerAnim\(base.Owner.Creature, "(?:Cast|PowerUp)", base.Owner.Character.CastAnimDelay\)',statement):continue
        if re.fullmatch(r'await PlayerCmd\.GainEnergy\(base.DynamicVars.Energy.(?:BaseValue|IntValue), base.Owner\)',statement):
            effects.append(dict(operation='energy',amount=dict(visible='energy',energy_icons=True))); continue
        if statement=='await PlayerCmd.GainStars(base.DynamicVars.Stars.BaseValue, base.Owner)':
            effects.append(dict(operation='stars',amount=dict(variable='Stars')));continue
        if statement=='await PowerCmd.Apply<NoDrawPower>(choiceContext, base.Owner.Creature, 1m, base.Owner.Creature, this)':
            effects.append(dict(operation='no_draw',amount=1));continue
        power=re.fullmatch(r'await PowerCmd.Apply<(VulnerablePower|WeakPower|StrengthPower)>\(choiceContext, (cardPlay.Target|base.Owner.Creature), base.DynamicVars.(Vulnerable|Weak|Strength).BaseValue, base.Owner.Creature, this\)',statement)
        if power:
            expected={'VulnerablePower':('cardPlay.Target','Vulnerable','vulnerable'),
                      'WeakPower':('cardPlay.Target','Weak','weak'),
                      'StrengthPower':('base.Owner.Creature','Strength','strength')}[power[1]]
            if (power[2],power[3])!=expected[:2]: return None,'power_target_or_amount_mismatch'
            effects.append(dict(operation=expected[2],amount=dict(visible=power[3].lower()))); continue
        match=re.fullmatch(r'await DamageCmd\.Attack\(base.DynamicVars.Damage.BaseValue\)(?P<tail>.*?)\.Execute\(choiceContext\)',statement)
        if match:
            tail=match['tail']; repeats=1
            if tail.count('.FromCard(this, cardPlay)')!=1:return None,'attack_source_not_recognized'
            tail=tail.replace('.FromCard(this, cardPlay)','')
            target=re.findall(r'\.Targeting\(cardPlay.Target\)|\.TargetingAllOpponents\(base.Owner\)',tail)
            if len(target)!=1:return None,'attack_target_not_recognized'
            tail=tail.replace(target[0],'')
            count=re.search(r'\.WithHitCount\((\d+|base.DynamicVars.(?:Repeat|Hits).(?:BaseValue|IntValue))\)',tail)
            if count:
                repeats=int(count[1]) if count[1].isdigit() else dict(variable=count[1].split('.')[2])
                tail=tail.replace(count[0],'',1)
            tail=re.sub(r'\.WithHitFx\("[^"\\]+"\)','',tail)
            tail=re.sub(r'\.WithHitFx\("[^"\\]+", null, "[^"\\]+"\)','',tail)
            if tail.strip(): return None,'attack_builder_modifiers_not_recognized'
            effects.append(dict(operation='damage',amount=dict(visible='damage'),repeats=repeats)); continue
        return None,'statement_not_recognized:'+statement[:180]
    if not effects: return None,'no_recognized_effects'
    if 'CardKeyword.Retain' in text or 'CardKeyword.Ethereal' in text: return None,'hand_lifecycle_not_recognized'
    return dict(effects=effects,exhaust='CardKeyword.Exhaust' in text),None


def recognize_potion(text):
    """Recognize complete, narrowly defined OnUse programs, including cosmetic VFX.

    Every non-cosmetic statement must match. Random selection, generated cards,
    orb effects and trigger hooks remain explicit gaps, never zero effects.
    """
    code=body(text,'OnUse')
    if code is None: return None,'no_OnUse_or_inherited_behavior'
    hooks=set(re.findall(r'override\s+(?:async\s+)?[\w<>?]+\s+(\w+)\s*\(',text))
    if hooks-{'OnUse'}: return None,'other_behavior_hooks:'+','.join(sorted(hooks-{'OnUse'}))
    target=re.search(r'override TargetType TargetType => TargetType\.(\w+);',text)
    if not target or target[1] not in {'AnyPlayer','AnyEnemy','AllEnemies','Self'}:
        return None,'targeting_not_recognized'
    # Only these exact cosmetic constructs are discarded.
    code=re.sub(r'NCombatRoom\.Instance\?\.PlaySplashVfx\(target, new Color\("[a-fA-F0-9]+"\)\);','',code)
    code=re.sub(r'NCombatRoom\.Instance\?\.PlaySplashVfx\(target, Colors\.(?:Red|Purple)\);','',code)
    code=re.sub(r'NCombatRoom\.Instance\?\.CombatVfxContainer\.AddChildSafely\(NGroundFireVfx\.Create\(target\)\);','',code)
    cosmetic_loop=r'foreach \(Creature item in targets\)\s*\{\s*NCombatRoom\.Instance\?\.CombatVfxContainer\.AddChildSafely\(NFireSmokePuffVfx\.Create\(item\)\);\s*\}'
    code=re.sub(cosmetic_loop,'',code)
    code=re.sub(r'await Cmd\.CustomScaledWait\(0\.2f, 0\.3f\);','',code)
    if re.search(r'\b(if|for|foreach|while|switch|return|throw)\b',code):
        return None,'conditional_or_iterative_behavior'
    effects=[]; damage_declared=False; player_declared=False; targets_declared=False; strength_alias=False;dexterity_alias=False
    for statement in code.split(';'):
        statement=' '.join(statement.split())
        if not statement: continue
        if statement=='PotionModel.AssertValidForTargetedPotion(target)': continue
        if statement=='DamageVar damage = base.DynamicVars.Damage':
            if 'new DamageVar(' not in text or 'ValueProp.Unpowered' not in text:
                return None,'damage_properties_not_unpowered'
            damage_declared=True; continue
        if statement=='Creature player = base.Owner.Creature': player_declared=True; continue
        if statement=='IReadOnlyList<Creature> targets = player.CombatState.HittableEnemies' and player_declared:
            targets_declared=True; continue
        if statement=='decimal baseValue = base.DynamicVars.Strength.BaseValue': strength_alias=True; continue
        if statement=='decimal baseValue = base.DynamicVars.Dexterity.BaseValue':dexterity_alias=True;continue
        if statement=='await CreatureCmd.Heal(target, (decimal)target.MaxHp * base.DynamicVars["HealPercent"].BaseValue / 100m)' and target[1]=='AnyPlayer':
            effects.append(dict(operation='heal_percent',amount=dict(variable='HealPercent')));continue
        if statement=='await CreatureCmd.GainMaxHp(target, base.DynamicVars.MaxHp.BaseValue)' and target[1]=='AnyPlayer':
            effects.append(dict(operation='gain_max_hp',amount=dict(variable='MaxHp')));continue
        if statement=='await PlayerCmd.GainStars(base.DynamicVars.Stars.BaseValue, target.Player)' and target[1]=='AnyPlayer':
            effects.append(dict(operation='stars',amount=dict(variable='Stars')));continue
        if statement=='await CreatureCmd.GainBlock(target, base.DynamicVars.Block, null)' and re.search(r'new BlockVar\(\d+m, ValueProp.Unpowered\)',text):
            effects.append(dict(operation='unpowered_block',amount=dict(visible='block'))); continue
        if statement=='await CreatureCmd.GainBlock(target, target.Block * 2, ValueProp.Unpowered, null)':
            effects.append(dict(operation='multiply_block',amount=3)); continue
        if statement in {'await PlayerCmd.GainEnergy(base.DynamicVars.Energy.BaseValue, target.Player)',
                         'await PlayerCmd.GainEnergy(base.DynamicVars.Energy.IntValue, target.Player)'}:
            effects.append(dict(operation='energy',amount=dict(visible='energy',energy_icons=True))); continue
        if statement=='await CardPileCmd.Draw(choiceContext, base.DynamicVars.Cards.BaseValue, target.Player)':
            effects.append(dict(operation='draw',amount=dict(visible='draw'))); continue
        if statement=='await CreatureCmd.Damage(choiceContext, target, damage.BaseValue, damage.Props, base.Owner.Creature, null, null)' and damage_declared and target[1]=='AnyEnemy':
            effects.append(dict(operation='unpowered_damage',amount=dict(visible='damage'))); continue
        if statement=='await CreatureCmd.Damage(choiceContext, targets, damage.BaseValue, damage.Props, player, null, null)' and damage_declared and targets_declared and target[1]=='AllEnemies':
            effects.append(dict(operation='unpowered_damage',amount=dict(visible='damage'))); continue
        match=re.fullmatch(r'await PowerCmd.Apply<(StrengthPower|VulnerablePower|WeakPower|FlexPotionPower|DexterityPower|SpeedPotionPower)>\(choiceContext, target, (base.DynamicVars.(?:Strength|Vulnerable|Weak|Dexterity).BaseValue|baseValue), base.Owner.Creature, null\)',statement)
        if match:
            power=match[1]; expression=match[2]
            required={'StrengthPower':('strength','Strength','AnyPlayer'),'FlexPotionPower':('temporary_strength','Strength','AnyPlayer'),
                      'VulnerablePower':('vulnerable','Vulnerable','AnyEnemy'),'WeakPower':('weak','Weak','AnyEnemy'),
                      'DexterityPower':('dexterity','Dexterity','AnyPlayer'),'SpeedPotionPower':('temporary_dexterity','Dexterity','AnyPlayer')}[power]
            alias=(power=='FlexPotionPower' and strength_alias or power=='SpeedPotionPower' and dexterity_alias) and expression=='baseValue'
            if target[1]!=required[2] or expression!=f'base.DynamicVars.{required[1]}.BaseValue' and not alias:
                return None,'power_target_or_amount_mismatch'
            effects.append(dict(operation=required[0],amount=dict(variable='DexterityPower') if required[1]=='Dexterity' else dict(visible=required[1].lower()))); continue
        return None,'statement_not_recognized:'+statement[:180]
    if not effects: return None,'no_recognized_effects'
    return dict(effects=effects,target_type=target[1]),None

def recognize_relic(text):
    """Complete reviewed hook shapes; no arbitrary C# or description execution."""
    hooks=set(re.findall(r'override\s+(?:async\s+)?[\w<>?]+\s+(\w+)\s*\(',text))
    properties=set(re.findall(r'override\s+[\w<>?]+\s+(\w+)\s*(?:=>|\{)',text))
    if properties-{'Rarity','CanonicalVars','ExtraHoverTips','ShowCounter','DisplayAmount'}:
        return None,'relic_property_adapter_required'
    compact=lambda name:re.sub(r'\s+','',body(text,name) or '')
    unpowered_block=bool(re.search(r'new BlockVar\(\d+m, ValueProp.Unpowered\)',text))
    unpowered_damage=bool(re.search(r'new DamageVar\(\d+m, ValueProp.Unpowered\)',text))
    scheduled=[]
    if hooks=={'AfterBlockCleared'} and unpowered_block:
        match=re.fullmatch(r'if\(creature==base.Owner.Creature&&base.Owner.PlayerCombatState.TurnNumber==(\d+)\)\{Flash\(\);awaitCreatureCmd.GainBlock\(base.Owner.Creature,base.DynamicVars.Block,null\);\}',compact('AfterBlockCleared'))
        if match:scheduled=[dict(phase='after_block_cleared',turn=int(match[1]),operation='unpowered_block',variable='Block')]
    elif hooks=={'AfterSideTurnStart'}:
        code=compact('AfterSideTurnStart')
        block='if(participants.Contains(base.Owner.Creature)){Flash();awaitCreatureCmd.GainBlock(base.Owner.Creature,base.DynamicVars.Block,null);}'
        if code==block and unpowered_block:
            scheduled=[dict(phase='side_turn_start',turn=None,operation='unpowered_block',variable='Block')]
        energy=re.fullmatch(r'if\(participants.Contains\(base.Owner.Creature\)&&base.Owner.PlayerCombatState.TurnNumber==(\d+)\)\{Flash\(\);awaitPlayerCmd.GainEnergy\(base.DynamicVars.Energy.BaseValue,base.Owner\);\}',code)
        display_energy=re.fullmatch(r'if\(participants.Contains\(base.Owner.Creature\)\)\{if\(base.Owner.PlayerCombatState.TurnNumber==(\d+)\)\{Flash\(\);awaitPlayerCmd.GainEnergy\(base.DynamicVars.Energy.BaseValue,base.Owner\);\}InvokeDisplayAmountChanged\(\);\}',code)
        match=energy or display_energy
        if match:scheduled=[dict(phase='side_turn_start',turn=int(match[1]),operation='energy',variable='Energy')]
    elif hooks=={'AfterPlayerTurnStart'} and unpowered_damage:
        expected='if(player==base.Owner){Flash();awaitCreatureCmd.Damage(choiceContext,player.Creature.CombatState.HittableEnemies,base.DynamicVars.Damage,base.Owner.Creature);}'
        if compact('AfterPlayerTurnStart')==expected:
            scheduled=[dict(phase='after_hand_draw',turn=None,operation='unpowered_damage',variable='Damage')]
    elif hooks=={'AfterSideTurnStart','BeforeSideTurnEnd','AfterCombatEnd','AfterRoomEntered'} and unpowered_damage:
        expected={
            'AfterSideTurnStart':'''if (!participants.Contains(base.Owner.Creature)) { return Task.CompletedTask; }
                if (base.Owner.PlayerCombatState.TurnNumber == base.DynamicVars["DamageTurn"].IntValue) { base.Status = RelicStatus.Active; }
                InvokeDisplayAmountChanged(); return Task.CompletedTask;''',
            'BeforeSideTurnEnd':'''if (participants.Contains(base.Owner.Creature)) {
                int intValue = base.DynamicVars["DamageTurn"].IntValue; int turnNumber = base.Owner.PlayerCombatState.TurnNumber;
                base.Status = RelicStatus.Normal; if (turnNumber == intValue) {
                    TaskHelper.RunSafely(DoActivateVisuals());
                    await CreatureCmd.Damage(choiceContext, base.Owner.Creature.CombatState.HittableEnemies, base.DynamicVars.Damage, base.Owner.Creature);
                    InvokeDisplayAmountChanged(); } }''',
            'AfterCombatEnd':'base.Status = RelicStatus.Normal; InvokeDisplayAmountChanged(); return Task.CompletedTask;',
            'AfterRoomEntered':'''if (!(room is CombatRoom)) { return Task.CompletedTask; }
                base.Status = RelicStatus.Normal; InvokeDisplayAmountChanged(); return Task.CompletedTask;''',
            'DoActivateVisuals':'IsActivating = true; Flash(); await Cmd.Wait(1f); IsActivating = false;',
        }
        turn=re.search(r'new DynamicVar\("DamageTurn", (\d+)m\)',text)
        if turn and all(compact(name)==re.sub(r'\s+','',code) for name,code in expected.items()):
            scheduled=[dict(phase='side_turn_end',turn=int(turn[1]),operation='unpowered_damage',variable='Damage')]
    if not scheduled:return None,'relic_trigger_program_not_recognized'
    return dict(effects=[],scheduled=scheduled),None


def audit(catalog,rulebook,source=SOURCE):
    rows=[]; proposed=json.loads(json.dumps(rulebook))
    for entry in catalog['entries']:
        identity=entry['entity_id']; category=entry['category']
        model=entry.get('model_type')
        if model:
            namespace,classname=model.rsplit('.',1); path=Path(source)/namespace/(classname+'.cs')
        else:
            classname=''.join(w.title() for w in identity.split('.',1)[1].split('_'))
            path=Path(source)/('MegaCrit.Sts2.Core.Models.'+{'card':'Cards','potion':'Potions','relic':'Relics'}[category])/(classname+'.cs')
        row=dict(entity_id=identity,category=category,name=entry['name'],source_found=path.exists(),
                 data_present=True,rule_present=identity in rulebook['entries'],live_validation=False)
        if not path.exists(): row['gap']='source_model_mapping_missing'; rows.append(row); continue
        text=path.read_text(encoding='utf-8-sig'); row['source_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        row['hooks']=sorted(set(re.findall(r'override\s+(?:async\s+)?[\w<>?]+\s+(\w+)\s*\(',text)))
        row['commands']=sorted(set('.'.join(c) for c in re.findall(r'\b(\w+Cmd)\.(\w+)',text)))
        recognized,reason=(recognize_card(text) if category=='card' else recognize_potion(text) if category=='potion' else recognize_relic(text))
        row['recognized_sequence']=recognized; row['gap']=reason
        if identity not in proposed['entries']:
            proposed['entries'][identity]=dict(category=category,reference_sha256=entry_fingerprint(entry),
                validation='pending_review',source=str(path.relative_to(source)),source_sha256=row['source_sha256'],
                **(recognized or dict(effects=[])),review_gap=reason or 'source_recognized; require rule review and transition validation')
        rows.append(row)
    validate_rulebook(proposed)
    return dict(schema_version='source-coverage/1.0',game_version=catalog['game_version'],rows=rows,
                counts={c:dict(total=sum(r['category']==c for r in rows),source_found=sum(r['category']==c and r['source_found'] for r in rows),
                               straight_line_recognized=sum(r['category']==c and bool(r.get('recognized_sequence')) for r in rows)) for c in ('card','potion','relic')},
                full_support=False,description='Proposals remain pending; no unknown code assigned zero'),proposed
