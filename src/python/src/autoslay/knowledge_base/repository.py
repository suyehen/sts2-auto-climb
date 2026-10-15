"""One process-local JSON cache and content-addressed decision snapshots.

Only this layer reads knowledge JSON. Cached payloads never escape without a
copy. Context-local snapshots pin all registered files, including absent files,
so an update cannot change the knowledge seen halfway through a decision.
"""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
import hashlib
import inspect
import json
import re
from pathlib import Path
from threading import RLock
from types import MappingProxyType

from autoslay.paths import ROOT

KNOWLEDGE_ROOT = ROOT / 'data/knowledge'
_active = ContextVar('autoslay_knowledge_snapshot', default=None)


class FrozenDict(dict):
    def _deny(self, *args, **kwargs):
        raise TypeError('Knowledge snapshot is read-only')
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _deny

    def __deepcopy__(self, memo):
        return {deepcopy(k, memo): deepcopy(v, memo) for k, v in self.items()}


class FrozenList(list):
    def _deny(self, *args, **kwargs):
        raise TypeError('Knowledge snapshot is read-only')
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _deny

    def __deepcopy__(self, memo):
        return [deepcopy(v, memo) for v in self]


def freeze(value):
    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, dict):
        return FrozenDict({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return FrozenList(freeze(v) for v in value)
    if isinstance(value, tuple):
        return tuple(freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class Document:
    path: Path
    stamp: tuple
    sha256: str
    data: dict


@dataclass(frozen=True)
class KnowledgeSnapshot:
    revision: str
    documents: object
    datasets: object

    def read(self, path, default=None):
        key = str(Path(path).resolve())
        if key not in self.documents:
            raise ValueError('Knowledge path not registered in this snapshot: ' + key)
        document = self.documents[key]
        if document is None:
            if default is None:
                raise FileNotFoundError(key)
            return deepcopy(default)
        return deepcopy(document.data)


class Repository:
    def __init__(self, root=KNOWLEDGE_ROOT, capacity=64, snapshot_capacity=8):
        if any(type(n) is not int or n < 1 for n in (capacity, snapshot_capacity)):
            raise ValueError('Positive knowledge cache capacities required')
        self.root = Path(root).resolve()
        self.capacity = capacity
        self.snapshot_capacity = snapshot_capacity
        self._lock = RLock()
        self._documents = OrderedDict()
        self._snapshots = OrderedDict()
        self._views = OrderedDict()
        self.parses = 0
        self.hits = 0

    @staticmethod
    def _stamp(path):
        try:
            st = path.stat()
            return st.st_mtime_ns, st.st_ctime_ns, st.st_size
        except FileNotFoundError:
            return None

    def _document(self, path):
        path = Path(path).resolve()
        key = str(path)
        with self._lock:
            for _ in range(3):
                stamp = self._stamp(path)
                if stamp is None:
                    self._documents.pop(key, None)
                    return None
                cached = self._documents.get(key)
                if cached is not None and cached.stamp == stamp:
                    self.hits += 1
                    self._documents.move_to_end(key)
                    return cached
                raw = path.read_bytes()
                if self._stamp(path) != stamp:
                    continue
                data = json.loads(raw.decode('utf-8-sig'))
                if not isinstance(data, dict):
                    raise ValueError('Knowledge document must be a JSON object: ' + key)
                document = Document(path, stamp, hashlib.sha256(raw).hexdigest(), freeze(data))
                self.parses += 1
                self._documents[key] = document
                self._documents.move_to_end(key)
                while len(self._documents) > self.capacity:
                    self._documents.popitem(last=False)
                return document
            raise OSError('Knowledge file changed during read: ' + key)

    def read(self, path, default=None):
        document = self._document(path)
        if document is None:
            if default is None:
                raise FileNotFoundError(str(path))
            return deepcopy(default)
        return deepcopy(document.data)

    def view(self, document, kind, build):
        """Cache validated projections/fingerprints in the same repository."""
        if document is None:
            raise FileNotFoundError('Knowledge document missing')
        key = document.sha256, kind
        with self._lock:
            if key not in self._views:
                self._views[key] = freeze(build(document.data))
            self._views.move_to_end(key)
            while len(self._views) > self.capacity:
                self._views.popitem(last=False)
            return deepcopy(self._views[key])

    def dataset_path(self, name):
        manifest = self.read(self.root / 'manifest.json')
        return self._paths(manifest)[name]

    def _paths(self, manifest):
        if manifest.get('schema_version') != 'knowledge-manifest/1.0':
            raise ValueError('Unsupported knowledge manifest')
        values = manifest.get('datasets')
        if not isinstance(values, dict):
            raise ValueError('Knowledge datasets required')
        result = {}
        for name, relative in values.items():
            if not isinstance(relative, str) or Path(relative).is_absolute():
                raise ValueError('Relative knowledge dataset path required')
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError('Knowledge dataset escapes root')
            result[name] = path
        return result

    def acquire_snapshot(self, revision=None):
        with self._lock:
            if revision is not None:
                snapshot = self._snapshots.get(revision)
                if snapshot is None:
                    raise ValueError('Knowledge snapshot no longer retained: ' + revision)
                self._snapshots.move_to_end(revision)
                return snapshot
            manifest_path = self.root / 'manifest.json'
            for _ in range(3):
                manifest = self._document(manifest_path)
                if manifest is None:
                    raise FileNotFoundError(str(manifest_path))
                paths = self._paths(manifest.data)
                documents = {str(manifest_path): manifest}
                documents.update({str(p): self._document(p) for p in paths.values()})
                if any(self._stamp(Path(p)) != (doc.stamp if doc else None)
                       for p, doc in documents.items()):
                    continue
                hashes = {p: doc.sha256 if doc else None for p, doc in documents.items()}
                revision = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
                snapshot = self._snapshots.get(revision)
                if snapshot is None:
                    snapshot = KnowledgeSnapshot(revision, MappingProxyType(documents), MappingProxyType(paths))
                    self._snapshots[revision] = snapshot
                self._snapshots.move_to_end(revision)
                while len(self._snapshots) > self.snapshot_capacity:
                    self._snapshots.popitem(last=False)
                return snapshot
            raise OSError('Knowledge datasets changed during snapshot acquisition')

    def status(self):
        with self._lock:
            return dict(cached_documents=len(self._documents), retained_snapshots=len(self._snapshots),
                        parses=self.parses, cache_hits=self.hits, sharing_scope='current_process')


REPOSITORY = Repository()


def read_json(path, default=None, *, fresh=False):
    snapshot = _active.get()
    key = str(Path(path).resolve())
    if not fresh and snapshot is not None and key in snapshot.documents:
        return snapshot.read(path, default)
    return REPOSITORY.read(path, default)


def dataset_path(name):
    snapshot = _active.get()
    return snapshot.datasets[name] if snapshot else REPOSITORY.dataset_path(name)


def read_view(path, kind, build):
    snapshot = _active.get()
    key = str(Path(path).resolve())
    document = snapshot.documents[key] if snapshot is not None and key in snapshot.documents else REPOSITORY._document(path)
    return REPOSITORY.view(document, kind, build)


def document_sha256(path):
    """Fingerprint the same pinned document used by read_json/read_view."""
    snapshot = _active.get()
    key = str(Path(path).resolve())
    document = snapshot.documents[key] if snapshot is not None and key in snapshot.documents else REPOSITORY._document(path)
    if document is None:
        raise FileNotFoundError(str(path))
    return document.sha256


def current_snapshot():
    return _active.get()


def asset_path(group, filename):
    prefixes = {'cards': 'CARD', 'potions': 'POTION', 'relics': 'RELIC', 'monsters': 'MONSTER', 'map': 'MAP'}
    if group not in prefixes or not isinstance(filename, str) or not re.fullmatch(
            re.escape(prefixes[group]) + r'\.[A-Z0-9_]+\.webp', filename):
        raise ValueError('Invalid knowledge asset')
    return REPOSITORY.root / 'assets' / group / filename


@contextmanager
def knowledge_scope(snapshot=None, revision=None):
    selected = snapshot or _active.get()
    if selected is not None and revision and selected.revision != revision:
        raise ValueError('Conflicting knowledge snapshot revisions')
    selected = selected or REPOSITORY.acquire_snapshot(revision)
    token = _active.set(selected)
    try:
        yield selected
    finally:
        _active.reset(token)


def scoped(function=None, *, report_argument=None):
    """Pin legacy nested callers without threading new arguments through rules."""
    if function is None:
        return lambda selected: scoped(selected, report_argument=report_argument)
    signature = inspect.signature(function) if report_argument else None
    @wraps(function)
    def wrapped(*args, **kwargs):
        revision = None
        if report_argument:
            report = signature.bind(*args, **kwargs).arguments.get(report_argument)
            revision = (report or {}).get('knowledge_revision')
        with knowledge_scope(revision=revision) as snapshot:
            result = function(*args, **kwargs)
            if isinstance(result, dict):
                result['knowledge_revision'] = snapshot.revision
            return result
    return wrapped
