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
        if re.fullmatch(r'await PlayerCmd\.GainEnergy\(base.DynamicVars.Energy.(?:BaseValue|IntValue), base.Owner\)',statement):
            effects.append(dict(operation='energy',amount=dict(visible='energy',energy_icons=True))); continue
        power=re.fullmatch(r'await PowerCmd.Apply<(VulnerablePower|WeakPower|StrengthPower)>\(choiceContext, (cardPlay.Target|base.Owner.Creature), base.DynamicVars.(Vulnerable|Weak|Strength).BaseValue, base.Owner.Creature, this\)',statement)
        if power:
            expected={'VulnerablePower':('cardPlay.Target','Vulnerable','vulnerable'),
                      'WeakPower':('cardPlay.Target','Weak','weak_boundary'),
                      'StrengthPower':('base.Owner.Creature','Strength','strength')}[power[1]]
            if (power[2],power[3])!=expected[:2]: return None,'power_target_or_amount_mismatch'
            effects.append(dict(operation=expected[2],amount=dict(visible=power[3].lower()))); continue
        match=re.fullmatch(r'await DamageCmd\.Attack\(base.DynamicVars.Damage.BaseValue\)\.FromCard\(this, cardPlay\)\.(?:Targeting\(cardPlay.Target\)|TargetingAllOpponents\(base.Owner\))(?P<tail>.*?)\.Execute\(choiceContext\)',statement)
        if match:
            tail=match['tail']; repeats=1
            count=re.search(r'\.WithHitCount\((\d+)\)',tail)
            if count: repeats=int(count[1]); tail=tail.replace(count[0],'')
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
    code=re.sub(r'NCombatRoom\.Instance\?\.CombatVfxContainer\.AddChildSafely\(NGroundFireVfx\.Create\(target\)\);','',code)
    cosmetic_loop=r'foreach \(Creature item in targets\)\s*\{\s*NCombatRoom\.Instance\?\.CombatVfxContainer\.AddChildSafely\(NFireSmokePuffVfx\.Create\(item\)\);\s*\}'
    code=re.sub(cosmetic_loop,'',code)
    code=re.sub(r'await Cmd\.CustomScaledWait\(0\.2f, 0\.3f\);','',code)
    if re.search(r'\b(if|for|foreach|while|switch|return|throw)\b',code):
        return None,'conditional_or_iterative_behavior'
    effects=[]; damage_declared=False; player_declared=False; targets_declared=False; strength_alias=False
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
        if statement=='await CreatureCmd.GainBlock(target, base.DynamicVars.Block, null)' and 'new BlockVar(12m, ValueProp.Unpowered)' in text:
            effects.append(dict(operation='unpowered_block',amount=dict(visible='block'))); continue
        if statement=='await CreatureCmd.GainBlock(target, target.Block * 2, ValueProp.Unpowered, null)':
            effects.append(dict(operation='multiply_block',amount=3)); continue
        if statement=='await PlayerCmd.GainEnergy(base.DynamicVars.Energy.BaseValue, target.Player)':
            effects.append(dict(operation='energy',amount=dict(visible='energy',energy_icons=True))); continue
        if statement=='await CardPileCmd.Draw(choiceContext, base.DynamicVars.Cards.BaseValue, target.Player)':
            effects.append(dict(operation='draw',amount=dict(visible='draw'))); continue
        if statement=='await CreatureCmd.Damage(choiceContext, target, damage.BaseValue, damage.Props, base.Owner.Creature, null, null)' and damage_declared and target[1]=='AnyEnemy':
            effects.append(dict(operation='unpowered_damage',amount=dict(visible='damage'))); continue
        if statement=='await CreatureCmd.Damage(choiceContext, targets, damage.BaseValue, damage.Props, player, null, null)' and damage_declared and targets_declared and target[1]=='AllEnemies':
            effects.append(dict(operation='unpowered_damage',amount=dict(visible='damage'))); continue
        match=re.fullmatch(r'await PowerCmd.Apply<(StrengthPower|VulnerablePower|WeakPower|FlexPotionPower)>\(choiceContext, target, (base.DynamicVars.(?:Strength|Vulnerable|Weak).BaseValue|baseValue), base.Owner.Creature, null\)',statement)
        if match:
            power=match[1]; expression=match[2]
            required={'StrengthPower':('strength','Strength','AnyPlayer'),'FlexPotionPower':('temporary_strength','Strength','AnyPlayer'),
                      'VulnerablePower':('vulnerable','Vulnerable','AnyEnemy'),'WeakPower':('weak_boundary','Weak','AnyEnemy')}[power]
            if target[1]!=required[2] or expression!=f'base.DynamicVars.{required[1]}.BaseValue' and not (power=='FlexPotionPower' and expression=='baseValue' and strength_alias):
                return None,'power_target_or_amount_mismatch'
            effects.append(dict(operation=required[0],amount=dict(visible=required[1].lower()))); continue
        return None,'statement_not_recognized:'+statement[:180]
    if not effects: return None,'no_recognized_effects'
    return dict(effects=effects,target_type=target[1]),None

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
        recognized,reason=(recognize_card(text) if category=='card' else recognize_potion(text) if category=='potion' else (None,'trigger_adapter_required'))
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
