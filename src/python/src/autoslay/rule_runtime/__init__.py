"""Independent C# rule runtime protocol; no live-game transport or old-rule fallback."""
from autoslay.rule_runtime.client import RuleRuntime, RuleRuntimeError

__all__ = ['RuleRuntime', 'RuleRuntimeError']
