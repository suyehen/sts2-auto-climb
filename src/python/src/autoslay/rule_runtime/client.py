"""Bounded JSON Lines client for a private, independent rules Host process."""
import json
import queue
import subprocess
import threading
import uuid
import time
from collections import deque

PROTOCOL='autoslay-rules-host/1'


def _json_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('Duplicate rule response field: '+key)
        value[key]=item
    return value


def _nonfinite(value):
    raise ValueError('Non-finite JSON number: '+value)


class RuleRuntimeError(RuntimeError):
    def __init__(self,code,message):
        super().__init__(message);self.code=code


class RuleRuntime:
    def __init__(self,command,*,timeout=10,cwd=None,expected_identity=None):
        if not isinstance(command,(list,tuple)) or not command or any(not isinstance(p,str) or not p for p in command):
            raise ValueError('Explicit argument vector required')
        if type(timeout) not in (int,float) or not 0<timeout<=300:raise ValueError('Invalid rule runtime timeout')
        identity_fields={'engine','source_version','source_assembly_sha256','rules_sha256','host_sha256','parameters_sha256','parameter_contract_sha256','parameter_behavior_sha256','parameter_source_fingerprint'}
        if expected_identity is not None and (not isinstance(expected_identity,dict) or set(expected_identity)-identity_fields):
            raise ValueError('Unknown expected rule identity fields')
        self.timeout=timeout;self._lock=threading.RLock();self._queue=queue.Queue(maxsize=2);self._epoch=None
        self._identity=None;self._expected_identity=dict(expected_identity or {})
        self._reader_error=None;self._diagnostics=deque(maxlen=32)
        self._process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            encoding='utf-8',errors='strict',bufsize=1,shell=False,cwd=cwd,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self._reader=threading.Thread(target=self._read,daemon=True);self._reader.start()
        self._stderr_reader=threading.Thread(target=self._read_stderr,daemon=True);self._stderr_reader.start()

    @property
    def diagnostics(self):
        """At most 128 KiB of diagnostic characters; never interpreted as protocol or instructions."""
        return ''.join(tuple(self._diagnostics))

    def _read_stderr(self):
        try:
            while chunk:=self._process.stderr.readline(4096):self._diagnostics.append(chunk)
        except (UnicodeError,OSError,ValueError):pass

    def _enqueue(self,value):
        try:self._queue.put_nowait(value)
        except queue.Full:
            self._reader_error=RuleRuntimeError('protocol_error','Unsolicited Host responses exceed the protocol buffer')
            return False
        return True

    def _read(self):
        try:
            while line:=self._process.stdout.readline(4_000_002):
                if len(line)>4_000_000:
                    self._reader_error=RuleRuntimeError('protocol_error','Rule response exceeds protocol limit');return
                if not self._enqueue(line):return
        except (UnicodeError,OSError,ValueError) as exc:self._reader_error=RuleRuntimeError('protocol_error',str(exc))
        finally:self._enqueue(None)

    def request(self,operation,payload=None):
        if not isinstance(operation,str) or not operation:raise ValueError('Operation required')
        if payload is not None and not isinstance(payload,dict):raise ValueError('Object payload required')
        with self._lock:
            if self._process.poll() is not None:raise RuleRuntimeError('host_closed','Independent Host is not running')
            identity=uuid.uuid4().hex
            message=dict(protocol_version=PROTOCOL,request_id=identity,operation=operation)
            if payload is not None:message['payload']=payload
            raw=json.dumps(message,ensure_ascii=False,allow_nan=False)
            if len(raw)>4_000_000:raise ValueError('Rule request exceeds protocol limit')
            try:
                deadline=time.monotonic()+self.timeout
                # Pipe backpressure is part of the same request budget, including a Host that never reads stdin.
                written=threading.Event();write_errors=[]
                def write():
                    try:self._process.stdin.write(raw+'\n');self._process.stdin.flush()
                    except (OSError,ValueError,UnicodeError) as exc:write_errors.append(exc)
                    finally:written.set()
                writer=threading.Thread(target=write,daemon=True);writer.start()
                if not written.wait(max(0,deadline-time.monotonic())):raise queue.Empty
                if write_errors:raise write_errors[0]
                if self._reader_error is not None:raise self._reader_error
                response=self._queue.get(timeout=max(0,deadline-time.monotonic()))
                if self._reader_error is not None:raise self._reader_error
                if isinstance(response,Exception):raise response
                if response is None:raise RuleRuntimeError('host_closed','Independent Host closed stdout')
                response=json.loads(response,object_pairs_hook=_json_object,parse_constant=_nonfinite)
                if not isinstance(response,dict) or response.get('request_id')!=identity or response.get('protocol_version')!=PROTOCOL:
                    raise RuleRuntimeError('protocol_error','Rule response identity mismatch')
                if type(response.get('ok')) is not bool:raise RuleRuntimeError('protocol_error','Rule response status missing')
                if not response['ok']:
                    error=response.get('error') or {}
                    if not isinstance(error,dict) or not isinstance(error.get('code'),str) or not error['code'] or not isinstance(error.get('message',''),str):
                        raise RuleRuntimeError('protocol_error','Rule error response missing code')
                    raise RuleRuntimeError(error.get('code','protocol_error'),error.get('message','Rule operation failed'))
                if 'result' not in response:raise RuleRuntimeError('protocol_error','Rule response result missing')
                return response['result']
            except queue.Empty as exc:
                self.close();raise RuleRuntimeError('host_timeout','Independent Host response timed out') from exc
            except (OSError,ValueError,UnicodeError) as exc:
                self.close();raise RuleRuntimeError('protocol_error',str(exc)) from exc
            except RuleRuntimeError as exc:
                if exc.code in {'protocol_error','host_closed'}:self.close()
                raise

    def hello(self):
        with self._lock:
            value=self.request('hello')
            if not isinstance(value,dict) or not isinstance(value.get('host_epoch'),str) or not value['host_epoch']:
                self.close();raise RuleRuntimeError('protocol_error','Rule Host epoch missing')
            if self._epoch is not None and self._epoch!=value['host_epoch']:
                self.close();raise RuleRuntimeError('host_epoch_changed','World handles cannot survive a Host restart')
            for key,expected in self._expected_identity.items():
                if key not in value or value[key]!=expected:
                    self.close();raise RuleRuntimeError('rule_identity_mismatch','Unexpected independent rule identity: '+key)
            identity={key:value.get(key) for key in ('engine','source_version','source_assembly_sha256','rules_sha256','host_sha256','parameter_schema','parameters_sha256','parameter_contract_sha256','parameter_behavior_sha256','parameter_source_fingerprint')}
            if self._identity is not None and identity!=self._identity:
                self.close();raise RuleRuntimeError('rule_identity_mismatch','Rule identity changed within the same Host epoch')
            self._identity=identity;self._epoch=value['host_epoch'];return value

    def close(self):
        with self._lock:
            if self._process.poll() is None:
                self._process.terminate()
                try:self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:self._process.kill();self._process.wait(timeout=2)
            for stream in (self._process.stdin,self._process.stdout,self._process.stderr):
                try:stream.close()
                except (OSError,ValueError):pass

    def __enter__(self):return self
    def __exit__(self,*_):self.close()
