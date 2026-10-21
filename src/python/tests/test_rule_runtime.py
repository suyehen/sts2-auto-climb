import json
import sys
import pytest
from autoslay.paths import ROOT
from autoslay.rule_runtime import RuleRuntime,RuleRuntimeError


def program(tmp_path,body):
    path=tmp_path/'host.py';path.write_text(body,encoding='utf-8')
    return [sys.executable,'-u',str(path)]


def test_private_host_preserves_identity_and_reports_missing_operation_without_fallback(tmp_path):
    command=program(tmp_path,"""import sys,json
for line in sys.stdin:
 r=json.loads(line);out=dict(request_id=r['request_id'],protocol_version=r['protocol_version'])
 if r['operation']=='hello':out.update(ok=True,result=dict(host_epoch='one',full_support=False))
 else:out.update(ok=False,error=dict(code='operation_not_implemented',message='not migrated'))
 print(json.dumps(out),flush=True)
""")
    with RuleRuntime(command) as runtime:
        assert not runtime.hello()['full_support']
        with pytest.raises(RuleRuntimeError) as error:runtime.request('simulate')
        assert error.value.code=='operation_not_implemented'
        assert runtime.hello()['host_epoch']=='one'


@pytest.mark.parametrize('response',["{}","not json",'{"ok":true,"ok":false}', '{"result":NaN}'])
def test_wrong_response_identity_or_malformed_data_closes_host(tmp_path,response):
    command=program(tmp_path,'import sys\nfor line in sys.stdin: print('+repr(response)+',flush=True)\n')
    with RuleRuntime(command) as runtime:
        with pytest.raises(RuleRuntimeError) as error:runtime.hello()
        assert error.value.code=='protocol_error' and runtime._process.poll() is not None


def test_private_host_timeout_reaps_only_its_own_process(tmp_path):
    command=program(tmp_path,'import sys,time\nfor line in sys.stdin: time.sleep(3)\n')
    with RuleRuntime(command,timeout=.05) as runtime:
        with pytest.raises(RuleRuntimeError) as error:runtime.hello()
        assert error.value.code=='host_timeout' and runtime._process.poll() is not None


def test_rule_host_schema_has_no_effect_program_fields():
    from jsonschema import Draft202012Validator
    value=json.loads((ROOT/'contracts/rules-host.schema.json').read_text('utf-8'))
    Draft202012Validator.check_schema(value)
    checker=Draft202012Validator(value)
    checker.validate(dict(protocol_version='autoslay-rules-host/1',request_id='one',operation='hello'))
    assert list(checker.iter_errors(dict(protocol_version='autoslay-rules-host/1',request_id='one',operation='hello',effects=[])))


def test_host_that_does_not_read_requests_is_stopped_within_request_budget(tmp_path):
    command=program(tmp_path,'import time\ntime.sleep(30)\n')
    with RuleRuntime(command,timeout=.1) as runtime:
        with pytest.raises(RuleRuntimeError) as error:runtime.request('hello',{'large':'x'*1_000_000})
        assert error.value.code=='host_timeout' and runtime._process.poll() is not None


def test_stderr_is_drained_and_bounded_without_becoming_protocol(tmp_path):
    command=program(tmp_path,"""import sys,json
for line in sys.stdin:
 sys.stderr.write('diagnostic '*50000+'\\n');sys.stderr.flush()
 r=json.loads(line)
 print(json.dumps(dict(request_id=r['request_id'],protocol_version=r['protocol_version'],ok=True,result=dict(host_epoch='one'))),flush=True)
""")
    with RuleRuntime(command) as runtime:
        assert runtime.hello()['host_epoch']=='one'
        assert 'diagnostic' in runtime.diagnostics and len(runtime.diagnostics)<=32*4096


def test_parameter_contract_rejects_effect_programs_and_fractional_integer_counts():
    from jsonschema import Draft202012Validator
    schema=json.loads((ROOT/'contracts/rule-parameters.schema.json').read_text('utf-8'))
    Draft202012Validator.check_schema(schema);checker=Draft202012Validator(schema)
    package=dict(schema_version='autoslay-rule-parameters/1',source_fingerprint='a'*64,
        behavior_sha256='b'*64,contract_sha256='c'*64,entries=[dict(key='v',kind='int32',value=2,source_path='Sample.cs',source_sha256='d'*64,line=1)])
    checker.validate(package)
    package['entries'][0]['value']=2.5
    assert list(checker.iter_errors(package))
    package['entries'][0]['value']=2;package['effects']=[]
    assert list(checker.iter_errors(package))


def test_identity_change_inside_one_host_epoch_is_rejected(tmp_path):
    command=program(tmp_path,"""import sys,json
count=0
for line in sys.stdin:
 count+=1;r=json.loads(line)
 print(json.dumps(dict(request_id=r['request_id'],protocol_version=r['protocol_version'],ok=True,
  result=dict(host_epoch='one',rules_sha256=str(count)))),flush=True)
""")
    with RuleRuntime(command) as runtime:
        runtime.hello()
        with pytest.raises(RuleRuntimeError) as error:runtime.hello()
        assert error.value.code=='rule_identity_mismatch' and runtime._process.poll() is not None
