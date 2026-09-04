"""Bounded in-memory repository indices; never writes a copy of image data."""
from collections import OrderedDict
import sys
import threading
import time


def deep_size(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(deep_size(k, seen) + deep_size(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(deep_size(v, seen) for v in value)
    elif hasattr(value, "__dict__"):
        size += deep_size(vars(value), seen)
    return size


class BoundedCache:
    def __init__(self, max_bytes, ttl=900, max_entries=8, clock=time.monotonic):
        self.max_bytes, self.ttl, self.max_entries, self.clock = max_bytes, ttl, max_entries, clock
        self.entries = OrderedDict()
        self.bytes = 0
        self.lock = threading.RLock()

    def _remove(self, key):
        self.bytes -= self.entries.pop(key)[1]

    def sweep(self):
        with self.lock:
            now = self.clock()
            for key, (_, _, at) in list(self.entries.items()):
                if now - at >= self.ttl:
                    self._remove(key)

    def get(self, key, default=None):
        with self.lock:
            self.sweep()
            value = self.entries.get(key)
            if value is None:
                return default
            self.entries.move_to_end(key)
            self.entries[key] = (value[0], value[1], self.clock())
            return value[0]

    def __setitem__(self, key, value):
        size = deep_size(key) + deep_size(value)
        with self.lock:
            self.sweep()
            if key in self.entries:
                self._remove(key)
            if size > self.max_bytes:
                return
            while self.entries and (self.bytes + size > self.max_bytes or len(self.entries) >= self.max_entries):
                self._remove(next(iter(self.entries)))
            self.entries[key] = (value, size, self.clock())
            self.bytes += size

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.bytes = 0

    def stats(self):
        with self.lock:
            self.sweep()
            return {"entries": len(self.entries), "max_entries": self.max_entries,
                    "bytes": self.bytes, "limit_bytes": self.max_bytes, "ttl_seconds": self.ttl}
