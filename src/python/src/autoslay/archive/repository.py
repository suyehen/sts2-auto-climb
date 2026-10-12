"""Immutable public evidence under YYYY-MM-DD/run_ID; SQLite is a rebuildable index."""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from pathlib import Path
from uuid import uuid4
from autoslay.paths import ROOT

VERSION='run-archive/1.0'
SHANGHAI=timezone(timedelta(hours=8))
RUNS_ROOT=ROOT/'data/runs'

def now(): return datetime.now(timezone.utc).isoformat()
def digest(value): return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False);stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)
def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',value):raise ValueError('Invalid archive identifier')
    return value

def for_legacy(directory=None):
    from autoslay.telemetry import decisions
    base=Path(directory or decisions.DIRECTORY).resolve()
    return Repository(RUNS_ROOT if base== (ROOT/'data/decisions').resolve() else base.parent/(base.name+'-runs'))

class Repository:
    def __init__(self,root=RUNS_ROOT):
        self.root=Path(root).resolve()
        self.index=self.root.parent/'indexes'/((('run-history' if self.root==RUNS_ROOT.resolve() else self.root.name))+'.sqlite')

    @contextmanager
    def db(self):
        self.index.parent.mkdir(parents=True,exist_ok=True)
        con=sqlite3.connect(self.index,timeout=15);con.row_factory=sqlite3.Row
        try:
            con.execute('PRAGMA journal_mode=WAL');con.execute('PRAGMA synchronous=FULL')
            con.executescript('''
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,path TEXT UNIQUE,summary TEXT);
                CREATE TABLE IF NOT EXISTS events (cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                  event_id TEXT UNIQUE,run_id TEXT,sequence INTEGER,kind TEXT,recorded_at TEXT,path TEXT,
                  act_index INTEGER,node_key TEXT,visit_id TEXT,summary TEXT);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id,sequence);
                CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY,run_id TEXT,location TEXT);
                CREATE TABLE IF NOT EXISTS runtime_aliases (epoch TEXT,runtime TEXT,run_id TEXT,
                  PRIMARY KEY(epoch,runtime));
            ''')
            yield con
            con.commit()
        except BaseException:
            con.rollback();raise
        finally: con.close()

    def _folder(self,con,run_id,payload):
        row=con.execute('SELECT path FROM runs WHERE id=?',(run_id,)).fetchone()
        if row:return self.root/row['path']
        clock=(payload.get('snapshot') or {}).get('run_clock') or {}
        stamp=clock.get('start_time_unix')
        started=datetime.fromtimestamp(stamp,timezone.utc).isoformat() if isinstance(stamp,(int,float)) and stamp>0 else payload['recorded_at']
        date=datetime.fromisoformat(started).astimezone(SHANGHAI).date().isoformat()
        scope=(payload.get('location') or {}).get('identity_scope')
        prefix='sessions' if scope=='menu' or not (payload.get('snapshot') or {}).get('state',{}).get('run_id') and scope!='saved_run' else ''
        return self.root/prefix/date/('run_'+run_id)

    def _locate(self,con,payload):
        from autoslay.telemetry.runs import location
        loc=dict(payload.get('location') or location(payload.get('snapshot') or {}))
        req=payload.get('request') or {}; snap=payload.get('snapshot') or {};s=snap.get('state') or {}
        if req.get('request_id') and payload['kind']!='action_submitted':
            parent=con.execute('SELECT run_id,location FROM requests WHERE id=?',(req['request_id'],)).fetchone()
            if parent:return json.loads(parent['location'])
        epoch=snap.get('session_epoch') or req.get('session_epoch'); runtime=s.get('run_id') or req.get('run_id')
        if not s.get('run_id') and (str(runtime or '').startswith('menu_') or (s.get('phase') or '').endswith('menu')):
            loc['identity_scope']='menu'
        if epoch and runtime:
            row=con.execute('SELECT run_id FROM runtime_aliases WHERE epoch=? AND runtime=?',(epoch,runtime)).fetchone()
            if s.get('run_history_id') and loc.get('run_key'):
                con.execute('INSERT OR REPLACE INTO runtime_aliases VALUES (?,?,?)',(epoch,runtime,loc['run_key']))
            elif row:loc['run_key']=row['run_id']
        if not loc.get('run_key'):
            if not epoch:return loc
            loc['run_key']='session_'+digest(epoch)[:24];loc['identity_scope']='menu'
        return loc

    def store(self,payload):
        payload=dict(payload);payload.setdefault('recorded_at',now())
        payload.setdefault('event_id',payload.get('report',{}).get('analysis_id') or uuid4().hex)
        payload.setdefault('kind','decision' if 'report' in payload else 'snapshot')
        identity=identifier(payload['event_id'])
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE')
            old=con.execute('SELECT run_id,sequence FROM events WHERE event_id=?',(identity,)).fetchone()
            if old:return dict(recorded=True,run_id=old['run_id'],sequence=old['sequence'],event_id=identity,duplicate=True)
            loc=self._locate(con,payload);run_id=loc.get('run_key')
            if not run_id:raise ValueError('Evidence has no run or session identity')
            identifier(run_id);payload['location']=loc
            folder=self._folder(con,run_id,payload);folder.mkdir(parents=True,exist_ok=True)
            row=con.execute('SELECT summary FROM runs WHERE id=?',(run_id,)).fetchone()
            meta=json.loads(row['summary']) if row else self._initial(run_id,payload)
            snap=payload.get('snapshot') or {};s=snap.get('state') or {};m=s.get('map') or {}
            act=loc.get('act_index');coord=loc.get('node_key');epoch=snap.get('session_epoch')
            # A resume in the same room is a separate attempt, not a lost overwrite.
            token=[epoch,act,loc.get('generation'),coord]
            if payload['kind'] in {'snapshot','heartbeat'} and token!=meta.get('current_visit_token'):
                meta['visit_count']=meta.get('visit_count',0)+1
                meta['current_visit_token']=token
                meta['current_visit_id']='visit_'+str(meta['visit_count'])
            loc.setdefault('visit_id',meta.get('current_visit_id'))
            req=payload.get('request') or {}
            if payload['kind']=='action_submitted' and req.get('request_id'):
                con.execute('INSERT OR IGNORE INTO requests VALUES (?,?,?)',(req['request_id'],run_id,json.dumps(loc)))
            seq=con.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE run_id=?',(run_id,)).fetchone()[0]
            payload.update(schema_version=VERSION,sequence=seq,archive_id=run_id)
            stored=dict(payload)
            if snap.get('state'):
                # Clock/capture timestamps do not duplicate identical gameplay snapshots.
                state_blob={k:v for k,v in snap.items() if k not in {'run_clock','captured_at'}}
                state_hash=digest(state_blob);state_path=folder/'snapshots'/(state_hash+'.json')
                if not state_path.exists():atomic(state_path,state_blob)
                stored.pop('snapshot',None);stored['snapshot_ref']='snapshots/'+state_hash+'.json'
                if snap.get('run_clock'):stored['run_clock']=snap['run_clock']
                if m.get('nodes') and m.get('act_index')==act:
                    name='generation_'+str(m.get('generation') if m.get('generation') is not None else 'unknown')+'.json'
                    atomic(folder/'maps'/('act_'+str(act).zfill(3))/name,dict(recorded_at=payload['recorded_at'],map=m))
            category='decisions' if payload['kind']=='decision' else 'choices' if payload['kind'] in {'ai_choice','local_choice'} else 'actions' if payload['kind'].startswith('action_') else 'outcomes' if payload['kind'] in {'outcome_observed','local_outcome'} else 'events'
            relative=category+'/'+identity+'.json';atomic(folder/relative,stored)
            summary={k:v for k,v in payload.items() if k not in {'snapshot','report','ai_payload','ai_delivery'}}
            summary['path']=relative
            # Keep computation metrics in full report files, not in each pushed message.
            con.execute('INSERT INTO events(event_id,run_id,sequence,kind,recorded_at,path,act_index,node_key,visit_id,summary) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (identity,run_id,seq,payload['kind'],payload['recorded_at'],str((folder/relative).relative_to(self.root)),act,coord,loc.get('visit_id'),json.dumps(summary,ensure_ascii=False)))
            self._update(meta,payload)
            meta.update(archive_id=run_id,path=folder.relative_to(self.root).as_posix(),schema_version=VERSION)
            atomic(folder/'manifest.json',meta);atomic(folder/'summary.json',meta)
            with (folder/'timeline.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(summary,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
            if payload['kind']=='decision':
                r=payload['report'];provenance=dict(game_version=snap.get('game_version'),game_assembly_sha256=snap.get('game_assembly_sha256'),
                    bridge_version=snap.get('bridge_version'),module_versions=r.get('module_versions'),knowledge=r.get('knowledge_compatibility'))
                atomic(folder/'provenance'/'references.json',provenance)
                policy={k:r.get(k) for k in ('weights','interaction_weights','combat_weights','module_versions')}
                atomic(folder/'provenance'/'policies'/(digest(policy)+'.json'),policy)
            con.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?)',(run_id,str(folder.relative_to(self.root)),json.dumps(meta,ensure_ascii=False)))
            return dict(recorded=True,run_id=run_id,sequence=seq,event_id=identity,path=str(folder))

    def _initial(self,run_id,p):
        snap=p.get('snapshot') or {};s=snap.get('state') or {};c=snap.get('run_clock') or {}
        started=c.get('start_time_unix');loc=p.get('location') or {}
        floor=s.get('floor');from_start=bool(started and isinstance(floor,int) and floor<=1)
        return dict(archive_id=run_id,identity_scope=loc.get('identity_scope'),first_recorded_at=p['recorded_at'],
                    started_at=datetime.fromtimestamp(started,timezone.utc).isoformat() if started else None,
                    date_basis='game_start' if started else 'first_observation',timezone='Asia/Shanghai',
                    completeness='from_start_observed' if from_start else 'partial',status='in_progress',result='unknown',
                    counts={},observed_auto_seconds=0.0,timing_gaps=0,epochs=[],control_modes=[],initial_resources=None)

    def _update(self,meta,p):
        meta['last_recorded_at']=p['recorded_at'];kind=p['kind'];counts=meta['counts'];counts[kind]=counts.get(kind,0)+1
        snap=p.get('snapshot') or {};s=snap.get('state') or {};player=s.get('player') or {};creature=player.get('creature') or {};clock=snap.get('run_clock') or {}
        loc=p.get('location') or {};epoch=snap.get('session_epoch')
        if epoch and epoch not in meta['epochs']:meta['epochs'].append(epoch)
        if player.get('character'):meta['character']=player['character']
        if s.get('ascension') is not None:meta['ascension']=s['ascension']
        if s.get('floor') is not None:meta['max_floor']=max(meta.get('max_floor',0),s['floor'])
        if loc.get('act_index') is not None:meta['max_act_index']=max(meta.get('max_act_index',0),loc['act_index'])
        for name in ('game_version','game_assembly_sha256','bridge_version'):
            if snap.get(name):meta[name]=snap[name]
        if s:
            resources=dict(hp=creature.get('hp'),max_hp=creature.get('max_hp'),gold=player.get('gold'),
                           deck=player.get('deck'),relics=player.get('relics'),potions=player.get('potions'))
            if player and meta.get('initial_resources') is None:meta['initial_resources']=resources
            if player:meta['latest_resources']=resources
            if kind in {'snapshot','heartbeat'}:
                meta['location']=loc;meta['phase']=s.get('phase');meta['control_mode']=s.get('control_mode')
                meta['last_seen_at']=p['recorded_at'];meta['connected']=snap.get('connected',True)
                prev=meta.get('timing_sample');current=dict(at=p['recorded_at'],epoch=epoch,mode=s.get('control_mode'),connected=snap.get('connected',True),game_paused=clock.get('game_paused',False))
                if prev and prev['epoch']==epoch:
                    delta=(datetime.fromisoformat(current['at'])-datetime.fromisoformat(prev['at'])).total_seconds()
                    if 0<=delta<=10 and prev['mode']=='auto' and current['mode']=='auto' and prev['connected'] and current['connected'] and not prev.get('game_paused') and not current.get('game_paused'):
                        meta['observed_auto_seconds']+=delta
                    elif delta>10:meta['timing_gaps']+=1
                meta['timing_sample']=current
        if clock.get('run_time_seconds') is not None:meta['game_time_seconds']=clock['run_time_seconds']
        if clock.get('result') in {'win','loss','abandoned'}:
            meta.update(result=clock['result'],status='ended',result_source='game')
            meta.setdefault('ended_at',p['recorded_at'])
        report=p.get('report') or {};ms=report.get('request_elapsed_ms')
        if isinstance(ms,(int,float)):
            meta['analysis_ms_total']=meta.get('analysis_ms_total',0)+ms
            meta['analysis_count']=meta.get('analysis_count',0)+1
        if report.get('module_versions'):meta['module_versions']=report['module_versions']
        details=p.get('details') or {}
        if kind=='ai_choice':meta['last_choice']=details
        if kind=='local_choice':meta['last_choice']=dict(details,group='local_deterministic',index=None)
        if (p.get('receipt') or {}).get('status') in {'failed','rejected','needs_user_input','unknown'}:
            meta['exception_count']=meta.get('exception_count',0)+1

    def catalog(self,page=1,limit=30,character=None,ascension=None,status=None,date=None,sort='last_recorded_at'):
        if not 1<=page or not 1<=limit<=100:raise ValueError('Invalid pagination')
        if sort not in {'last_recorded_at','first_recorded_at','ascension','max_floor','game_time_seconds'}:raise ValueError('Invalid sorting')
        clauses=["COALESCE(json_extract(summary,'$.identity_scope'),'')!='menu'","replace(path,char(92),'/') NOT LIKE 'sessions/%'"];args=[]
        for field,value in (('character',character),('ascension',ascension),('status',status)):
            if value is not None and value!='':clauses.append("json_extract(summary,'$."+field+"')=?");args.append(value)
        if date:clauses.append("replace(path,char(92),'/') LIKE ?");args.append(date+'/%')
        where=' AND '.join(clauses)
        with self.db() as con:
            total=con.execute('SELECT COUNT(*) FROM runs WHERE '+where,args).fetchone()[0]
            rows=[json.loads(r[0]) for r in con.execute("SELECT summary FROM runs WHERE "+where+" ORDER BY json_extract(summary,'$."+sort+"') DESC LIMIT ? OFFSET ?",args+[limit,(page-1)*limit])]
        result=[]
        for value in rows:
            value=dict(value)
            # List pages never ship entire decks or relic descriptions.
            for k in ('initial_resources','latest_resources'):
                if value.get(k):value[k]={n:v for n,v in value[k].items() if n in {'hp','max_hp','gold'}}
            value['connection_fresh']=bool(value.get('last_seen_at') and (datetime.now(timezone.utc)-datetime.fromisoformat(value['last_seen_at'])).total_seconds()<8)
            result.append(value)
        return dict(records=result,total=total,page=page,limit=limit)

    def folder(self,run_id):
        identifier(run_id)
        with self.db() as con:row=con.execute('SELECT path FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise FileNotFoundError(run_id)
        path=(self.root/row['path']).resolve()
        if not path.is_relative_to(self.root):raise ValueError('Archive escapes root')
        return path

    def manifest(self,run_id):return read(self.folder(run_id)/'manifest.json')

    def load_event(self,event_id):
        identifier(event_id)
        with self.db() as con:row=con.execute('SELECT path FROM events WHERE event_id=?',(event_id,)).fetchone()
        if not row:raise FileNotFoundError(event_id)
        path=(self.root/row['path']).resolve()
        if not path.is_relative_to(self.root):raise ValueError('Record escapes root')
        value=read(path)
        if value.get('snapshot_ref'):
            folder=self.folder(value['archive_id']);source=(folder/value['snapshot_ref']).resolve()
            if not source.is_relative_to(folder):raise ValueError('Snapshot escapes archive')
            value['snapshot']=read(source)
            if value.get('run_clock'):value['snapshot']['run_clock']=value['run_clock']
        return value

    def entries(self,run_id=None,kind=None,after=0,limit=200,act=None,node=None,visit=None,skip_heartbeat=False,descending=False):
        if not 1<=limit<=10000 or after<0:raise ValueError('Invalid pagination')
        clauses=['cursor>?'];args=[after]
        if skip_heartbeat:clauses.append("kind!='heartbeat'")
        for column,value in (('run_id',run_id),('kind',kind),('act_index',act),('node_key',node),('visit_id',visit)):
            if value is not None:clauses.append(column+'=?');args.append(value)
        with self.db() as con:
            data=con.execute('SELECT cursor,summary FROM events WHERE '+' AND '.join(clauses)+' ORDER BY cursor '+('DESC' if descending else 'ASC')+' LIMIT ?',args+[limit]).fetchall()
        return [dict(json.loads(r['summary']),cursor=r['cursor']) for r in data]

    def latest_cursor(self):
        with self.db() as con:return con.execute('SELECT COALESCE(MAX(cursor),0) FROM events').fetchone()[0]

    def tree(self,run_id):
        folder=self.folder(run_id);meta=self.manifest(run_id);acts={}
        for path in (folder/'maps').glob('act_*/*.json'):
            value=read(path);m=value['map'];key=str(m['act_index'])
            act=acts.setdefault(key,dict(act_index=m['act_index'],maps=[],timeline=[]));act['maps'].append(value|dict(generation=m.get('generation')))
        entries=self.entries(run_id,limit=10000,skip_heartbeat=True)
        unlocated=[]
        for e in entries:
            if e['kind']=='heartbeat':continue
            loc=e.get('location') or {};idx=loc.get('act_index')
            if idx is None:unlocated.append(e);continue
            act=acts.setdefault(str(idx),dict(act_index=idx,maps=[],timeline=[]));act['timeline'].append(e)
            if e['kind']=='snapshot' and loc.get('coordinate'):act['current_position']=loc['coordinate']
        for act in acts.values():
            act['maps'].sort(key=lambda m:m['recorded_at'])
            if act['maps']:act['visited']=act['maps'][-1]['map'].get('visited',[])
        from autoslay.archive.summary import detail
        return dict(run_key=run_id,identity_scope=meta.get('identity_scope'),latest_at=meta.get('last_recorded_at'),
                    latest_location=meta.get('location'),acts=acts,unlocated=unlocated,manifest=meta,
                    statistics=detail(self,run_id,entries,meta),timeline_truncated=len(entries)>=10000)

    def rebuild(self):
        """Index recovery from original files. Does not execute or rewrite evidence."""
        issues=[];count=0
        with self.db() as con:
            con.execute('BEGIN IMMEDIATE');con.execute('DELETE FROM events');con.execute('DELETE FROM runs');con.execute('DELETE FROM requests');con.execute('DELETE FROM runtime_aliases')
            # A crash may leave atomic event files before the first manifest was committed.
            folders=list(self.root.glob('*/run_*'))+list(self.root.glob('sessions/*/run_*'))
            for folder in folders:
                if not folder.is_dir() or (folder/'manifest.json').exists():continue
                try:
                    records=[]
                    for category in ('decisions','events','actions','choices','outcomes'):
                        for path in (folder/category).glob('*.json'):
                            p=read(path)
                            if p.get('snapshot_ref'):p['snapshot']=read(folder/p['snapshot_ref'])
                            if p.get('run_clock') and p.get('snapshot'):p['snapshot']['run_clock']=p['run_clock']
                            records.append(p)
                    records.sort(key=lambda p:p['sequence'])
                    if not records:continue
                    meta=self._initial(records[0]['archive_id'],records[0])
                    for p in records:self._update(meta,p)
                    meta.update(schema_version=VERSION,path=folder.relative_to(self.root).as_posix(),recovered_manifest=True,completeness='recovered_partial')
                    atomic(folder/'manifest.json',meta);atomic(folder/'summary.json',meta)
                except (ValueError,OSError,KeyError) as exc:issues.append(dict(path=str(folder),error=str(exc)))
            for manifest in self.root.glob('**/manifest.json'):
                if '.pending' in manifest.parts:continue
                try:
                    meta=read(manifest);run_id=identifier(meta['archive_id']);folder=manifest.parent
                    con.execute('INSERT INTO runs VALUES (?,?,?)',(run_id,str(folder.relative_to(self.root)),json.dumps(meta,ensure_ascii=False)))
                    records=[]
                    for category in ('decisions','events','actions','choices','outcomes'):
                        for path in (folder/category).glob('*.json'):
                            try:records.append((read(path),path))
                            except (ValueError,OSError) as exc:issues.append(dict(path=str(path),error=str(exc)))
                    for payload,path in sorted(records,key=lambda x:x[0].get('sequence',0)):
                        identity=identifier(payload['event_id']);loc=payload.get('location') or {}
                        summary={k:v for k,v in payload.items() if k not in {'report','snapshot_ref','ai_payload','ai_delivery'}}
                        summary['path']=str(path.relative_to(folder))
                        con.execute('INSERT INTO events(event_id,run_id,sequence,kind,recorded_at,path,act_index,node_key,visit_id,summary) VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (identity,run_id,payload['sequence'],payload['kind'],payload['recorded_at'],str(path.relative_to(self.root)),loc.get('act_index'),loc.get('node_key'),loc.get('visit_id'),json.dumps(summary,ensure_ascii=False)))
                        req=payload.get('request') or {}
                        if payload['kind']=='action_submitted' and req.get('request_id'):con.execute('INSERT OR REPLACE INTO requests VALUES (?,?,?)',(req['request_id'],run_id,json.dumps(loc)))
                        count+=1
                    # Preserve runtime aliases even when source clocks were excluded from payload.
                    if meta.get('origin')!='imported':
                        for payload,path in records:
                            if payload.get('snapshot_ref'):
                                snap=read(folder/payload['snapshot_ref']);runtime=(snap.get('state') or {}).get('run_id')
                                if runtime:con.execute('INSERT OR IGNORE INTO runtime_aliases VALUES (?,?,?)',(snap.get('session_epoch'),runtime,run_id))
                except (ValueError,OSError,KeyError,sqlite3.IntegrityError) as exc:issues.append(dict(path=str(manifest),error=str(exc)))
        return dict(indexed_events=count,issues=issues)

    def pending_store(self,payload):
        identity=payload.get('event_id') or (payload.get('report') or {}).get('analysis_id') or uuid4().hex
        atomic(self.root/'.pending'/(identifier(identity)+'.json'),payload)

    def recover_pending(self):
        result=[]
        for path in (self.root/'.pending').glob('*.json'):
            try:
                pointer=self.store(read(path))
                # Recovery copy is retained, not deleted; it is excluded from subsequent scans.
                recovered=path.parent/'recovered';recovered.mkdir(exist_ok=True);path.replace(recovered/path.name)
                result.append(pointer)
            except (OSError,ValueError,sqlite3.Error) as exc:result.append(dict(error=str(exc),file=path.name))
        return result
