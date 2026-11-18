"""Locks per key: a result computed in the background and asked for by a page at the same time is computed once (the
second caller waits for the first, then finds it cached).
"""

import threading
from collections.abc import Hashable

_locks: dict[Hashable, threading.Lock] = {}
_guard = threading.Lock()


def key_lock(key: Hashable) -> threading.Lock:
    """The lock of a key (the same lock for equal keys)."""
    with _guard:
        return _locks.setdefault(key, threading.Lock())
