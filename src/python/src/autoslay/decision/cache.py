"""Bounded immutable analysis/reference cache; keys include versions and policy."""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock
import hashlib
import json

VERSION = "1.0.0"

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

class AnalysisCache:
    def __init__(self, capacity=64):
        if type(capacity) is not int or capacity<1: raise ValueError('Positive integer cache capacity required')
        self.capacity, self.entries, self.lock = capacity, OrderedDict(), RLock()
    def get(self, key):
        with self.lock:
            if key not in self.entries: return None
            self.entries.move_to_end(key)
            return deepcopy(self.entries[key])
    def put(self, key, value):
        with self.lock:
            self.entries[key] = deepcopy(value)
            self.entries.move_to_end(key)
            while len(self.entries) > self.capacity: self.entries.popitem(last=False)
    def clear(self):
        with self.lock: self.entries.clear()
