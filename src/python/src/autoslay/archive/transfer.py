"""Portable archive import/export. No executable content, overwrites or path escapes."""
import io
import json
import shutil
import zipfile
import hashlib
from pathlib import Path,PurePosixPath
from uuid import uuid4
from autoslay.archive.repository import Repository,VERSION,read,atomic,digest,identifier,now

MAX_PACKAGE=128*1024*1024
MAX_EXPANDED=512*1024*1024
MAX_FILES=20000
ALLOWED={'manifest.json','summary.json','timeline.jsonl','maps','snapshots','decisions','choices','actions','outcomes','events','provenance'}

def export(repo,run_id):
    folder=repo.folder(run_id);out=io.BytesIO()
    hashes={}
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file() and path.suffix in {'.json','.jsonl'}:
                name=path.relative_to(folder).as_posix()
                if name=='provenance/export-integrity.json':continue
                content=path.read_bytes();hashes[name]=hashlib.sha256(content).hexdigest();archive.writestr(name,content)
        archive.writestr('provenance/export-integrity.json',json.dumps(dict(schema_version='archive-integrity/1.0',sha256=hashes)))
    return out.getvalue()

def validate(folder):
    folder=Path(folder).resolve();manifest=read(folder/'manifest.json')
    if manifest.get('schema_version')!=VERSION:raise ValueError('Unsupported archive format')
    identifier(manifest['archive_id']);count=0;size=0
    integrity=folder/'provenance/export-integrity.json'
    if integrity.exists():
        for name,expected in read(integrity)['sha256'].items():
            path=(folder/name).resolve()
            if not path.is_relative_to(folder) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                raise ValueError('Archive file fingerprint mismatch')
    for path in folder.rglob('*'):
        if path.is_symlink() or not path.resolve().is_relative_to(folder):raise ValueError('Archive contains link or escaped path')
        if not path.is_file():continue
        relative=path.relative_to(folder)
        if relative.parts[0] not in ALLOWED or path.suffix not in {'.json','.jsonl'}:raise ValueError('Unsupported archive file')
        count+=1;size+=path.stat().st_size
        if count>MAX_FILES or size>MAX_EXPANDED:raise ValueError('Archive too large')
        if path.suffix=='.json':
            payload=read(path)
            if relative.parts[0] in {'decisions','events','actions','choices','outcomes'}:
                if identifier(payload['event_id'])!=path.stem or payload.get('archive_id')!=manifest['archive_id']:
                    raise ValueError('Event identity does not match archive')
                if type(payload.get('sequence')) is not int or payload['sequence']<1:raise ValueError('Invalid event sequence')
            if relative.parts[0]=='snapshots' and digest(payload)!=path.stem:raise ValueError('Snapshot fingerprint mismatch')
            if payload.get('snapshot_ref'):
                ref=PurePosixPath(payload['snapshot_ref'])
                if ref.is_absolute() or '..' in ref.parts or ref.parts[0]!='snapshots':raise ValueError('Invalid snapshot reference')
                if not (folder/str(ref)).is_file():raise ValueError('Missing referenced snapshot')
    return manifest

