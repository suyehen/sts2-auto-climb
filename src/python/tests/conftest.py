import pytest

@pytest.fixture(autouse=True)
def isolate_live_telemetry(monkeypatch,tmp_path):
    from autoslay.telemetry import decisions
    from autoslay.bridge import client
    monkeypatch.setattr(decisions,'DIRECTORY',tmp_path/'decisions')
    monkeypatch.setattr(client,'_snapshots',{})
