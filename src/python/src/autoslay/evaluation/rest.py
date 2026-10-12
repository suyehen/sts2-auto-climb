"""Rest-site alternatives use visible healing text and actual encyclopedia upgrades."""
import re
from autoslay.evaluation.choices import cards_report
from autoslay.rules.registry import clean

VERSION='1.0.0'

def evaluate_rest(options,deck,context,entries,policy):
    candidates=[]
    for option in options:
        if not option.get('available'): continue
        record=dict(option_id=option.get('option_id'),action_id=option.get('action_id'),utility=None)
        text=clean(option.get('description',''))
        if option.get('option_id','').lower()=='heal':
            match=re.search(r'(?:回复|恢复|Heal\s+)(\d+)(?:点生命|\s+HP)',text,re.I)
            if match:
                healed=min(int(match[1]),context.max_hp-context.hp)
                record.update(utility=healed*policy.hp_weight*context.urgency,healed=healed,basis='visible fixed healing amount')
        elif option.get('option_id','').lower()=='smith':
            upgrades=cards_report(deck,deck,context,entries,policy=policy,mode='upgrade')
            known=[c for c in upgrades['candidates'] if c['delta_utility'] is not None]
            record.update(upgrades=upgrades,basis='best modeled upgrade; requires subsequent selector')
            if known: record['utility']=known[0]['delta_utility']
        if record['utility'] is None: record['reason']='visible option not sufficiently modeled'
        candidates.append(record)
    candidates.sort(key=lambda c:(c['utility'] is not None,c['utility'] or 0),reverse=True)
    return dict(module_version=VERSION,candidates=candidates,execution_authorized=False,
                limitations=['healing and upgrade utility are proxies; unknown rest modifiers need Codex review'])
