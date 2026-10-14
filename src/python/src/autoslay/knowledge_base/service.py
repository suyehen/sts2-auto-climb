"""Offline typed knowledge access, Chinese-aware FTS5 and explicit relations."""
from collections import OrderedDict
from contextlib import closing
from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from threading import RLock

from autoslay.paths import ROOT
from autoslay.knowledge_base.repository import REPOSITORY, current_snapshot

INDEX_SCHEMA = 'knowledge-lexical/1.1'


def tokens(text):
    """Keep IDs/English and overlapping Chinese bigrams; no model dependency."""
    result = re.findall(r'[a-z0-9_.]+', text.casefold())
    for part in re.findall(r'[\u3400-\u9fff]+', text):
        result.extend(part[i:i + 2] for i in range(len(part) - 1))
        if len(part) == 1:
            result.append(part)
    return list(dict.fromkeys(result))


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ' '.join(_text(v) for v in value)
    if isinstance(value, dict):
        return ' '.join(_text(v) for v in value.values())
    return ''


def reference_text(entry):
    """Index explanatory fields, keeping source programs in cited evidence only."""
    parts = [_text(entry.get(k, '')) for k in
             ('description', 'rule', 'summary', 'tags', 'limitations', 'behavior_summary', 'applicable_conditions')]
    for group in ('variants', 'moves', 'options'):
        for row in entry.get(group, []):
            if isinstance(row, dict):
                parts.extend(_text(row.get(k, '')) for k in
                             ('description', 'title', 'name', 'text', 'label', 'static_text'))
    return ' '.join(parts).strip()


