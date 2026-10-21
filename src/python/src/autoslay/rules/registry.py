"""Native mechanic definitions shared by live simulation and planning.

Entity definitions are loaded from versioned JSON and compose simulator operations.
Descriptions and source evidence never determine execution.
"""
import re
from dataclasses import dataclass,replace
from autoslay.decision.contracts import Card, CombatState, UnsupportedEffect

VERSION = "3.0.0"
GAME_VERSION = "0.111.0"


@dataclass(frozen=True)
class Effect:
    kind: str
    amount: int
    repeats: int = 1
    generation: tuple[str,str,int | str] | None = None


@dataclass(frozen=True)
class CardRule:
    effects: tuple[Effect, ...]
    exhaust: bool = False
    playable: bool = True
    dynamic: bool = False
    canonical_values: bool = False


@dataclass(frozen=True)
class PotionRule:
    effects: tuple[Effect, ...]
    target_type: str


def clean(text: str) -> str:
    return re.sub(r"\[.*?\]", "", text)


@dataclass(frozen=True)
class EffectSpec:
    operation: str
    amount: int | dict
    repeats: int | dict = 1
    generation: tuple[str, str, int | str] | None = None

    def __post_init__(self):
        if self.operation not in OPERATIONS:
            raise ValueError('Mechanic operation is not implemented: ' + self.operation)
        if isinstance(self.amount, dict):
            from autoslay.rules.amounts import validate_expression
            validate_expression(self.amount)
        elif type(self.amount) is not int or not 0 <= self.amount <= 1000000000:
            raise ValueError('Invalid mechanic amount')
        if isinstance(self.repeats, dict):
            from autoslay.rules.amounts import validate_expression
            validate_expression(self.repeats)
            if self.operation not in {'damage','osty_damage','summon'}:
                raise ValueError('Variable repeats require attack damage')
        elif type(self.repeats) is not int or not 1 <= self.repeats <= 100:
            raise ValueError('Invalid mechanic repeats')
        if self.operation not in {'damage','dismantle','unpowered_damage','osty_damage','summon'} and self.repeats != 1:
            raise ValueError('Resource/status operations execute once')
        if self.operation == 'generate_card':
            if (not isinstance(self.generation,tuple) or len(self.generation)!=3
                    or not re.fullmatch(r'CARD\.[A-Z0-9_]+',self.generation[0])
                    or self.generation[1] not in {'hand','discard','draw'}
                    or not (self.generation[2]=='source' or type(self.generation[2]) is int and 0<=self.generation[2]<=10)):
                raise ValueError('Invalid generated card definition')
        elif self.generation is not None:
            raise ValueError('Generated card descriptor on a different mechanic')


@dataclass(frozen=True)
class Trigger:
    event: str
    effects: tuple[EffectSpec, ...]
    turn: int | None = None
    counter: dict | None = None


@dataclass(frozen=True)
class EntityMechanics:
    category: str
    triggers: tuple[Trigger, ...] = ()
    exhaust: bool = False
    unplayable: bool = False
    target_type: str | None = None
    periodic: tuple[int, int, int] | None = None
    passive: str | None = None
    state_fields: tuple[tuple[str, int | bool], ...] = ()
    x_resource: str | None = None

    def __post_init__(self):
        for field,value in self.state_fields:
            if field in {'permanent_draw_blocked','retain_hand_relic','retain_energy'}:
                if type(value) is not bool:raise ValueError('Boolean passive state required')
            elif field in {'hp_loss_reduction','turn_hp_loss_cap','extra_hand_draw',
                           'discard_block','exhaust_damage','shuffle_block','osty_attack_block'}:
                if type(value) is not int or value<0:raise ValueError('Nonnegative passive amount required')
            else:raise ValueError('Passive state field is not implemented: '+field)
        allowed=(PHASES|REACTIVE_EVENTS if self.category=='relic' else
                 {'on_play','on_draw','on_exhaust','turn_end_in_hand','on_discard'} if self.category=='card' else {'on_use'} if self.category=='potion' else set())
        if not allowed or any(t.event not in allowed for t in self.triggers):
            raise ValueError('Undefined mechanic category or event')
        if self.category in {'card','potion'}:
            activation='on_play' if self.category=='card' else 'on_use'
            if sum(t.event==activation for t in self.triggers)!=1:
                raise ValueError('Exactly one activation required')
        for trigger in self.triggers:
            if trigger.event in PHASES and any(e.operation not in PHASE_OPERATIONS for e in trigger.effects):
                raise ValueError('Phase operation is not implemented')
            if trigger.event in REACTIVE_EVENTS and (trigger.counter is None or any(
                    e.operation not in {'energy','unpowered_block','unpowered_damage','strength','dexterity'} for e in trigger.effects)):
                raise ValueError('Counter mechanic is not implemented')
            if trigger.event=='on_draw' and any(e.operation!='lose_energy' for e in trigger.effects):
                raise ValueError('Draw mechanic is not implemented')

    def effects(self, event):
        return tuple(e for t in self.triggers if t.event == event for e in t.effects)


