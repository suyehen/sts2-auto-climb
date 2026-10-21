"""Exact outcome comparison shared by native mechanic execution.

Adapters must supply every persistent dimension and the same continuation. This
module never turns arbitrary API-supplied forecasts into execution permission.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class Outcome:
    continuation: str
    values: tuple[tuple[str,int],...]
    complete: bool = False
    deterministic: bool = False

def compare(left,right,directions):
    """equal / dominates / dominated / tradeoff / unknown; no weighted scores."""
    if not left.complete or not right.complete or not left.deterministic or not right.deterministic:
        return 'unknown'
    if not isinstance(left.continuation,str) or not left.continuation or not left.values or not right.values: return 'unknown'
    if left.continuation!=right.continuation: return 'unknown'
    a,b=dict(left.values),dict(right.values)
    if len(a)!=len(left.values) or len(b)!=len(right.values): return 'unknown'
    if a.keys()!=b.keys() or a.keys()!=directions.keys(): return 'unknown'
    if any(type(v) is not int for v in (*a.values(),*b.values())): return 'unknown'
    if any(type(d) is not int or d not in {-1,1} for d in directions.values()): return 'unknown'
    delta=[(a[k]-b[k])*directions[k] for k in a]
    if all(d==0 for d in delta): return 'equal'
    if all(d>=0 for d in delta): return 'dominates'
    if all(d<=0 for d in delta): return 'dominated'
    return 'tradeoff'
