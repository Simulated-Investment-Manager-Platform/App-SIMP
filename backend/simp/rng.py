"""Seeded randomness (spec: Determinism).

Every draw uses a NumPy PCG64 generator seeded from (run seed, component name, date), so
changing one component's parameters never reshuffles another component's draws.
"""

from __future__ import annotations

import zlib
from datetime import date

import numpy as np


def component_rng(seed: int, component: str, day: date | None = None, *extra: int) -> np.random.Generator:
    entropy = [int(seed) & 0xFFFFFFFF, zlib.crc32(component.encode())]
    if day is not None:
        entropy.append(day.toordinal())
    entropy.extend(int(x) & 0xFFFFFFFF for x in extra)
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(entropy)))