PHASES = frozenset({'after_block_cleared', 'side_turn_start', 'after_hand_draw', 'side_turn_end','after_energy_reset_late'})
REACTIVE_EVENTS = frozenset({'after_card_played'})
ENEMY_OPERATIONS = frozenset({'damage', 'dismantle', 'unpowered_damage', 'vulnerable',
    'double_vulnerable', 'enemy_strength', 'weak', 'weak_boundary', 'poison'})
ATOMIC_OPERATIONS = frozenset({'energy', 'lose_energy', 'multiply_energy', 'stars',
    'unpowered_block', 'unpowered_damage', 'heal', 'heal_percent', 'gain_max_hp','summon','heal_osty'})
PHASE_OPERATIONS=ATOMIC_OPERATIONS | {'strength','dexterity'}
IMPLEMENTATION_SHA256 = "0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9"
OPERATIONS = frozenset({'damage','dismantle','block','draw','energy','strength','temporary_strength',
    'vulnerable','lose_hp','enemy_strength','colossus','double_vulnerable','weak_boundary',
    'random_exhaust_boundary','upgrade_boundary','exhaust_nonattacks','unpowered_damage',
    'unpowered_block','multiply_block','stars','heal','heal_percent','gain_max_hp','dexterity',
    'temporary_dexterity','weak','no_draw','multiply_energy','poison','regeneration','plating',
    'next_turn_block','next_turn_energy','next_turn_stars','next_turn_draw','ritual',
    'block_and_next_turn','generate_card','lose_energy'})
