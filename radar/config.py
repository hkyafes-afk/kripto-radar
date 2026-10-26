"""Ayarları config.yaml'dan okur, eksik/bozuk alanlarda güvenli varsayılanları kullanır."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "borsa": {"ana": "okx", "funding_karsilastir": ["bitget", "gate", "mexc", "htx", "binance", "bybit"]},
    "tarama": {
        "coin_sayisi": 100,
        "min_hacim_usd": 5_000_000,
        "periyot": "4H",
        "sadece_kripto": True,
        "periyotlar": ["15m", "1H", "4H"],
        "haric": ["USDC", "DAI", "TUSD", "FDUSD", "USDE", "PYUSD", "USDG"],
    },
    "gostergeler": {
        "supertrend_atr": 10,
        "supertrend_carpan": 3.0,
        "rsi": 14,
        "adx": 14,
        "adx_trend_min": 20,
        "bollinger": 20,
        "bollinger_std": 2.0,
        "ema_hizli": 20,
        "ema_yavas": 50,
        "gunluk_ema": 50,
        "divergence_pivot": 3,
        "divergence_bakis": 30,
    },
    "funding": {"long_esik_yillik": -10, "short_esik_yillik": 50, "trend_asiri_yillik": 60},
    "rejim": {
        "ayi_long_carpan": 0.5,
        "boga_short_carpan": 0.6,
        "short_temel_carpan": 0.85,
        "ayi_donus_long_carpan": 0.6,
    },
    "sinyal": {
        "guclu_esik": 65,
        "izle_esik": 50,
        "trend": {"stop_atr": 1.5, "hedef_atr": 3.0, "max_saat": 168},
        "donus": {"stop_atr": 1.2, "hedef_atr": 2.0, "max_saat": 72},
    },
    "periyot_ayar": {
        "15m": {"trend_max_saat": 24, "donus_max_saat": 8},
        "1H": {"trend_max_saat": 72, "donus_max_saat": 24},
        "4H": {"trend_max_saat": 168, "donus_max_saat": 72},
    },
    "demo": {"baslangic_bakiye": 1000, "islem_tutari_usd": 20, "kaldirac": 1,
             "max_acik_toplam": 12, "max_acik_periyot": 6, "boyut_modu": "risk"},
    "risk": {"risk_yuzde": 0.5, "max_pozisyon_oran": 0.35, "max_kaldirac": 5, "max_isi_yuzde": 3.0,
             "max_ayni_yon": 5, "max_ayni_yon_risk_yuzde": 2.0, "max_spread_bp": 15, "max_kayma_bp": 20,
             "kismi_kar_r": 1.0, "kismi_kar_oran": 0.5, "basabas": True, "iz_suren_atr": 2.0,
             "gunluk_kayip_yuzde": 2.0, "max_dusus_yuzde": 10.0, "kayip_serisi": 4, "seri_risk_carpan": 0.5,
             "setup_kapat_min_islem": 30, "setup_kapat_beklenti_r": -0.05, "otomatik_kapat": True},
    "panel": {"telegram_panel": True, "panel_sn": 15},
    "canli": {"dongu_dakika": 330, "fiyat_kontrol_sn": 60, "komut_kontrol_sn": 15, "yayin_min_dakika": 6,
              "websocket": True, "bitget_ws": True},
    "ucret": {"taker_yuzde": 0.05, "maker_yuzde": 0.02, "mmr_yuzde": 0.5},
    "defter": {
        "komisyon_gidis_donus": 0.10,
        "kayma": 0.05,
        "tekrar_bekleme_saat": 24,
        "min_ornek": 30,
    },
    "harvest": {
        "min_perp_hacim_usd": 10_000_000,
        "min_spot_hacim_usd": 2_000_000,
        "min_yillik": 8,
        "min_pozitif_oran": 0.75,
        "gun": 30,
        "liste": 15,
        "maliyet_yuzde": 0.30,
    },
    "bildirim": {
        "telegram": True,
        "whatsapp": True,
        "gunluk_ozet_saat": 9,
        "saat_dilimi": "Europe/Istanbul",
        "max_sinyal_mesaj": 8,
    },
    "dashboard": {"url": ""},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def load_config(path: str | os.PathLike | None = None) -> dict:
    p = Path(path) if path else ROOT / "config.yaml"
    user: dict = {}
    if p.exists():
        try:
            user = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:  # kullanıcı dosyayı bozarsa bot yine çalışsın
            print(f"[uyarı] config.yaml okunamadı, varsayılanlar kullanılıyor: {e}")
    cfg = _merge(DEFAULTS, user)
    if not cfg["dashboard"].get("url"):
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        if "/" in repo:
            owner, name = repo.split("/", 1)
            cfg["dashboard"]["url"] = f"https://{owner.lower()}.github.io/{name}/"
    return cfg
