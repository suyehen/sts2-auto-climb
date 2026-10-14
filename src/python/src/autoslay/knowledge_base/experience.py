"""Validate and explicitly publish reviewed experience; never infer game rules."""
from datetime import datetime, timezone
import json
from math import isfinite
import os
import shutil
import tempfile

from autoslay.execution.lock import dispatch_lock
from autoslay.knowledge_base.repository import REPOSITORY


def validate(data, dataset):
    if dataset not in {'strategy', 'timing'} or data.get('schema_version') != f'knowledge-{dataset}/1.0':
        raise ValueError('Unsupported experience schema')
    if not isinstance(data.get('entries'), list):
        raise ValueError('Experience entries required')
    seen = set()
    for entry in data['entries']:
        if not isinstance(entry, dict):
            raise ValueError('Experience entry must be an object')
        identity = entry.get('id')
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError('Unique experience ID required')
        seen.add(identity)
        if entry.get('review_status') not in {'pending_review', 'reviewed'}:
            raise ValueError('Experience review status required')
        for field in ('title', 'game_version'):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise ValueError('Experience field required: ' + field)
        sources = entry.get('source_refs')
        if not isinstance(sources, list) or not sources or any(not isinstance(s, str) or not s for s in sources):
            raise ValueError('Experience evidence references required')
        if entry['review_status'] == 'reviewed':
            if not isinstance(entry.get('reviewed_by'), str) or not entry['reviewed_by']:
                raise ValueError('Reviewer required')
            try:
                reviewed_at = datetime.fromisoformat(entry['reviewed_at'])
                if reviewed_at.tzinfo is None:
                    raise ValueError('Review timestamp must include timezone')
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError('Valid review timestamp required') from exc
        if dataset == 'strategy':
            if not isinstance(entry.get('summary'), str) or not entry['summary']:
                raise ValueError('Strategy summary required')
            if not isinstance(entry.get('applicable_conditions'), dict):
                raise ValueError('Strategy applicable conditions required')
        else:
            if not isinstance(entry.get('environment'), dict) or not entry['environment']:
                raise ValueError('Timing environment required')
            if not isinstance(entry.get('measurement_scope'), str) or not entry['measurement_scope']:
                raise ValueError('Timing measurement scope required')
            if entry.get('unit') != 'ms' or type(entry.get('samples')) is not int or entry['samples'] < 1:
                raise ValueError('Timing unit ms and positive sample count required')
            for field in ('p50_ms', 'p95_ms'):
                value = entry.get(field)
                if type(value) not in (int, float) or not isfinite(value) or value < 0:
                    raise ValueError('Finite nonnegative timing percentile required')
            if entry['p95_ms'] < entry['p50_ms']:
                raise ValueError('Timing p95 cannot be below p50')
            if not isinstance(entry.get('sample_window'), dict) or not all(
                    isinstance(entry['sample_window'].get(k), str) and entry['sample_window'][k] for k in ('start', 'end')):
                raise ValueError('Timing sample window required')
            try:
                start = datetime.fromisoformat(entry['sample_window']['start'])
                end = datetime.fromisoformat(entry['sample_window']['end'])
                if end < start:
                    raise ValueError('Timing sample window reversed')
            except (TypeError, ValueError) as exc:
                raise ValueError('Valid ordered timing sample window required') from exc


def publish(data, dataset, repository=REPOSITORY):
    """Explicit replacement of a reviewed collection, with atomic save/history."""
    validate(data, dataset)
    if any(e['review_status'] != 'reviewed' for e in data['entries']):
        raise ValueError('Pending experience must remain in staging')
    path = repository.dataset_path(dataset)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    with dispatch_lock(path.parent / '.experience-lock') as acquired:
        if not acquired:
            raise OSError('Experience update busy; previous collection preserved')
        if path.exists() and path.read_bytes() == encoded:
            return dict(changed=False, entries=len(data['entries']), path=str(path))
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                history = path.parent / 'history'
                history.mkdir(exist_ok=True)
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
                shutil.copy2(path, history / (stamp + '.json'))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return dict(changed=True, entries=len(data['entries']), path=str(path),
                rules_enabled=False)
