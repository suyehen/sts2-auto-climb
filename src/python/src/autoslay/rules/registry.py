"""Amounts come from visible text; never infer an unsupported card from its name."""
import re
from dataclasses import dataclass
from autoslay.decision.contracts import Card, CombatState, UnsupportedEffect

VERSION = "1.1.1"
GAME_VERSION = "0.111.0"


@dataclass(frozen=True)
class Effect:
    kind: str
    amount: int
    repeats: int = 1


@dataclass(frozen=True)
class CardRule:
    effects: tuple[Effect, ...]
    exhaust: bool = False


@dataclass(frozen=True)
class PotionRule:
    effects: tuple[Effect, ...]
    target_type: str


def clean(text: str) -> str:
    return re.sub(r"\[.*?\]", "", text)


def amount(text: str, kind: str) -> int:
    patterns = {
        "damage": r"(?:造成|Deal\s+)(\d+)(?:点伤害|\s+damage)",
        "block": r"(?:获得|Gain\s+)(\d+)(?:点格挡|\s+Block)",
        "draw": r"(?:抽|Draw\s+)(\d+)(?:张牌|\s+cards?)",
        "vulnerable": r"(?:给予|Apply\s+)(\d+)(?:层易伤|\s+Vulnerable)",
        "strength": r"(?:获得|Gain\s+)(\d+)(?:点力量|\s+Strength)",
        "lose_hp": r"(?:失去|Lose\s+)(\d+)(?:点生命|\s+HP)",
        "weak": r"(?:给予|Apply\s+)(\d+)(?:层虚弱|\s+Weak)",
        "energy": r"(?:获得|Gain\s+)(\d+)(?:点能量|\s+Energy)",
    }
    match = re.search(patterns[kind], text, re.IGNORECASE)
    if not match:
        raise UnsupportedEffect("Cannot read visible " + kind)
    return int(match[1])


class KnowledgeRules:
    version = VERSION

    def __init__(self, rulebook=None):
        from autoslay.knowledge.rulebook import Rulebook
        self.rulebook = rulebook or Rulebook()

    def effects(self, rule, description, state=None):
        text = clean(description)
        effects = []
        for item in rule.get("effects", []):
            value = item["amount"]
            if isinstance(value, dict):
                icons = re.findall(r"\[img\]res://images/packed/sprite_fonts/[a-z_]*energy_icon\.png\[/img\]", description) if value.get("energy_icons") else []
                value_amount = len(icons) if icons else amount(text, value["visible"])
                if value.get("invert_legion") and state and state.legion_ready:
                    if value_amount % 2:
                        raise UnsupportedEffect("Cannot invert visible Legion block safely")
                    value_amount //= 2
            else:
                value_amount = value
            effects.append(Effect(item["operation"], value_amount, item.get("repeats", 1)))
        return tuple(effects)

    def card(self, card: Card, state: CombatState) -> CardRule:
        if card.costs_x or card.costs_star_x:
            raise UnsupportedEffect('Star-X cost requires a dedicated adapter' if card.costs_star_x
                                    else 'X-cost cards require a dedicated adapter')
        try:
            rule = self.rulebook.require(card.entity_id, "card")
            if rule.get("unplayable"):
                raise UnsupportedEffect("Unplayable card; observe end-turn behavior")
            return CardRule(self.effects(rule, card.description, state), rule.get("exhaust", False))
        except (ValueError, KeyError) as exc:
            raise UnsupportedEffect(str(exc)) from exc


    def potion(self, potion):
        try:
            rule = self.rulebook.require(potion.entity_id, "potion")
            effects = self.effects(rule, potion.description)
            target = rule.get('target_type', 'AnyPlayer')
            if target not in {'AnyPlayer', 'AnyEnemy', 'AllEnemies', 'Self'}:
                raise UnsupportedEffect('Potion targeting requires adapter')
            return PotionRule(effects, target)
        except (ValueError, KeyError) as exc:
            raise UnsupportedEffect(str(exc)) from exc


# Retained import compatibility; execution is driven by the shared knowledge rulebook.
IroncladRules = KnowledgeRules
