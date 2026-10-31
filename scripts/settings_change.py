"""Validate a narrowly allow-listed settings change without rewriting YAML comments."""
from __future__ import annotations

import argparse
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode

# Exact paths only. These bounds are intentionally conservative and reviewed.
ALLOWLIST = {
    "tarama.coin_sayisi": ("integer", 20, 150),
    "tarama.min_hacim_usd": ("integer", 1_000_000, 100_000_000),
    "risk.risk_yuzde": ("number", 0.1, 1.0),
    "risk.max_spread_bp": ("integer", 1, 50),
    "risk.max_kayma_bp": ("integer", 1, 50),
    "sinyal.guclu_esik": ("integer", 55, 90),
    "sinyal.izle_esik": ("integer", 35, 70),
}
FORBIDDEN_PREFIXES = ("birlesik.", "borsa.", "ekip.", "demo.", "canli.", "telegram.", "bildirim.")


class SettingsChangeError(ValueError):
    pass


def _node_for_path(root: MappingNode, path: str) -> ScalarNode:
    current: Any = root
    for part in path.split("."):
        if not isinstance(current, MappingNode):
            raise SettingsChangeError(f"Yapı beklenenden farklı: {path}")
        found = None
        for key_node, value_node in current.value:
            if isinstance(key_node, ScalarNode) and key_node.value == part:
                found = value_node
                break
        if found is None:
            raise SettingsChangeError(f"Parametre bulunamadı: {path}")
        current = found
    if not isinstance(current, ScalarNode):
        raise SettingsChangeError("Yalnız mevcut scalar parametreler değiştirilebilir.")
    return current


def apply_change(text: str, path: str, raw_value: str) -> tuple[str, dict]:
    if path not in ALLOWLIST or path.startswith(FORBIDDEN_PREFIXES):
        raise SettingsChangeError(f"Bu parametre değişiklik listesinde yok: {path}")
    kind, minimum, maximum = ALLOWLIST[path]
    try:
        value = yaml.safe_load(raw_value)
    except yaml.YAMLError as exc:
        raise SettingsChangeError("Yeni değer YAML olarak okunamadı.") from exc
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsChangeError("Bu parametre tam sayı olmalı.")
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsChangeError("Bu parametre sayısal olmalı.")
        value = float(value)
        if not math.isfinite(value):
            raise SettingsChangeError("NaN/sonsuz değer kabul edilmez.")
    if not minimum <= value <= maximum:
        raise SettingsChangeError(f"İzin verilen aralık: {minimum}–{maximum}")

    config = yaml.safe_load(text)
    node = _node_for_path(yaml.compose(text), path)
    current = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise SettingsChangeError(f"Parametre config içinde yok: {path}")
        current = current[part]
    if kind == "integer" and (isinstance(current, bool) or not isinstance(current, int)):
        raise SettingsChangeError("Mevcut config tipi integer değil.")
    if kind == "number" and (isinstance(current, bool) or not isinstance(current, (int, float))):
        raise SettingsChangeError("Mevcut config tipi number değil.")

    replacement = str(value)
    updated = text[:node.start_mark.index] + replacement + text[node.end_mark.index:]
    # Parse again before any write; malformed/duplicate YAML is rejected.
    yaml.safe_load(updated)
    return updated, {"path": path, "old_value": current, "new_value": value,
                     "range": [minimum, maximum], "approval": "pull_request_required"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--value", required=True)
    parser.add_argument("--file", default="config.yaml")
    args = parser.parse_args()
    target = Path(args.file)
    original = target.read_text(encoding="utf-8")
    updated, summary = apply_change(original, args.path, args.value)
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print(yaml.safe_dump(summary, allow_unicode=True, sort_keys=True).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
