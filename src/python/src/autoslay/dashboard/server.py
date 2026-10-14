"""Loopback observation and explicit Agent controls; credentials remain local."""
import json
import logging
import re
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import httpx
from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import scoped, read_json, dataset_path, asset_path
from autoslay.knowledge_base.service import SERVICE as knowledge_service
from autoslay.bridge.client import load_config
from autoslay.telemetry import decisions
from autoslay.telemetry import runs
from autoslay.decision.unified import analyze_decision
from autoslay.knowledge_base.rulebook import Rulebook
from autoslay.archive.repository import for_legacy
from autoslay.archive.views import live as live_view
from autoslay.archive import transfer

PORT=18767
ASSETS=ROOT/'src/web/public'

def resident(route,body=None):
    token=Path(load_config().token_file).read_text().strip()
    with httpx.Client(trust_env=False,timeout=100) as client:
        response=client.post('http://127.0.0.1:18766'+route,json=body or {},headers={'Authorization':'Bearer '+token})
        response.raise_for_status(); return response.json()

def agent_request(route,body=None):
    token=Path(load_config().token_file).read_text().strip()
    with httpx.Client(trust_env=False,timeout=22,follow_redirects=False) as client:
        response=client.post('http://127.0.0.1:18769'+route,json=body or {},headers={'Authorization':'Bearer '+token})
        data=response.json()
        return data,response.status_code


@scoped
def catalog_page(query):
    if query.get('category',[''])[0] in {'event','monster','intent'}:
        from autoslay.knowledge_base.behavior import query as behavior_query
        data=behavior_query(query=query.get('q',[''])[0],category=query['category'][0],limit=int(query.get('limit',['48'])[0]),page=int(query.get('page',['1'])[0]))
        manifest_path=dataset_path('monster_portraits')
        portraits=read_json(manifest_path, default={})
        for entry in data['entries']:
            entry['classification']=dict(scope_label={'event':'事件','monster':'怪物','intent':'意图'}[entry['category']],rarity_label={'reference_only':'参考资料','source_extracted_unreviewed':'模型待核验','reviewed_existing_rule':'已有专用规则'}[entry['calculation_status']],source='local_game')
            portrait=(portraits.get('entries') or {}).get(entry['entity_id'])
            if portrait and portraits.get('game_assembly_sha256')==data['game_assembly_sha256']:
                entry['portrait_url']='/assets/monsters/'+portrait['file']
                entry['portrait_kind']=portrait['kind']
        data['facets']=dict(scopes=[],rarities=[],card_types=[]);return data
    data=read_json(dataset_path('catalog'))
    from autoslay.knowledge_base.browse import query_catalog
    return query_catalog(data,q=query.get('q',[''])[0],category=query.get('category',[''])[0],
        owner=query.get('owner',[''])[0],rarity=query.get('rarity',[''])[0],card_type=query.get('card_type',[''])[0],
        include_shared=query.get('include_shared',['0'])[0]=='1',page=int(query.get('page',['1'])[0]),
        limit=int(query.get('limit',['100'])[0]))


@scoped
def coverage_report():
    from autoslay.decision.mechanisms import inventory as mechanism_inventory
    from autoslay.knowledge_base.installation import version_status
    inventory=read_json(dataset_path('source_inventory'))
    audit_path=dataset_path('source_audit')
    source_audit=read_json(audit_path, default={}) or None
    return dict(rules=Rulebook().audit(),mechanisms=mechanism_inventory(),source_inventory=inventory,source_audit=source_audit,installation=version_status())