VISIBLE_FIELDS = frozenset({'damage','block','draw','energy','strength','vulnerable','lose_hp','weak'})
STACK_POWERS = {
    'vigor':'POWER.VIGOR_POWER', 'intangible':'POWER.INTANGIBLE_POWER',
    'buffer':'POWER.BUFFER_POWER', 'thorns':'POWER.THORNS_POWER', 'artifact':'POWER.ARTIFACT_POWER',
    'accuracy':'POWER.ACCURACY_POWER', 'afterimage':'POWER.AFTERIMAGE_POWER',
    'barricade':'POWER.BARRICADE_POWER', 'blur':'POWER.BLUR_POWER',
    'rage':'POWER.RAGE_POWER', 'envenom':'POWER.ENVENOM_POWER',
    'demon_form':'POWER.DEMON_FORM_POWER', 'pyre':'POWER.PYRE_POWER',
    'well_laid_plans':'POWER.WELL_LAID_PLANS_POWER', 'feel_no_pain':'POWER.FEEL_NO_PAIN_POWER',
    'accelerant':'POWER.ACCELERANT_POWER', 'cruelty':'POWER.CRUELTY_POWER',
    'the_sealed_throne':'POWER.THE_SEALED_THRONE_POWER',
    'retain_hand':'POWER.RETAIN_HAND_POWER', 'fan_of_knives':'POWER.FAN_OF_KNIVES_POWER',
    'demise':'POWER.DEMISE_POWER', 'doom':'POWER.DOOM_POWER',
    'flame_barrier':'POWER.FLAME_BARRIER_POWER', 'anticipate':'POWER.ANTICIPATE_POWER',
    'feeding_frenzy':'POWER.FEEDING_FRENZY_POWER',
    'dark_shackles':'POWER.DARK_SHACKLES_POWER', 'piercing_wail':'POWER.PIERCING_WAIL_POWER',
    'shackling_potion':'POWER.SHACKLING_POTION_POWER',
    'crush_under':'POWER.CRUSH_UNDER_POWER','dying_star':'POWER.DYING_STAR_POWER',
    'enfeebling_touch':'POWER.ENFEEBLING_TOUCH_POWER',
    'clarity':'POWER.CLARITY_POWER','radiance':'POWER.RADIANCE_POWER',
    'gigantification':'POWER.GIGANTIFICATION_POWER',
    'calcify':'POWER.CALCIFY_POWER',
    'spirit_of_ash':'POWER.SPIRIT_OF_ASH_POWER','devour_life':'POWER.DEVOUR_LIFE_POWER',
    'setup_strike':'POWER.SETUP_STRIKE_POWER','flex_potion':'POWER.FLEX_POTION_POWER',
    'speed_potion':'POWER.SPEED_POTION_POWER',
}
TEMPORARY_ENEMY_STRENGTH = frozenset({'dark_shackles','piercing_wail','shackling_potion','crush_under','dying_star','enfeebling_touch'})
TEMPORARY_PLAYER_STATS={'anticipate':'dexterity','feeding_frenzy':'strength',
    'setup_strike':'strength','flex_potion':'strength','speed_potion':'dexterity'}
OPERATIONS = OPERATIONS | STACK_POWERS.keys() | {'enemy_hp_loss','remove_enemy_block','remove_enemy_artifact',
    'enemy_strength_loss','temporary_enemy_strength_loss','player_damage','exhaust_hand',
    'discard_hand','draw_discarded','lose_max_hp','player_weak','player_frail','resolve_doom','shuffle_hand_and_discard',
    'osty_damage','summon','heal_osty'}
ENEMY_OPERATIONS = ENEMY_OPERATIONS | {'doom','demise','enemy_hp_loss','remove_enemy_block',
    'remove_enemy_artifact','enemy_strength_loss','temporary_enemy_strength_loss'} | TEMPORARY_ENEMY_STRENGTH


# The only entity definitions are the models dataset, shared by every caller.
from autoslay.rules.model_store import ModelRegistry
ENTITY_MECHANICS = ModelRegistry()
ENEMY_OPERATIONS=ENEMY_OPERATIONS|{'osty_damage'}


def definition(identity, category=None):
    result = ENTITY_MECHANICS.get(identity)
    if result is None:
        raise UnsupportedEffect('Mechanic definition missing: ' + str(identity))
    if category is not None and result.category != category:
        raise UnsupportedEffect('Mechanic category mismatch: ' + str(identity))
    return result


def mechanism_status(identity, expected_version=None):
    if expected_version and expected_version != GAME_VERSION:
        return 'game_version_mismatch'
    if identity and identity.startswith('POWER.'):
        from autoslay.rules.statuses import PLAYER_POWER_IDS, ENEMY_POWER_IDS,PET_POWER_IDS
        return 'defined' if identity in PLAYER_POWER_IDS|ENEMY_POWER_IDS|PET_POWER_IDS else 'missing_mechanic'
    if identity and identity.startswith('EVENT.'):
        from autoslay.rules.model_store import load_models
        return 'defined' if identity in load_models().flows else 'missing_mechanic'
    if identity and identity.startswith('MONSTER.'):
        from autoslay.rules.model_store import load_models
        return 'defined' if identity in load_models().monsters else 'missing_mechanic'
    if identity and identity.startswith('SELECTION.'):
        from autoslay.rules.model_store import load_models
        return 'defined' if identity in load_models().selections else 'missing_mechanic'
    return 'defined' if identity in ENTITY_MECHANICS else 'missing_mechanic'


