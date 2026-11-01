"""UnifiedDecisionEngine (radar/unified/decision.py). Bağlamlar ve fırsatlar SENTETİKTİR (elle yazılmış sayılar)."""
from __future__ import annotations

import copy
from dataclasses import replace
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from radar.config import load_config  # noqa: E402
from radar.unified.decision import UnifiedDecisionEngine, exit_rule_tr  # noqa: E402
from radar.unified.factors import FactorEngine  # noqa: E402
from radar.unified.models import Decision, FactorCoverage, Horizon, Opportunity, StrategyAuthority  # noqa: E402
from radar.unified.strategies import Strategy, SwingTrendStrategy  # noqa: E402
from test_unified_strategies import kemal_ctx  # noqa: E402

CFG = load_config()
NOW = 1_800_000_000_000


class SentetikStrateji(Strategy):
    """Test için sabit fırsat döndüren sentetik strateji."""
    horizon = Horizon.SWING
    timeframes = ("1D",)
    default_authority = StrategyAuthority.PAPER

    def __init__(self, cfg, opps, name="sentetik", authority=StrategyAuthority.PAPER, boom=False):
        self.name = name
        super().__init__(cfg)
        self.authority = authority
        self._opps, self._boom = opps, boom

    def evaluate(self, ctx):
        if self._boom:
            raise RuntimeError("sentetik hata")
        return list(self._opps)


def opp(side="long", strategy="sentetik", gross=400.0, authority=StrategyAuthority.PAPER, entry=101.0, dist=9.0):
    sg = 1 if side == "long" else -1
    return Opportunity(symbol="X", inst="X-USDT-SWAP", side=side, strategy=strategy, setup=strategy, timeframe="1D",
                       horizon=Horizon.SWING, created_ts_ms=NOW, bar_ts_ms=NOW, entry=entry, stop=entry - sg * dist,
                       targets=(entry + sg * 2 * dist,), gross_edge_bp=gross, confidence=0.6,
                       evidence_for=(f"{strategy} kanıtı",), authority=authority)


def mctx(**kw):
    raw = kemal_ctx(ema20=97.5, **kw)
    return FactorEngine(CFG).from_legacy_ctx(raw, now_ms=NOW, live_ts_ms=NOW, list_ts_ms=NOW - 900 * 86_400_000)


def no_policy_cfg():
    c = copy.deepcopy(CFG)
    c.setdefault("ekip", {}).setdefault("kurallar", {})["aktif"] = False
    return c


def test_sentetik_legacy_trend_firsati_acilir_ve_aciklanir():
    eng = UnifiedDecisionEngine(CFG, strategies=[SwingTrendStrategy(CFG)])
    ds = eng.decide([mctx()], NOW)
    assert len(ds) == 1
    d = ds[0]
    assert d.decision is Decision.OPEN and d.strategy == "swing_trend" and d.side.value == "long"
    assert d.net_edge_bp and d.net_edge_bp > 0 and d.risk_proposal["boyut_carpani"] == 1.0
    assert "AÇ (paper)" in d.explanation_tr and "Çıkış planı" in d.explanation_tr
    assert "kural çıkışı" in d.explanation_tr and "hedef yok" in d.explanation_tr      # Kemal: hedefsiz, MA çıkışı
    assert not eng.errors


def test_sentetik_ayni_firsat_tek_karar_kanit_birlesir():
    s1 = SentetikStrateji(CFG, [opp(strategy="a", gross=300)], name="a")
    s2 = SentetikStrateji(CFG, [opp(strategy="b", gross=500)], name="b")
    ds = UnifiedDecisionEngine(no_policy_cfg(), strategies=[s1, s2]).decide([mctx()], NOW)
    assert len(ds) == 1                                                   # ayrı pozisyon yok
    d = ds[0]
    assert set(d.strategies) == {"a", "b"} and d.evidence_count == 2
    assert d.strategy == "b"                                              # net avantajı yüksek olan birincil
    assert {"a kanıtı", "b kanıtı"} <= set(d.evidence_for)
    assert "tek pozisyon, tek risk bütçesi" in d.explanation_tr


def test_sentetik_veri_sorunu_shadow():
    ds = UnifiedDecisionEngine(CFG, strategies=[SwingTrendStrategy(CFG)]).decide(
        [mctx(suspect="Bitget ile fiyat farkı %4")], NOW)
    assert ds[0].decision is Decision.SHADOW and any(c.startswith("VERI_SORUNU") for c in ds[0].reason_codes)


def test_sentetik_zorunlu_veri_eksik_shadow():
    ds = UnifiedDecisionEngine(CFG, strategies=[SwingTrendStrategy(CFG)]).decide([mctx(w_trend=None)], NOW)
    assert ds[0].decision is Decision.SHADOW and "ZORUNLU_EKSIK:btc_regime" in ds[0].reason_codes
    assert "btc_regime" in ds[0].missing_factors


def test_sentetik_politika_oynaklik_kapisi():
    ds = UnifiedDecisionEngine(CFG, strategies=[SwingTrendStrategy(CFG)]).decide([mctx(atr=6.5)], NOW)
    assert ds and ds[0].decision is Decision.SHADOW
    assert any(c.startswith("POLITIKA:oynaklık kapısı") for c in ds[0].reason_codes)


def test_sentetik_genc_coin_shadow():
    ds = UnifiedDecisionEngine(CFG, strategies=[SwingTrendStrategy(CFG)]).decide([mctx(coin_class="C")], NOW)
    assert ds[0].decision is Decision.SHADOW and "SINIF_C" in ds[0].reason_codes


