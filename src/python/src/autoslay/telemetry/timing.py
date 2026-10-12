from contextlib import contextmanager
from time import perf_counter

VERSION = "1.0.0"


class Timing:
    def __init__(self):
        self.stages_ms = {}

    @contextmanager
    def stage(self, name: str):
        start = perf_counter()
        try:
            yield
        finally:
            self.stages_ms[name] = self.stages_ms.get(name, 0) + (perf_counter() - start) * 1000
