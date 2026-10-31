"""Birleşik sistemin özellik bayrakları ve güvenlik kilitleri.

config.yaml → `birlesik:` bloğu (yoksa aşağıdaki varsayılanlar). Mevcut ayarların hiçbiri değişmez.

  karar_modu    legacy (varsayılan) | shadow | unified
                legacy  : mevcut ajan sistemi karar verir; birleşik motor çalışmaz.
                shadow  : kararları kaydeder; yalnız operasyonel uygunluk kapısından geçen gölge adayları ayrı paper defterinde sanal emir/pozisyon olarak yürütür. Gerçek borsa emri yok.
                unified : birleşik motor karar verir (PR 2–3 tamamlanıp kullanıcı onaylamadan açılmaz).
  yurutme_modu  paper (tek izinli) | okx_demo | okx_live
                Bu sürümde paper dışı her istek REDDEDİLİR ve paper kullanılır. Gerçek emir, gerçek hesap, API anahtarı yok.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .models import DecisionMode, ExecutionMode

DEFAULTS: dict[str, Any] = {
    "karar_modu": "legacy",
    "yurutme_modu": "paper",
    "golge_aralik_sn": 300,            # gölge modunda birleşik motor en sık bu aralıkla çalışır
    "gunluk_saklama_gun": 14,          # karar günlüğü (site/birlesik/kararlar-*.jsonl) bu kadar gün tutulur
    "kapsam": {"min_oran": 0.8},
    "maliyet": {
        # Belirsizlik tamponu (bp, nominalin). Maliyet sonrası avantaj bundan küçükse SHADOW. Kısa ufukta daha sıkı:
        # gün içi araştırmada brüt kenar 10–30 bp iken maliyet ~14–20 bp çıktı (data/TEK_AJAN_ARASTIRMA.md, bölüm 5).
        "belirsizlik_tamponu_bp": {"micro": 30.0, "intraday": 20.0, "swing": 10.0, "position": 10.0, "carry": 10.0},
        "eksik_veri_tampon_bp": 5.0,       # eksik her maliyet girdisi için tampona eklenir
        "min_guven": 0.6,                  # maliyet hesabı güveni bunun altındaysa en iyi ihtimalle SHADOW
        "stop_olasiligi": 0.5,             # beklenen stop kayması = olasılık × stop'ta kayma (bilinmiyorsa)
        "kacirma_tamponu_bp": 5.0,         # limit/IOC girişte kaçırma ve kısmi dolum tamponu
        "kismi_dolum_shadow_orani": 0.5,   # IOC'de doldurulamayan oran bundan büyükse SHADOW
        # Girdi yoksa kullanılan muhafazakâr varsayımlar (hepsi missing_inputs ve assumptions içinde yazılır):
        "varsayilan_spread_bp": {"micro": 4.0, "intraday": 6.0, "swing": 8.0, "position": 8.0, "carry": 8.0},
        "varsayilan_kayma_bp": {"micro": 5.0, "intraday": 8.0, "swing": 10.0, "position": 10.0, "carry": 10.0},
        "varsayilan_tutma_saat": {"micro": 0.25, "intraday": 6.0, "swing": 72.0, "position": 336.0, "carry": 720.0},
        "varsayilan_funding_yillik_yuzde": 11.0,   # bt2 sert modeli: long günde %0,03 öder
        "funding_gelir_kredisi": False,    # yönlü işlemde funding GELİRİNİ avantaja sayma (muhafazakâr)
    },
}


class ExecutionModeError(RuntimeError):
    """Paper dışı yürütme istendi (bu sürümde yasak)."""


def _merge(base: dict, over: Mapping | None) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def unified_cfg(cfg: Mapping | None) -> dict:
    return _merge(DEFAULTS, (cfg or {}).get("birlesik"))


def decision_mode(cfg: Mapping | None) -> DecisionMode:
    """Geçersiz ya da boş değer → LEGACY (güvenli taraf)."""
    raw = str(unified_cfg(cfg).get("karar_modu") or "").strip().lower()
    try:
        return DecisionMode(raw)
    except ValueError:
        return DecisionMode.LEGACY


def execution_mode(cfg: Mapping | None) -> tuple[ExecutionMode, str | None]:
    """Döner: (kullanılacak mod, uyarı). Paper dışı istek reddedilir → (PAPER, uyarı metni)."""
    raw = str(unified_cfg(cfg).get("yurutme_modu") or "paper").strip().lower()
    if raw == ExecutionMode.PAPER.value:
        return ExecutionMode.PAPER, None
    return ExecutionMode.PAPER, (f"yürütme modu '{raw}' istendi ama bu sürümde yalnız paper izinli; paper kullanılıyor "
                                 "(gerçek emir ve borsa bağlantısı kapalı)")


def ensure_paper(mode: ExecutionMode | str) -> None:
    """Yürütme adaptörleri emir işlemeden önce çağırır. Paper dışı → ExecutionModeError."""
    m = mode.value if isinstance(mode, ExecutionMode) else str(mode).lower()
    if m != ExecutionMode.PAPER.value:
        raise ExecutionModeError(f"'{m}' yürütme modu bu sürümde kapalı; yalnız paper işlem yapılabilir")