def test_sentetik_maliyet_reddi():
    s = SentetikStrateji(CFG, [opp(gross=10.0)])
    d = UnifiedDecisionEngine(no_policy_cfg(), strategies=[s]).decide([mctx()], NOW)[0]
    assert d.decision is Decision.REJECT and "MALIYET_SONRASI_AVANTAJ_YOK" in d.reason_codes


def test_sentetik_yalniz_golge_yetkisi():
    s = SentetikStrateji(CFG, [opp(authority=StrategyAuthority.SHADOW_ONLY)], authority=StrategyAuthority.SHADOW_ONLY)
    d = UnifiedDecisionEngine(no_policy_cfg(), strategies=[s]).decide([mctx()], NOW)[0]
    assert d.decision is Decision.SHADOW and "YETKI_GOLGE" in d.reason_codes


def test_sentetik_tek_kirilim_yarim_boyut():
    s = SentetikStrateji(CFG, [opp(strategy="controlled_breakout")], name="controlled_breakout")
    d = UnifiedDecisionEngine(no_policy_cfg(), strategies=[s]).decide([mctx()], NOW)[0]
    assert d.decision is Decision.REDUCE_SIZE and d.risk_proposal["boyut_carpani"] == 0.5
    t = SentetikStrateji(CFG, [opp(strategy="swing_trend", gross=300)], name="swing_trend")
    d2 = UnifiedDecisionEngine(no_policy_cfg(), strategies=[s, t]).decide([mctx()], NOW)[0]
    assert d2.decision is Decision.OPEN                                    # trend teyidi varsa tam boyut


def test_sentetik_celiski_rejimle_cozulur():
    L = SentetikStrateji(CFG, [opp(side="long", strategy="L")], name="L")
    S = SentetikStrateji(CFG, [opp(side="short", strategy="S", gross=900)], name="S")
    ds = {d.side.value: d for d in UnifiedDecisionEngine(no_policy_cfg(), strategies=[L, S]).decide([mctx()], NOW)}
    assert ds["long"].decision is Decision.OPEN                            # boğa: long kazanır (net avantaj düşük olsa da)
    assert ds["short"].decision is Decision.REJECT and "CELISKI_KAYBETTI" in ds["short"].reason_codes
    yatay = {d.side.value: d for d in UnifiedDecisionEngine(no_policy_cfg(), strategies=[L, S]).decide(
        [mctx(w_trend="yatay")], NOW)}
    assert yatay["long"].decision is Decision.SHADOW and "CELISKI_REJIM_BELIRSIZ" in yatay["short"].reason_codes


def test_sentetik_hatali_strateji_digerlerini_durdurmaz():
    bad = SentetikStrateji(CFG, [], name="bozuk", boom=True)
    good = SentetikStrateji(CFG, [opp()], name="iyi")
    eng = UnifiedDecisionEngine(no_policy_cfg(), strategies=[bad, good])
    ds = eng.decide([mctx()], NOW)
    assert len(ds) == 1 and eng.errors and "bozuk" in eng.errors[0]


def test_sentetik_idempotency_birincilden():
    o = opp()
    d = UnifiedDecisionEngine(no_policy_cfg(), strategies=[SentetikStrateji(CFG, [o])]).decide([mctx()], NOW)[0]
    assert d.idempotency_key == o.idempotency_key


def test_cikis_kurali_turkcesi():
    assert "50 günlük" in exit_rule_tr({"type": "ma", "n": 50})
    assert exit_rule_tr(None) is None and exit_rule_tr({"type": "?"}) is None


def test_politika_motoru_hatasi_fail_closed(monkeypatch):
    from radar import policy

    def broken_gate(*args, **kwargs):
        raise RuntimeError("sentetik politika hatası")

    monkeypatch.setattr(policy, "gate", broken_gate)
    eng = UnifiedDecisionEngine(CFG, strategies=[SwingTrendStrategy(CFG)])
    ds = eng.decide([mctx()], NOW)
    assert ds and ds[0].decision is Decision.SHADOW
    assert any(code.startswith("POLITIKA:POLITIKA_HATASI") for code in ds[0].reason_codes)
    assert any("sentetik politika hatası" in error for error in eng.errors)


def test_channel_coverage_failure_blocks_only_its_strategy_horizon():
    context = mctx()
    swing_ok = context.coverage_for(Horizon.SWING)
    intraday_bad = FactorCoverage(
        expected_total=6, present=5, required_total=6, required_ok=5,
        coverage_ratio=5 / 6, min_ratio=0.8, required_missing=("close_1H",),
        sufficient_for_paper=False, reason_codes=("ZORUNLU_EKSIK:close_1H",),
    )
    context = replace(context, coverage_by_horizon={
        Horizon.SWING: swing_ok, Horizon.POSITION: swing_ok,
        Horizon.INTRADAY: intraday_bad,
    })
    swing = opp(strategy="swing-channel")
    intraday = replace(opp(strategy="intraday-channel"), horizon=Horizon.INTRADAY, timeframe="1H")
    strategy = SentetikStrateji(CFG, [swing, intraday], name="two-horizon")
    decisions = {d.strategy: d for d in UnifiedDecisionEngine(
        no_policy_cfg(), strategies=[strategy]).decide([context], NOW)}
    assert decisions["swing-channel"].decision is Decision.OPEN
    assert decisions["intraday-channel"].decision is Decision.SHADOW
    assert "ZORUNLU_EKSIK:close_1H" in decisions["intraday-channel"].reason_codes
