"""Tiny local text embedding for FalkorDB's vector index: no API key, no model download, deterministic.

Signed feature hashing of words and word pairs into 256 dimensions, L2-normalised. It captures shared vocabulary
("connection pool exhausted" ~ "pool connections queueing") rather than deep meaning. It's swappable for any
embedding model, because the vector index and the queries stay the same.
"""

from __future__ import annotations

import hashlib
import math
import re

DIM = 256
_STOP = set("the a an and or of to in on at for with is are was were be been it this that by from as after before "
            "into out up down not no but our we you they".split())


def _tokens(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 1]
    # light stemming so "connections" ~ "connection", "timeouts" ~ "timeout"
    words = [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words]
    return words + [f"{a}_{b}" for a, b in zip(words, words[1:])]


def embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for tok in _tokens(text):
        h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:8], "little")
        v[h % DIM] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]
