import pytest

@pytest.fixture(autouse=True)
def isolate_live_telemetry(monkeypatch,tmp_path):
    from autoslay.telemetry import decisions
    from autoslay.bridge import client
    from autoslay.agent import runtime,launch
    from autoslay.execution import deterministic
    monkeypatch.setattr(decisions,'DIRECTORY',tmp_path/'decisions')
    monkeypatch.setattr(client,'_snapshots',{})
    # Simulated Agents must not contend with a real service restart or write
    # real dispatch journals. Knowledge/config modules keep their own roots.
    for module in (runtime,launch,deterministic):
        monkeypatch.setattr(module,'ROOT',tmp_path)