def make_server(port=PORT,directory=None):
    repository=for_legacy(directory)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self,data,status=200,kind='application/json; charset=utf-8',attachment=False):
            payload=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(status); self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(payload))); self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; connect-src 'self' http://127.0.0.1:18770; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            if attachment: self.send_header('Content-Disposition','attachment; filename="'+('archive.zip' if kind=='application/zip' else 'decision.json')+'"')
            self.end_headers(); self.wfile.write(payload)

        def handle_request(self):
            parsed=urlsplit(self.path); route=parsed.path; query=parse_qs(parsed.query)
            expected=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host')!=expected: self.send(dict(error='INVALID_HOST'),403); return
            if route.startswith('/api/'):
                origin=self.headers.get('Origin')
                if self.headers.get('X-Autoslay-ReadOnly')!='1' or origin and origin!='http://'+expected:
                    self.send(dict(error='INVALID_ORIGIN'),403); return
            try:
                if self.command=='GET' and route.startswith('/assets/'):
                    match=re.fullmatch(r'/assets/(cards|potions|relics|monsters|map)/((CARD|POTION|RELIC|MONSTER|MAP)\.[A-Z0-9_]+\.webp)',route)
                    if not match or {'cards':'CARD','potions':'POTION','relics':'RELIC','monsters':'MONSTER','map':'MAP'}[match[1]]!=match[3]:raise ValueError('Invalid image')
                    self.send(asset_path(match[1],match[2]).read_bytes(),kind='image/webp');return
                if self.command=='GET' and route in {'/','/index.html','/style.css','/sidebar.html','/sidebar.js','/sidebar.css','/sidebar-combat.js','/shared.js','/agent.js','/services.js','/workspace.js','/presentation.js','/map.js','/history-model.js','/workbench.css','/library.js','/event-model.js','/event-forecast.js','/combat-forecast.js','/configuration.js','/agent-state.js','/play.js','/play-state.js'}:
                    name='index.html' if route=='/' else route[1:]
                    kind={'html':'text/html','js':'text/javascript','css':'text/css'}[name.rsplit('.',1)[1]]+'; charset=utf-8'
                    self.send((ASSETS/name).read_bytes(),kind=kind); return
                if self.command=='GET' and route=='/api/catalog':
                    self.send(catalog_page(query));return
                if self.command=='GET' and route=='/api/knowledge/status':
                    self.send(knowledge_service.status());return
                if self.command=='GET' and route=='/api/knowledge/search':
                    self.send(knowledge_service.search(query.get('q',[''])[0],
                        category=query.get('category',[None])[0],dataset=query.get('dataset',[None])[0],
                        game_version=query.get('game_version',[None])[0],assembly_hash=query.get('assembly_hash',[None])[0],
                        language=query.get('language',[None])[0],limit=int(query.get('limit',['10'])[0]),
                        include_related=query.get('include_related',['0'])[0]=='1'));return
                if self.command=='GET' and route=='/api/agent/status':
                    data,code=agent_request('/status');self.send(data,code);return
                if self.command=='GET' and route=='/api/play/state':
                    data,code=agent_request('/play/state',{'request_id':query.get('request_id',[None])[0]});self.send(data,code);return
                if self.command=='POST' and route=='/api/play/start':
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=30000 or self.headers.get('Transfer-Encoding'):raise ValueError('Invalid body')
                    body=json.loads(self.rfile.read(length))
                    if not isinstance(body,dict):raise ValueError('Invalid body')
                    data,code=agent_request('/play/start',body);self.send(data,code);return
                if self.command=='POST' and route in {'/api/config/export','/api/config/preview','/api/config/import','/api/config/backups','/api/config/restore'}:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=250000 or self.headers.get('Transfer-Encoding'):raise ValueError('Invalid config size')
                    body=json.loads(self.rfile.read(length))
                    if not isinstance(body,dict):raise ValueError('Invalid config body')
                    data,code=agent_request(route.removeprefix('/api'),body);self.send(data,code);return
                if self.command=='POST' and route in {'/api/agent/switch','/api/agent/profile','/api/agent/models','/api/agent/start','/api/agent/pause','/api/agent/stop'}:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<=length<=30000 or self.headers.get('Transfer-Encoding'):raise ValueError('Invalid body')
                    body=json.loads(self.rfile.read(length) or b'{}')
                    if not isinstance(body,dict):raise ValueError('Invalid body')
                    data,code=agent_request(route.removeprefix('/api/agent'),body);self.send(data,code);return
                if self.command=='GET' and route=='/api/archives':
                    self.send(repository.catalog(page=int(query.get('page',['1'])[0]),limit=int(query.get('limit',['30'])[0]),
                        character=query.get('character',[None])[0],ascension=int(query['ascension'][0]) if query.get('ascension') else None,
                        status=query.get('status',[None])[0],date=query.get('date',[None])[0],sort=query.get('sort',['last_recorded_at'])[0]));return
                if self.command=='GET' and route=='/api/archive':
                    self.send(repository.tree(query.get('id',[''])[0]));return
                if self.command=='GET' and route=='/api/archive/timeline':
                    values=repository.entries(query.get('id',[''])[0],after=int(query.get('after',['0'])[0]),
                        limit=int(query.get('limit',['100'])[0]),act=int(query['act'][0]) if query.get('act') else None,
                        node=query.get('node',[None])[0],visit=query.get('visit',[None])[0],skip_heartbeat=True)
                    self.send(dict(events=values,next_cursor=values[-1]['cursor'] if values else None));return
                if self.command=='GET' and route=='/api/live':
                    self.send(live_view(repository));return
                if self.command=='GET' and route=='/api/archive/cursor':
                    self.send(dict(cursor=repository.latest_cursor()));return
                if self.command=='GET' and route=='/api/archive/export':
                    data=transfer.export(repository,query.get('id',[''])[0]);self.send(data,kind='application/zip',attachment=True);return
                if self.command=='GET' and route=='/api/stream':
                    cursor=int(query.get('after',['0'])[0])
                    if cursor<0:raise ValueError('Invalid event cursor')
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-cache');self.end_headers()
                    deadline=time.monotonic()+25
                    try:
                        while time.monotonic()<deadline:
                            events=repository.entries(after=cursor,limit=100)
                            if events:
                                cursor=events[-1]['cursor']
                                self.wfile.write(('event: archive\ndata: '+json.dumps(dict(cursor=cursor,events=events),ensure_ascii=False)+'\n\n').encode('utf-8'))
                            else:self.wfile.write(b': keepalive\n\n')
                            self.wfile.flush();time.sleep(.5)
                    except (BrokenPipeError,ConnectionResetError,OSError):pass
                    return
                if self.command=='POST' and route=='/api/archive/import':
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=transfer.MAX_PACKAGE or self.headers.get('Transfer-Encoding'):raise ValueError('Invalid archive size')
                    self.send(transfer.import_zip(repository,self.rfile.read(size)));return
                if self.command=='POST' and route=='/api/archive/open-folder':
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<10000 or self.headers.get('Transfer-Encoding'):raise ValueError('Invalid folder request')
                    body=json.loads(self.rfile.read(size));self.send(transfer.open_folder(repository,body['path']));return
                if self.command=='GET' and route=='/api/history':
                    self.send(dict(records=decisions.list_records(directory))); return
                if self.command=='GET' and route=='/api/runs':
                    self.send(dict(runs=runs.tree(directory))); return
                if self.command=='GET' and route=='/api/event':
                    self.send(runs.load_event(query.get('id',[''])[0],directory)); return
                if self.command=='GET' and route=='/api/record':
                    self.send(decisions.load(query.get('id',[''])[0],query.get('origin',['live'])[0],directory),
                              attachment=query.get('download')==['1']); return
                if self.command=='GET' and route=='/api/coverage':
                    self.send(coverage_report());return
                if self.command=='GET' and route=='/api/evidence':
                    identity=decisions.safe_id(query.get('id',[''])[0]); evidence=dict(choice=None,observations=[],plans=[])
                    path=ROOT/'data/calibration/choices'/(identity+'.json')
                    if path.exists(): evidence['choice']=json.loads(path.read_text(encoding='utf-8-sig'))
                    for p in (ROOT/'data/calibration/observations').glob('*.json'):
                        value=json.loads(p.read_text(encoding='utf-8-sig'))
                        if value.get('decision_id')==identity:evidence['observations'].append(value)
                    try:
                        decision=repository.load_event(identity)
                        entries=repository.entries(decision['archive_id'],limit=10000,skip_heartbeat=True)
                        from autoslay.telemetry.effects import archived as execution_facts
                        evidence['executed_effects']=execution_facts(repository,decision,entries)
                        for e in entries:
                            detail=e.get('details') or {}
                            if detail.get('analysis_id')==identity or detail.get('decision_id')==identity:
                                if e['kind'] in {'ai_choice','local_choice'}:evidence['choice']=detail
                                if e['kind'] in {'outcome_observed','local_outcome','agent_outcome'} and detail not in evidence['observations']:evidence['observations'].append(detail)
                            if e['kind']=='action_plan':
                                plan=detail.get('plan') or {};source=decision['report'].get('source') or {}
                                if all(plan.get(a)==source.get(b) for a,b in [('session_epoch','session_epoch'),('run_id','run_id'),('expected_state_version','state_version')]):evidence['plans'].append(detail)
                    except FileNotFoundError:pass
                    # Plans are matched by source identity, not guessed from chronological proximity.
                    if evidence['choice']:
                        source=evidence['choice'].get('source') or {}
                        for p in (ROOT/'.runtime/logs/plans').glob('*.json'):
                            data=json.loads(p.read_text(encoding='utf-8-sig')); plan=data.get('plan') or {}
                            if plan.get('session_epoch')==source.get('session_epoch') and plan.get('expected_state_version')==source.get('state_version') and plan.get('run_id')==source.get('run_id'):
                                evidence['plans'].append(data)
                    evidence['note']='Intent is separate from execution; no record means not recorded, not no action'
                    self.send(evidence); return
                if self.command=='GET' and route=='/api/replays':
                    self.send(dict(files=replay_files())); return
                if self.command=='GET' and route=='/api/state': self.send(resident('/state')); return
                if self.command=='POST' and route in {'/api/analyze','/api/replay'}:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<=length<=10000 or self.headers.get('Transfer-Encoding'): raise ValueError('Invalid body')
                    body=json.loads(self.rfile.read(length) or b'{}')
                    if route=='/api/analyze':
                        report=resident('/decision',body)
                        if report.get('error') or 'analysis_id' not in report: self.send(report); return
                        self.send(decisions.load(report['analysis_id'],'live',directory)); return
                    name=body.get('file','')
                    if Path(name).name!=name or name not in replay_files():
                        raise ValueError('Select an existing replay')
                    snapshot=json.loads((ROOT/'data/replays'/name).read_text(encoding='utf-8-sig'))
                    report=analyze_decision(snapshot,use_cache=False)
                    record=decisions.archive(report,snapshot,'replay',directory)
                    self.send(decisions.load(record['record_id'],'replay',directory)); return
                self.send(dict(error='UNKNOWN_ROUTE'),404)
            except (ValueError,KeyError,TypeError): self.send(dict(error='INVALID_REQUEST'),400)
            except FileNotFoundError: self.send(dict(error='NOT_FOUND'),404)
            except httpx.HTTPError: self.send(dict(error='RESIDENT_UNAVAILABLE'),503)
            except Exception as exc:
                logging.exception('Decision panel request failed: %s',route)
                self.send(dict(error='PANEL_ERROR',type=type(exc).__name__),500)
        do_GET=handle_request
        do_POST=handle_request
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

def replay_files():
    files=[]
    for path in sorted((ROOT/'data/replays').glob('*.json')):
        try:
            data=json.loads(path.read_text(encoding='utf-8-sig'))
            if isinstance(data,dict) and data.get('schema_version')=='1.0' and isinstance(data.get('state'),dict) and data.get('game_version'):
                files.append(path.name)
        except (OSError,ValueError): continue
    return files

def serve(port=PORT,open_browser=False):
    server=make_server(port)
    print(f'Local: http://127.0.0.1:{server.server_port}',flush=True)
    if open_browser:
        try:
            import os
            url=f'http://127.0.0.1:{server.server_port}/'
            if os.name=='nt': os.startfile(url)
            else:
                import webbrowser
                webbrowser.open(url)
        except OSError:
            logging.exception('Default browser could not be opened; dashboard remains available')
    try: server.serve_forever()
    finally: server.server_close()