class KnowledgeService:
    def __init__(self, repository=REPOSITORY, cache_root=None):
        self.repository = repository
        self.cache_root = Path(cache_root or ROOT / '.runtime/cache/knowledge')
        self._lock = RLock()
        self._views = OrderedDict()
        self.settings = repository.read(ROOT / 'config/knowledge.default.json')
        if self.settings.get('schema_version') != 'knowledge-config/1.0':
            raise ValueError('Unsupported knowledge configuration')
        for key, maximum in (('max_results', 50), ('context_bytes', 180000), ('relation_limit', 50)):
            value = self.settings.get(key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('Invalid knowledge configuration: ' + key)
        if type(self.settings.get('timing_min_samples')) is not int or self.settings['timing_min_samples'] < 1:
            raise ValueError('Positive timing minimum sample count required')

    def acquire_snapshot(self, revision=None):
        active = current_snapshot()
        if active is not None and self.repository is REPOSITORY and revision is None:
            return active
        return self.repository.acquire_snapshot(revision)

    @staticmethod
    def _read(snapshot, name):
        path = snapshot.datasets.get(name)
        return snapshot.read(path, {}) if path else {}

    def _view(self, snapshot):
        with self._lock:
            if snapshot.revision in self._views:
                self._views.move_to_end(snapshot.revision)
                return self._views[snapshot.revision]
            datasets = {name: self._read(snapshot, name) for name in
                        ('catalog', 'behavior', 'mechanics', 'strategy', 'timing', 'aliases', 'relations')}
            if datasets['catalog']:
                from autoslay.knowledge_base.catalog import validate
                validate(datasets['catalog'])
            if datasets['behavior']:
                from autoslay.knowledge_base.behavior import validate
                validate(datasets['behavior'])
            from autoslay.knowledge_base.experience import validate as validate_experience
            for name in ('strategy', 'timing'):
                if datasets[name]:
                    validate_experience(datasets[name], name)
            aliases = datasets['aliases'].get('entries', {})
            if not isinstance(aliases, dict) or any(not isinstance(v, list) or
                    any(not isinstance(a, str) for a in v) for v in aliases.values()):
                raise ValueError('Invalid knowledge aliases')
            documents = {}
            entities = {}
            for name in ('catalog', 'behavior', 'mechanics', 'strategy', 'timing'):
                data = datasets[name]
                rows = data.get('entries', [])
                if not isinstance(rows, list):
                    raise ValueError('Knowledge entries must be a list: ' + name)
                for entry in rows:
                    identity = entry.get('entity_id') or entry.get('id')
                    if not isinstance(identity, str) or not identity:
                        raise ValueError('Knowledge ID required: ' + name)
                    if name in {'strategy', 'timing'} and entry.get('review_status') != 'reviewed':
                        continue
                    knowledge_id = identity if name in {'catalog', 'behavior'} else name + ':' + identity
                    if knowledge_id in documents:
                        raise ValueError('Duplicate knowledge ID: ' + knowledge_id)
                    title = entry.get('name') or entry.get('title') or identity
                    text = reference_text(entry)
                    document = dict(knowledge_id=knowledge_id, entity_id=entry.get('entity_id'),
                        dataset=name, category=entry.get('category', name), title=title,
                        aliases=aliases.get(knowledge_id, []) + entry.get('aliases', []), text=text,
                        game_version=entry.get('game_version', data.get('game_version')),
                        game_assembly_sha256=entry.get('game_assembly_sha256', data.get('game_assembly_sha256')),
                        language=entry.get('language', data.get('language')),
                        review_status=entry.get('review_status', entry.get('calculation_status', data.get('verification', 'reference_only'))),
                        applicable_conditions=entry.get('applicable_conditions', {}),
                        source_ref=dict(path=str(snapshot.datasets[name]), knowledge_id=knowledge_id,
                                        provenance=entry.get('provenance', entry.get('source')),
                                        dataset_hash=snapshot.documents[str(snapshot.datasets[name])].sha256),
                        content_hash=hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
                    documents[knowledge_id] = document
                    if entry.get('entity_id'):
                        entities[entry['entity_id']] = entry
            relations = datasets['relations'].get('entries', [])
            if not isinstance(relations, list) or any(not isinstance(r, dict) or
                    not all(isinstance(r.get(k), str) for k in ('source', 'target', 'kind')) for r in relations):
                raise ValueError('Invalid knowledge relations')
            view = datasets, documents, entities, relations
            self._views[snapshot.revision] = view
            while len(self._views) > 4:
                self._views.popitem(last=False)
            return view

    def get_entities(self, ids, *, snapshot=None, game_version=None, assembly_hash=None):
        if isinstance(ids, (str, bytes)):
            raise ValueError('Entity IDs must be a sequence of strings')
        ids = list(ids)
        if any(not isinstance(identity, str) or not identity for identity in ids):
            raise ValueError('Entity IDs must be nonempty strings')
        snapshot = snapshot or self.acquire_snapshot()
        _, documents, entities, _ = self._view(snapshot)
        found, missing, incompatible = [], [], []
        for identity in dict.fromkeys(ids):
            if identity not in entities:
                missing.append(identity)
            elif not self._matches(documents[identity], game_version=game_version, assembly_hash=assembly_hash):
                incompatible.append(identity)
            else:
                found.append(deepcopy(entities[identity]))
        return dict(entries=found, missing_ids=missing, incompatible_ids=incompatible,
                    knowledge_revision=snapshot.revision, reference_only=True)

    @staticmethod
    def _matches(doc, *, category=None, dataset=None, game_version=None, assembly_hash=None, language=None):
        return all(expected is None or doc.get(field) == expected for field, expected in
                   (('category', category), ('dataset', dataset), ('game_version', game_version),
                    ('game_assembly_sha256', assembly_hash), ('language', language)))

    def _index(self, snapshot, documents, force=False):
        path = self.cache_root / snapshot.revision / 'lexical.sqlite'
        with self._lock:
            if path.exists() and not force:
                try:
                    with closing(sqlite3.connect(path)) as con:
                        if con.execute('SELECT revision,schema_version FROM metadata').fetchone() == (snapshot.revision,INDEX_SCHEMA):
                            if con.execute('SELECT count(*) FROM documents').fetchone()[0] == len(documents):
                                return path
                except sqlite3.DatabaseError:
                    pass
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.sqlite')
            os.close(fd)
            try:
                with closing(sqlite3.connect(temporary)) as con:
                    con.execute('CREATE TABLE metadata(revision TEXT NOT NULL,schema_version TEXT NOT NULL)')
                    con.execute('INSERT INTO metadata VALUES (?,?)', (snapshot.revision,INDEX_SCHEMA))
                    con.execute('CREATE VIRTUAL TABLE documents USING fts5(identity UNINDEXED, name, body)')
                    con.executemany('INSERT INTO documents VALUES (?,?,?)', [
                        (identity, ' '.join(tokens(' '.join([identity, doc['title'], *doc['aliases']]))),
                         ' '.join(tokens(doc['text']))) for identity, doc in documents.items()])
                    con.commit()
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return path

    def rebuild_index(self):
        snapshot = self.acquire_snapshot()
        _, documents, _, _ = self._view(snapshot)
        path = self._index(snapshot, documents, force=True)
        return dict(knowledge_revision=snapshot.revision, path=str(path),
                    indexed_documents=len(documents), backend='sqlite_fts5', model_calls=0)

    def search(self, query, *, category=None, dataset=None, game_version=None, assembly_hash=None,
               language=None, limit=10, include_related=False, snapshot=None):
        if not isinstance(query, str) or not query.strip() or len(query) > 500 or type(limit) is not int or not 1 <= limit <= self.settings['max_results']:
            raise ValueError('Supply a query of 1..500 characters and limit 1..50')
        snapshot = snapshot or self.acquire_snapshot()
        _, documents, _, relations = self._view(snapshot)
        selected = {i: doc for i, doc in documents.items() if self._matches(doc, category=category,
            dataset=dataset, game_version=game_version, assembly_hash=assembly_hash, language=language)}
        needle = query.strip().casefold()
        exact = [i for i, doc in selected.items() if needle in
                 {i.casefold(), doc['title'].casefold(), *(a.casefold() for a in doc['aliases'])}]
        terms = tokens(query)[:100]
        backend = 'sqlite_fts5'
        scores = {}
        try:
            path = self._index(snapshot, documents)
            with closing(sqlite3.connect(path)) as con:
                expression = ' OR '.join('"' + t.replace('"', '""') + '"' for t in terms)
                rows = con.execute('SELECT identity, bm25(documents,0,8,1) FROM documents WHERE documents MATCH ? ORDER BY 2',
                                   (expression,)).fetchall() if expression else []
            scores = {identity: -score for identity, score in rows if identity in selected}
        except (sqlite3.DatabaseError, OSError):
            backend = 'memory_keywords'
            for identity, doc in selected.items():
                name = set(tokens(' '.join([identity, doc['title'], *doc['aliases']])))
                body = set(tokens(doc['text']))
                value = sum(8 if t in name else 1 if t in body else 0 for t in terms)
                if value:
                    scores[identity] = value
        ranked = sorted(scores, key=lambda i: (-scores[i], i))
        ordered = exact + [i for i in ranked if i not in exact]
        entries = []
        for identity in ordered[:limit]:
            doc = selected[identity]
            entries.append(deepcopy({k: v for k, v in doc.items() if k != 'text'}) |
                           dict(snippet=doc['text'][:1200], match='exact' if identity in exact else 'keywords',
                                score=scores.get(identity, 0)))
        related = []
        if include_related:
            primary = {e['knowledge_id'] for e in entries}
            targets = {r['target'] for r in relations if r['source'] in primary}
            related = [deepcopy({k: v for k, v in selected[i].items() if k != 'text'})
                       for i in sorted(targets - primary) if i in selected][:self.settings['relation_limit']]
        return dict(entries=entries, related_entries=related, total_matches=len(ordered),
                    knowledge_revision=snapshot.revision, backend=backend, reference_only=True,
                    model_calls=0, limitations=['Search ranking does not certify executable rules.'])

    def get_mechanics(self, ids=None, *, game_version=None, snapshot=None):
        snapshot = snapshot or self.acquire_snapshot()
        data = self._read(snapshot, 'mechanics')
        if game_version is not None and data.get('game_version') != game_version:
            return dict(error='UNSUPPORTED_MECHANICS_VERSION', knowledge_revision=snapshot.revision)
        wanted = None if ids is None else set(ids)
        return dict(entries=[e for e in data.get('entries', []) if wanted is None or e['id'] in wanted],
                    game_version=data.get('game_version'), knowledge_revision=snapshot.revision)

    def get_rule_view(self, *, snapshot=None):
        from autoslay.knowledge_base.rulebook import Rulebook
        from autoslay.knowledge_base.repository import knowledge_scope
        snapshot = snapshot or self.acquire_snapshot()
        with knowledge_scope(snapshot):
            return Rulebook(snapshot.datasets['rules'], snapshot.datasets['catalog'])

    def get_monster(self, identity, *, ascension, game_version, assembly_hash, snapshot=None):
        if type(ascension) is not int or ascension < 0:
            raise ValueError('Nonnegative ascension required')
        if not game_version or not assembly_hash:
            raise ValueError('Monster reference requires game version and assembly hash')
        snapshot = snapshot or self.acquire_snapshot()
        result = self.get_entities([identity], snapshot=snapshot,
                                   game_version=game_version, assembly_hash=assembly_hash)
        if not result['entries'] or result['entries'][0]['category'] != 'monster':
            return dict(error='MONSTER_REFERENCE_MISSING_OR_INCOMPATIBLE', knowledge_revision=snapshot.revision)
        from autoslay.knowledge_base.behavior import compact
        entry = compact(result['entries'][0])
        entry['health_variants'] = [v for v in entry.get('health_variants', [])
                                   if v['ascension_min'] <= ascension <= v['ascension_max']]
        for move in entry.get('moves', []):
            move['numeric_variants'] = [v for v in move.get('numeric_variants', [])
                                       if v['ascension_min'] <= ascension <= v['ascension_max']]
        return dict(entry=entry, ascension=ascension, knowledge_revision=snapshot.revision,
                    reference_only=True, rules_enabled=False)

    def build_context(self, ids, *, snapshot=None, max_bytes=None):
        snapshot = snapshot or self.acquire_snapshot()
        maximum = self.settings['context_bytes'] if max_bytes is None else max_bytes
        if type(maximum) is not int or maximum < 2:
            raise ValueError('Context byte budget must be at least 2')
        result = self.get_entities(ids, snapshot=snapshot)
        kept, omitted = [], []
        for entry in result['entries']:
            if len(json.dumps(kept + [entry], ensure_ascii=False).encode()) <= maximum:
                kept.append(entry)
            else:
                omitted.append(entry['entity_id'])
        return result | dict(entries=kept, omitted_ids=omitted)

    def get_timing_profile(self, scope, environment, *, game_version=None, snapshot=None):
        snapshot = snapshot or self.acquire_snapshot()
        datasets, _, _, _ = self._view(snapshot)
        rows = [deepcopy(e) for e in datasets['timing'].get('entries', [])
                if e.get('review_status') == 'reviewed' and e.get('measurement_scope') == scope
                and e.get('environment') == environment
                and (game_version is None or e.get('game_version') == game_version)
                and type(e.get('samples')) is int and e['samples'] >= self.settings['timing_min_samples']]
        return dict(entries=rows, knowledge_revision=snapshot.revision,
                    gap=None if rows else 'No reviewed timing profile for this scope and environment')

    def status(self):
        snapshot = self.acquire_snapshot()
        return dict(knowledge_revision=snapshot.revision, datasets={name: dict(path=str(path),
            available=snapshot.documents[str(path)] is not None) for name, path in snapshot.datasets.items()},
            cache=self.repository.status(), retrieval='offline_keywords', model_calls=0)


SERVICE = KnowledgeService()
