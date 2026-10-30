"""Özellik bayrakları ve paper kilidi (radar/unified/flags.py). Config sözlükleri SENTETİKTİR."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.config import load_config  # noqa: E402
from radar.unified import flags as F  # noqa: E402
from radar.unified.models import DecisionMode, ExecutionMode  # noqa: E402


def test_gercek_config_varsayilanlari_guvenli():
    cfg = load_config()
    assert F.decision_mode(cfg) is DecisionMode.LEGACY
    assert F.execution_mode(cfg) == (ExecutionMode.PAPER, None)


def test_sentetik_gecersiz_karar_modu_legacy():
    assert F.decision_mode({"birlesik": {"karar_modu": "turbo"}}) is DecisionMode.LEGACY
    assert F.decision_mode({"birlesik": {"karar_modu": " SHADOW "}}) is DecisionMode.SHADOW
    assert F.decision_mode({}) is DecisionMode.LEGACY and F.decision_mode(None) is DecisionMode.LEGACY


@pytest.mark.parametrize("mode", ["okx_demo", "okx_live", "LIVE", "gercek"])
def test_sentetik_paper_disi_yurutme_reddedilir(mode):
    m, warn = F.execution_mode({"birlesik": {"yurutme_modu": mode}})
    assert m is ExecutionMode.PAPER and warn and "yalnız paper" in warn
    with pytest.raises(F.ExecutionModeError):
        F.ensure_paper(mode)
    F.ensure_paper(ExecutionMode.PAPER)


def test_sentetik_ic_ice_ayar_birlesimi():
    u = F.unified_cfg({"birlesik": {"maliyet": {"belirsizlik_tamponu_bp": {"swing": 15.0}}}})
    assert u["maliyet"]["belirsizlik_tamponu_bp"]["swing"] == 15.0
    assert u["maliyet"]["belirsizlik_tamponu_bp"]["intraday"] == 20.0        # diğerleri varsayılan kalır
    assert F.DEFAULTS["maliyet"]["belirsizlik_tamponu_bp"]["swing"] == 10.0  # varsayılanlar bozulmadı
