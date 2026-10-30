"""Günlük (swing) stratejiler: legacy yöneticilerin çekirdeğini sarmalar (GOREV.md "Eski ajan dönüşümü")."""
from __future__ import annotations

from ..models import Horizon, MarketContext, Opportunity, Regime, StrategyAuthority
from .base import LegacyManagerStrategy


class SwingTrendStrategy(LegacyManagerStrategy):
    """Kemal'in trend çekirdeği: 50 günlük ortalama + 20/100 trend filtresi, 3 ATR iz stop, trend bitince çıkış."""
    name = "swing_trend"
    manager_id = "kemal"
    horizon = Horizon.SWING
    timeframes = ("1D",)
    default_authority = StrategyAuthority.PAPER
    description = "Günlük trend takibi (Kemal çekirdeği)"


class CompressionBreakoutStrategy(LegacyManagerStrategy):
    """Selin'in sıkışma çekirdeği (TTM varyantı config'ten): volatilite sıkışmasından hacimli patlama."""
    name = "compression_breakout"
    manager_id = "selin"
    horizon = Horizon.SWING
    timeframes = ("1D",)
    default_authority = StrategyAuthority.PAPER
    description = "Sıkışmadan patlama (Selin çekirdeği)"


class ControlledBreakoutStrategy(LegacyManagerStrategy):
    """Kaan'ın kırılım çekirdeği. Tek başına ana sermaye motoru değil: karar motoru, aynı coin ve yönde trend ya da
    sıkışma fırsatı yoksa boyutu yarıya indirir (decision.py)."""
    name = "controlled_breakout"
    manager_id = "kaan"
    horizon = Horizon.SWING
    timeframes = ("1D",)
    default_authority = StrategyAuthority.PAPER
    description = "Kontrollü kırılım (Kaan çekirdeği)"


class PanicReversalStrategy(LegacyManagerStrategy):
    """Ayşe'nin seçici aşırılık yaklaşımı. Başlangıçta yalnız gölge. Basit "çok düştü al" değil: legacy çekirdek
    kapitülasyon mumu + hacim ister; burada ayrıca BTC çöküşü ve eksik mikro yapı kanıtı karşı kanıt olarak yazılır."""
    name = "panic_reversal"
    manager_id = "ayse"
    horizon = Horizon.SWING
    timeframes = ("1D",)
    default_authority = StrategyAuthority.SHADOW_ONLY
    description = "Panik dönüşü (Ayşe çekirdeği), gölgede"

    def extra_against(self, ctx: MarketContext, o: Opportunity) -> tuple[str, ...]:
        out = []
        if o.side.value == "long" and ctx.regime.regime is Regime.CRISIS:
            out.append("BTC kriz rejiminde (çöküşle çelişiyor)")
        if ctx.liquidity.spread_bp is None:
            out.append("spread normalleşmesi doğrulanamadı (anlık alış/satış verisi yok)")
        return tuple(out)


class LegacyMuratStrategy(LegacyManagerStrategy):
    """Murat: yalnız legacy gölge karşılaştırma (iki dönemde de ekibin en zayıfı)."""
    name = "legacy_murat"
    manager_id = "murat"
    horizon = Horizon.SWING
    timeframes = ("1D",)
    default_authority = StrategyAuthority.SHADOW_ONLY
    description = "Murat geri çekilme (legacy gölge karşılaştırma)"
