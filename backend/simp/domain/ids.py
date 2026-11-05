"""Deterministic ULIDs.

A ULID is 48 bits of millisecond timestamp + 80 bits of randomness, Crockford base32.
SIMP takes the timestamp from the *simulated* event time and the 80 random bits from a
seeded counter mixed with the run id, so the same run config replays to the same ids,
ids still sort by (simulated) creation time, and two runs never collide.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(out))


def ulid_from(ts: datetime, entropy: int) -> str:
    ms = int(ts.timestamp() * 1000) & ((1 << 48) - 1)
    return _encode(ms, 10) + _encode(entropy & ((1 << 80) - 1), 16)


def ulid_timestamp_ms(ulid: str) -> int:
    v = 0
    for ch in ulid[:10]:
        v = (v << 5) | _CROCKFORD.index(ch)
    return v


class IdFactory:
    """Issues deterministic, unique ULIDs for one run."""

    def __init__(self, run_id: str) -> None:
        self._salt = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:10], "big")
        self._counter = 0

    def new(self, ts: datetime) -> str:
        self._counter += 1
        # 40 bits of run salt (high) + 40 bits counter (low) keeps per-ms ordering by issue order.
        entropy = ((self._salt >> 40) << 40) | (self._counter & ((1 << 40) - 1))
        return ulid_from(ts, entropy)


def run_id_for(config_hash: str, nonce: str) -> str:
    """Run ids are plain ULIDs built from submission time + a hash of (config_hash, nonce)."""
    now = datetime.now().astimezone()
    ent = int.from_bytes(hashlib.sha256(f"{config_hash}:{nonce}".encode()).digest()[:10], "big")
    return ulid_from(now, ent)
