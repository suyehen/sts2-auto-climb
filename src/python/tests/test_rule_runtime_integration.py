"""Real independent .NET Host, without game installation, Bridge or a live game."""
from concurrent.futures import ThreadPoolExecutor
import shutil
import json

import pytest

from autoslay.paths import ROOT
from autoslay.rule_runtime import RuleRuntime, RuleRuntimeError


def test_real_rules_host_runs_from_detached_directory_with_only_owned_assemblies(tmp_path):
    dotnet = ROOT / '.runtime/tools/dotnet/dotnet.exe'
    build = ROOT / 'src/csharp/rules-host/bin/Release/net9.0'
    if not dotnet.is_file() or not (build / 'AutoSlay.RulesHost.dll').is_file():
        pytest.skip('Build independent RulesHost Release and install the local .NET runtime before integration validation')
    detached = tmp_path / 'independent-host'
    shutil.copytree(build, detached)
    assert {p.name for p in detached.glob('*.dll')} == {'AutoSlay.RulesHost.dll', 'AutoSlay.Rules.dll'}
    with RuleRuntime([str(dotnet), str(detached / 'AutoSlay.RulesHost.dll')], cwd=str(detached)) as runtime:
        first = runtime.hello()
        assert first['engine'] == 'independent_csharp'
        assert first['full_support'] is False
        assert first['capabilities'] == ['identity_handshake']
        assert len(first['rules_sha256']) == 64
        assert len(first['host_sha256']) == 64
        assert len(first['parameters_sha256']) == 64 and len(first['parameter_contract_sha256']) == 64
        process_id = runtime._process.pid
        with pytest.raises(RuleRuntimeError) as error:
            runtime.request('simulate', {'world': 'not-hydrated'})
        assert error.value.code == 'operation_not_implemented'
        with pytest.raises(RuleRuntimeError) as error:
            runtime.request('hello', {'effects': []})
        assert error.value.code == 'invalid_request'
        with ThreadPoolExecutor(max_workers=4) as executor:
            values = list(executor.map(lambda _: runtime.hello(), range(8)))
        assert all(value['host_epoch'] == first['host_epoch'] for value in values)
        assert runtime._process.pid == process_id
    assert runtime._process.poll() is not None
    with RuleRuntime([str(dotnet), str(detached / 'AutoSlay.RulesHost.dll')],
            expected_identity={'rules_sha256':'0'*64}) as stale:
        with pytest.raises(RuleRuntimeError) as error:
            stale.hello()
        assert error.value.code == 'rule_identity_mismatch' and stale._process.poll() is not None
    package=json.loads((ROOT/'src/csharp/rules/Generated/rule-parameters.json').read_text('utf-8'))
    package['entries'][0]['value']+=1
    path=detached/'updated-parameters.json'
    path.write_text(json.dumps(package),encoding='utf-8')
    with RuleRuntime([str(dotnet), str(detached / 'AutoSlay.RulesHost.dll'), '--parameters', str(path)]) as updated:
        identity=updated.hello()
        assert identity['rules_sha256']==first['rules_sha256']
        assert identity['parameters_sha256']!=first['parameters_sha256']
        assert identity['parameter_behavior_sha256']==first['parameter_behavior_sha256']
        assert identity['parameter_contract_sha256']==first['parameter_contract_sha256']