def modifiers_supported(card, card_type=None):
    """Sharp adds damage to powered attacks; support depends on the mechanic."""
    if card.get('affliction_id'):
        return False
    enchantment=card.get('enchantment_id')
    if not enchantment:
        return True
    return (enchantment=='ENCHANTMENT.SHARP' and (card.get('type') or card_type)=='Attack'
            and type(card.get('enchantment_amount')) is int and card['enchantment_amount']>=0)


def audit(entity_ids=None):
    from autoslay.knowledge_base.templates import index
    from autoslay.rules.model_store import load_models
    models=load_models();definitions=models.definitions
    references, _ = index()
    identities = references if entity_ids is None else sorted(set(entity_ids))
    return dict(module_version=VERSION, game_version=GAME_VERSION,
        fingerprint=models.fingerprint,
        entries={i:'defined' if i in definitions else 'missing_mechanic' for i in identities},
        entry_scope='complete_catalog' if entity_ids is None else 'current_decision_entities_only',
        counts={c: dict(reference=sum(e['category']==c for e in references.values()),
                        defined=sum(e['category']==c and i in definitions for i,e in references.items()))
                for c in ('card','potion','relic')},
        definition_is_not_full_simulation_coverage=True, full_support=False,
        missing_mechanic_count=sum(i not in definitions for i in references))


