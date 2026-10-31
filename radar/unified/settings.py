"""Dashboard için config.yaml parametre keşfi.

Bu modül yalnızca mevcut konfigürasyonu keşfeder ve sınıflandırır.
Ayar yazma/uygulama API'si değildir; güvenli yazma sözleşmesi tamamlanmadan
arayüzün bu katalog üzerinden konfigürasyonu değiştirmesine izin verilmemelidir.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

SCHEMA_VERSION = 1

# Bunlar sıradan ayar olarak UI'dan değiştirilemez. PAPER ve tek-otorite
# sınırı konfigürasyon ekranından gevşetilemez.
_PROTECTED_PATHS = {
    "birlesik.karar_modu",
    "birlesik.yurutme_modu",
}
_SECRET_PARTS = {
    "api_key", "apikey", "secret", "token", "private_key", "password",
    "passphrase", "credential", "credentials",
}

_CATEGORY_PREFIXES = (
    ("gostergeler", "indicators"),
    ("sinyal", "strategies"),
    ("periyot_ayar", "strategies"),
    ("ekip", "strategies"),
    ("risk", "risk"),
    ("birlesik.risk", "risk"),
    ("borsa", "market"),
    ("tarama", "market"),
    ("funding", "derivatives"),
    ("harvest", "derivatives"),
    ("canli", "data"),
    ("birlesik.kapsam", "data"),
    ("birlesik.maliyet", "costs"),
    ("ucret", "costs"),
    ("defter", "costs"),
    ("birlesik.paper", "capital"),
    ("demo", "capital"),
    ("bildirim", "notifications"),
    ("panel", "notifications"),
    ("dashboard", "dashboard"),
)


def _value_type(value: Any) -> str:
    # bool, Python'da int alt sınıfıdır; önce kontrol edilmelidir.
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return "object"


def _category(path: str) -> str:
    for prefix, category in _CATEGORY_PREFIXES:
        if path == prefix or path.startswith(prefix + "."):
            return category
    return "general"


def _is_protected(path: str) -> bool:
    parts = path.lower().split(".")
    if path in _PROTECTED_PATHS:
        return True
    return any(any(secret in part for secret in _SECRET_PARTS) for part in parts)


def discover_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    """Config içindeki bütün scalar/list değerleri deterministik kataloglar.

    Katalog değerleri salt okunur keşif verisidir. Bilinmeyen aralıklar
    tahmin edilmez; write_ready her zaman False kalır.
    """
    entries: list[dict[str, Any]] = []

    def walk(node: Mapping[str, Any], prefix: str = "") -> None:
        for key in sorted(node, key=str):
            value = node[key]
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Mapping):
                walk(value, path)
                continue
            protected = _is_protected(path)
            entries.append({
                "path": path,
                "label": str(key).replace("_", " ").strip().capitalize(),
                "category": _category(path),
                "value_type": _value_type(value),
                "value": deepcopy(value),
                "protected": protected,
                "discovered": True,
                "write_ready": False,
                "write_block_reason": (
                    "Güvenlik/işlem yetkisi ayarı; sıradan dashboard ayarı olamaz."
                    if protected else
                    "Tip, aralık, kapsam ve atomik uygulama doğrulaması tamamlanmadı."
                ),
            })

    walk(config)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "read_only_discovery",
        "write_enabled": False,
        "parameter_count": len(entries),
        "editable_count": sum(not item["protected"] for item in entries),
        "write_ready_count": 0,
        "categories": sorted({item["category"] for item in entries}),
        "parameters": entries,
    }
