"""JSON dosya yardımcıları (state, defter). Atomik yazma ile bozulmaya karşı korumalı."""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path


def load(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _clean(o):
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else round(o, 8)
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item"):  # numpy skalerleri
        return _clean(o.item())
    return o


def dump(path: Path, obj, compact: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(_clean(obj), ensure_ascii=False,
                      separators=(",", ":") if compact else None,
                      indent=None if compact else 1)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def clean(obj):
    return _clean(obj)