class KnowledgeRules:
    version = VERSION

    def __init__(self, definitions=None, references=None):
        from autoslay.rules.model_store import load_models
        from autoslay.knowledge_base.repository import knowledge_scope
        with knowledge_scope():
            self.model_set = load_models() if definitions is None else None
            if references is None and definitions is None:
                from autoslay.knowledge_base.templates import index
                references=index()[0]
        self.definitions = self.model_set.definitions if definitions is None else definitions
        self.fingerprint = self.model_set.fingerprint if self.model_set else None
        self._references = references

    @property
    def references(self):
        if self._references is None:
            from autoslay.knowledge_base.templates import index
            self._references = index()[0]
        return self._references

    def definition(self, identity, category):
        result = self.definitions.get(identity)
        if result is None or result.category != category:
            raise UnsupportedEffect('Mechanic definition missing: ' + str(identity))
        return result

    def effects(self, specs, description, state=None, variables=None):
        effects = []
        for item in specs:
            value = item.amount
            if isinstance(value, dict):
                from autoslay.rules.amounts import evaluate_expression
                value_amount=evaluate_expression(value,variables,state,self.variable)
            else:
                value_amount = value
            repeats=item.repeats
            if isinstance(repeats,dict):
                from autoslay.rules.amounts import evaluate_expression
                repeats=evaluate_expression(repeats,variables,state,self.variable)
            if not 0<=repeats<=100:raise UnsupportedEffect('Unsupported effect repeat count')
            effects.append(Effect(item.operation, value_amount, repeats, item.generation))
        return tuple(effects)

    @staticmethod
    def variable(variables,name):
        matches=[v for v in variables or [] if v.get('name')==name]
        if len(matches)!=1:raise UnsupportedEffect('Dynamic variable must match exactly once: '+name)
        value=matches[0].get('base_value')
        if type(value) not in (int,float) or not 0<=value<=1000000000 or int(value)!=value:
            raise UnsupportedEffect('Nonnegative integral dynamic variable required: '+name)
        return int(value)

    def card(self, card: Card, state: CombatState) -> CardRule:
        if card.affliction_id:raise UnsupportedEffect('Affliction mechanic missing: '+card.affliction_id)
        if card.enchantment_id and card.enchantment_id!='ENCHANTMENT.SHARP':
            raise UnsupportedEffect('Enchantment mechanic missing: '+card.enchantment_id)
        try:
            rule = self.definition(card.entity_id, 'card')
            if (card.costs_x and rule.x_resource!='energy' or card.costs_star_x and rule.x_resource!='stars'):
                raise UnsupportedEffect('Variable-cost mechanic missing')
            if card.target_type not in {'AnyEnemy','AllEnemies'} and rule.target_type!='AllEnemies' and any(
                    e.operation in ENEMY_OPERATIONS for e in rule.effects('on_play')):
                raise UnsupportedEffect('Enemy effects require an enemy-targeting card')
            entry=self.references[card.entity_id]
            variants=[v for v in entry.get('variants',[]) if v['upgrade_level']==card.upgrade_level]
            if len(variants)!=1:raise UnsupportedEffect('Unique card variant required')
            if not modifiers_supported(dict(type=card.card_type,affliction_id=card.affliction_id,
                    enchantment_id=card.enchantment_id,enchantment_amount=card.enchantment_amount),variants[0].get('type')):
                raise UnsupportedEffect('Card modifier mechanic missing or invalid')
            variables=([dict(name=name,base_value=value) for name,value in card.dynamic_variables]
                       if card.dynamic_variables else variants[0].get('dynamic_variables'))
            keywords=card.keywords if card.keywords is not None else variants[0].get('keywords',[])
            from autoslay.rules.amounts import has_state
            active = rule.effects('on_play')
            if any(e.operation in {'summon','heal_osty','osty_damage'} for e in active) and not state.pets_known:
                raise UnsupportedEffect('Card requires observed pets')
            dynamic = any(has_state(e.amount) or has_state(e.repeats) for e in active)
            canonical_values=True
            effects=self.effects(active,card.description,state,variables)
            if canonical_values:
                adjusted=[]
                for effect in effects:
                    value=effect.amount
                    if effect.kind in {'damage','dismantle'}:
                        if card.enchantment_id=='ENCHANTMENT.SHARP':value+=card.enchantment_amount or 0
                        value+=state.base_strength
                    elif effect.kind=='osty_damage' and card.enchantment_id=='ENCHANTMENT.SHARP':value+=card.enchantment_amount or 0
                    elif effect.kind in {'block','block_and_next_turn','exhaust_nonattacks'}:
                        value=value+state.base_dexterity
                    adjusted.append(replace(effect,amount=value))
                effects=tuple(adjusted)
            return CardRule(effects,
                            'Exhaust' in keywords if 'keywords' in variants[0] or card.keywords is not None else rule.exhaust,
                            not (rule.unplayable or 'Unplayable' in keywords),dynamic,canonical_values)
        except (ValueError, KeyError) as exc:
            raise UnsupportedEffect(str(exc)) from exc


    def potion(self, potion):
        try:
            rule = self.definition(potion.entity_id, 'potion')
            variables=([dict(name=name,base_value=value) for name,value in potion.dynamic_variables]
                       if potion.dynamic_variables else self.references[potion.entity_id].get('dynamic_variables'))
            effects = self.effects(rule.effects('on_use'), potion.description,variables=variables)
            target = rule.target_type or self.references[potion.entity_id].get('target_type','AnyPlayer')
            if target not in {'AnyPlayer', 'AnyEnemy', 'AllEnemies', 'Self'}:
                raise UnsupportedEffect('Potion targeting mechanic missing')
            if target not in {'AnyEnemy','AllEnemies'} and any(e.kind in ENEMY_OPERATIONS for e in effects):
                raise UnsupportedEffect('Enemy effects require an enemy-targeting potion')
            return PotionRule(effects, target)
        except (ValueError, KeyError) as exc:
            raise UnsupportedEffect(str(exc)) from exc

    def lifecycle(self,card,state,event):
        try:
            rule=self.definition(card.entity_id,'card')
            variants=[v for v in self.references[card.entity_id]['variants'] if v['upgrade_level']==card.upgrade_level]
            if len(variants)!=1:raise UnsupportedEffect('Unique card lifecycle variant required')
            variables=([dict(name=name,base_value=value) for name,value in card.dynamic_variables]
                       if card.dynamic_variables else variants[0].get('dynamic_variables'))
            return self.effects(rule.effects(event),card.description,state,variables)
        except (ValueError,KeyError) as exc:raise UnsupportedEffect(str(exc)) from exc
