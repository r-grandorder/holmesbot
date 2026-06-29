from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Callable

DEFAULT_PATH = Path(__file__).resolve().parent / "shadows.json"


class ShadowCatalog:
    """Which (servant_id, ascension) pairs have a precomputed silhouette in S3.

    Written by scripts/precompute_silhouettes.py. Empty if not yet generated, in
    which case guess_shadow reports nothing eligible (the other games are fine).
    """

    def __init__(self, entries: list[tuple[int, str]]) -> None:
        self._entries = entries

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> "ShadowCatalog":
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = json.loads(p.read_text())  # {"servant_id": ["1", "3"], ...}
        entries = [(int(sid), asc) for sid, ascs in raw.items() for asc in ascs]
        return cls(entries)

    def __len__(self) -> int:
        return len(self._entries)

    def pick(
        self, allow: Callable[[int, str], bool] | None = None
    ) -> tuple[int, str] | None:
        gate = allow or (lambda _sid, _asc: True)
        pool = [(sid, asc) for sid, asc in self._entries if gate(sid, asc)]
        return random.choice(pool) if pool else None
