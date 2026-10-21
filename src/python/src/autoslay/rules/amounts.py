"""Small integer expression language. No Python/C# execution or arbitrary fields."""
from autoslay.decision.contracts import UnsupportedEffect

STATE_FIELDS={'block','hp','max_hp','energy','stars','hand_count','enemy_count','draw_count','discard_count',
              'exhaust_count','cards_played','attacks_played','skills_played','cards_exhausted',
              'turn_cards_exhausted','turn_cards_drawn','x_value','star_x_value','strength',
              'last_discard_count','last_exhaust_count','combat_cards_played','skill_hand_count','turn','osty_hp','osty_max_hp'}
HISTORY_FIELDS={'cards_played','attacks_played','skills_played','cards_exhausted',
                'turn_cards_exhausted','turn_cards_drawn','combat_cards_played'}
ARITHMETIC={'add','multiply','subtract','floor_divide','min','max','less_than','at_least'}


def validate_expression(value,depth=0):
    if depth>8:raise ValueError('Amount expression nesting exceeds limit')
    if type(value) is int:
        if not 0<=value<=1000000000:raise ValueError('Amount constant out of range')
        return
    if not isinstance(value,dict) or len(value)!=1:raise ValueError('Invalid numeric expression')
    key,argument=next(iter(value.items()))
    if key=='variable':
        if not isinstance(argument,str) or not argument.isascii() or not argument.isidentifier():raise ValueError('Invalid variable identifier')
    elif key=='state':
        if not isinstance(argument,str) or argument not in STATE_FIELDS:raise ValueError('Unsupported amount state field')
    elif key in ARITHMETIC:
        if not isinstance(argument,list) or not 2<=len(argument)<=8 or key in {'subtract','floor_divide','less_than','at_least'} and len(argument)!=2:
            raise ValueError('Invalid arithmetic operands')
        for item in argument:validate_expression(item,depth+1)
    else:raise ValueError('Unsupported numeric expression')


def has_state(value):
    if not isinstance(value,dict):return False
    return 'state' in value or any(has_state(v) for a in value.values() if isinstance(a,list) for v in a)


def evaluate_expression(value,variables,state,variable_reader):
    validate_expression(value)
    def read(node):
        if type(node) is int:return node
        key,argument=next(iter(node.items()))
        if key=='variable':return variable_reader(variables,argument)
        if key=='state':
            if state is None:raise UnsupportedEffect('Amount requires current state')
            if argument in HISTORY_FIELDS and not state.history_known:raise UnsupportedEffect('Amount requires observed combat history: '+argument)
            if argument in {'osty_hp','osty_max_hp'}:
                if not state.pets_known:raise UnsupportedEffect('Amount requires observed pets')
                return (state.osty.hp if argument=='osty_hp' else state.osty.max_hp) if state.osty and state.osty.hp>0 else 0
            if argument=='hand_count':return len(state.hand)
            if argument=='skill_hand_count':
                if any(c.card_type is None for c in state.hand):raise UnsupportedEffect('Hand card types required')
                return sum(c.card_type=='Skill' for c in state.hand)
            if argument=='enemy_count':return sum(e.hp>0 for e in state.enemies)
            if argument=='draw_count':return sum(n for _,n in state.draw_composition)
            if argument=='exhaust_count':return sum(n for _,n in state.exhausted_composition)
            if argument=='strength':return state.base_strength+state.strength_delta
            if argument=='discard_count':
                if not state.discard_known:raise UnsupportedEffect('Amount requires known discard')
                return sum(n for _,n in state.discard_composition)
            result=getattr(state,argument)
            if type(result) is not int:raise UnsupportedEffect('Amount state field is not integral')
            return result
        values=[read(v) for v in argument]
        if key=='add':result=sum(values)
        elif key=='multiply':
            result=1
            for v in values:result*=v
        elif key=='subtract':result=values[0]-values[1]
        elif key=='floor_divide':
            if values[1]==0:raise UnsupportedEffect('Amount division by zero')
            result=values[0]//values[1]
        elif key=='min':result=min(values)
        elif key=='less_than':result=int(values[0]<values[1])
        elif key=='at_least':result=int(values[0]>=values[1])
        else:result=max(values)
        if abs(result)>10**18:raise UnsupportedEffect('Amount arithmetic exceeds bound')
        return result
    result=read(value)
    if not 0<=result<=1000000000:raise UnsupportedEffect('Computed amount out of supported range')
    return result
