"""Minimal stdlib ULID.

48-bit millisecond timestamp + 80 bits of randomness, Crockford base32,
26 chars, lexicographically sortable. Monotonic within the process via a
simple lock-and-bump so journal events for the same millisecond sort in
emission order.
"""
from __future__ import annotations

import os
import threading
import time

_ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32
_LOCK = threading.Lock()
_LAST = (0, 0)  # (ms, randomness-high-portion)


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ENCODING[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_ulid() -> str:
    global _LAST
    with _LOCK:
        ms = int(time.time() * 1000)
        rand = int.from_bytes(os.urandom(10), "big")
        last_ms, last_rand = _LAST
        if ms == last_ms and rand <= last_rand:
            rand = last_rand + 1
        _LAST = (ms, rand)
    return _encode(ms, 10) + _encode(rand, 16)


def ulid_ts(ulid: str) -> float:
    """Decode the millisecond timestamp of a ULID."""
    ms = 0
    for ch in ulid[:10]:
        ms = (ms << 5) | _ENCODING.index(ch)
    return ms / 1000.0