def open_folder(repo,source):
    source=Path(source).resolve();meta=validate(source)
    fingerprint=digest([(str(p.relative_to(source)),digest(read(p))) for p in sorted(source.rglob('*.json'))])
    original=meta['archive_id'];archive_id=original+'_import_'+fingerprint[:12]
    try:
        current=repo.folder(original)
        if current==source:return dict(archive_id=original,reused=True)
        if read(current/'manifest.json').get('import_fingerprint')==fingerprint:return dict(archive_id=original,reused=True)
    except FileNotFoundError:pass
    try:
        if repo.folder(archive_id).exists():return dict(archive_id=archive_id,reused=True)
    except FileNotFoundError:pass
    date=meta.get('started_at') or meta['first_recorded_at'];day=date[:10]
    # Keep readable dates; reject untrusted directory components.
    from datetime import datetime
    from autoslay.archive.repository import SHANGHAI
    day=datetime.fromisoformat(date).astimezone(SHANGHAI).date().isoformat()
    destination=repo.root/day/('run_'+identifier(archive_id));temp=repo.root/'.pending'/('import_'+uuid4().hex)
    temp.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(source,temp)
    prefix='import_'+fingerprint[:12]+'_'
    files=[]
    for category in ('decisions','events','actions','choices','outcomes'):
        files.extend((temp/category).glob('*.json'))
    ids={read(path)['event_id']:prefix+digest(read(path)['event_id'])[:32] for path in files}
    rows=[]
    for path in files:
        p=read(path);old=p['event_id'];p['source_event_id']=old;p['event_id']=ids[old];p['archive_id']=archive_id
        if p.get('location'):p['location']['run_key']=archive_id
        if p.get('report') and p['report'].get('analysis_id') in ids:p['report']['analysis_id']=ids[p['report']['analysis_id']]
        if isinstance(p.get('ai_payload'),dict):
            ai=p['ai_payload']
            if ai.get('analysis_id') in ids:ai['analysis_id']=ids[ai['analysis_id']]
            pointer=ai.get('decision_record') or {}
            if pointer.get('record_id') in ids:pointer['record_id']=ids[pointer['record_id']]
        for body in ('request','receipt','details'):
            if isinstance(p.get(body),dict):
                for field in ('analysis_id','decision_id','request_id'):
                    if field in p[body]:p[body][field]=ids.get(p[body][field],prefix+str(p[body][field]))
        # Files live under a namespace even if imported archive has the same original run ID.
        newpath=path.with_name(p['event_id']+'.json');atomic(newpath,p);path.unlink()
        rows.append({k:v for k,v in p.items() if k not in {'report','ai_payload','snapshot_ref'}}|dict(path=str(newpath.relative_to(temp)).replace('\\','/')))
    meta.update(archive_id=archive_id,path=destination.relative_to(repo.root).as_posix(),source_archive_id=original,imported_at=now(),import_fingerprint=fingerprint,origin='imported')
    if meta.get('location'):meta['location']['run_key']=archive_id
    for field in ('analysis_id','decision_id'):
        if (meta.get('last_choice') or {}).get(field) in ids:meta['last_choice'][field]=ids[meta['last_choice'][field]]
    atomic(temp/'manifest.json',meta);atomic(temp/'summary.json',meta)
    (temp/'timeline.jsonl').write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in sorted(rows,key=lambda e:e['sequence'])),encoding='utf-8')
    # Namespaced imported evidence has new hashes. Preserve the source inventory separately.
    integrity=temp/'provenance/export-integrity.json'
    if integrity.exists():integrity.replace(temp/'provenance/source-integrity.json')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise ValueError('Destination already exists; original archives preserved')
    temp.replace(destination)
    report=repo.rebuild()
    return dict(archive_id=archive_id,path=str(destination),indexed=report,origin='imported')

def import_zip(repo,payload):
    if len(payload)>MAX_PACKAGE:raise ValueError('Package too large')
    if not zipfile.is_zipfile(io.BytesIO(payload)):raise ValueError('Invalid archive package')
    staging=repo.root/'.pending'/('package_'+uuid4().hex);staging.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries=archive.infolist()
        if len(entries)>MAX_FILES or sum(e.file_size for e in entries)>MAX_EXPANDED:raise ValueError('Package expands too large')
        seen=set()
        for entry in entries:
            name=entry.filename.replace('\\','/');path=PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or ':' in name or not path.parts or path.parts[0] not in ALLOWED:raise ValueError('Invalid package path')
            if name.lower() in seen:raise ValueError('Duplicate package path')
            seen.add(name.lower())
            if (entry.external_attr>>16)&0o170000==0o120000:raise ValueError('Package contains symbolic link')
            target=staging/str(path)
            if entry.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                if target.suffix not in {'.json','.jsonl'}:raise ValueError('Unsupported package content')
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(entry))
    return open_folder(repo,staging)
