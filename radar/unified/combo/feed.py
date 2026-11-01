"""Mevcut OKX public WebSocket Feed'ine bağlanan COMBO ticker dinleyicisi."""
from __future__ import annotations

import math
from typing import Any

from radar import ws
from .config import ComboConfig
from .instrument import OKXInstrumentMetadata
from .models import ComboQuote
from .strategy import ComboStrategy


class ComboWebSocketListener:
    """Public-only feed; BBO kontrat miktarını doğrulanmış ctVal ile BTC'ye çevirir."""

    def __init__(self, strategy: ComboStrategy, feed=None,
                 instrument_meta: OKXInstrumentMetadata | None = None):
        self.strategy = strategy
        self.feed = feed if feed is not None else ws.OKXFeed()
        self.owns_feed = feed is None
        # Ağdan metadata almak yalnız public endpoint kullanır. Başarısızsa fail-closed.
        self.instrument_meta = instrument_meta or OKXInstrumentMetadata.fetch_public(strategy.cfg.symbol)
        if self.instrument_meta.inst_id != strategy.cfg.symbol:
            raise ValueError("OKX metadata sembolü COMBO sembolüyle uyuşmuyor")
        if not math.isclose(self.instrument_meta.tick_size, strategy.cfg.price_tick,
                            rel_tol=0.0, abs_tol=1e-10):
            raise ValueError(
                f"COMBO tick ayarı {strategy.cfg.price_tick} OKX tickSz "
                f"{self.instrument_meta.tick_size} ile uyuşmuyor"
            )
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.feed.add_tick_listener(self._on_tick_event)

    def _on_tick_event(self, tick: dict[str, Any]) -> None:
        if tick.get("exchange") != "okx" or tick.get("inst") != self.strategy.cfg.symbol:
            return
        try:
            if tick.get("size_unit") != "contracts_for_swap":
                raise ValueError("OKX SWAP BBO miktar birimi doğrulanmadı")
            bid_contracts = float(tick["bid_size_contracts"])
            ask_contracts = float(tick["ask_size_contracts"])
            bid_btc = self.instrument_meta.contracts_to_btc(bid_contracts)
            ask_btc = self.instrument_meta.contracts_to_btc(ask_contracts)
            quote = ComboQuote(
                symbol=tick["inst"], last=float(tick["last"]), bid=float(tick["bid"]),
                ask=float(tick["ask"]), ts_ms=int(tick["ts_ms"]),
                bid_size=bid_btc, ask_size=ask_btc,
            )
            self.last_result = self.strategy.on_quote(quote)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"

    def start(self) -> None:
        self.feed.set_symbols([self.strategy.cfg.symbol])
        if self.owns_feed and not self.feed.is_alive():
            self.feed.start()

    def stop(self) -> None:
        if self.owns_feed:
            self.feed.stop()

    def status(self) -> dict[str, Any]:
        return {
            "feed": self.feed.status(),
            "owns_feed": self.owns_feed,
            "instrument_metadata": {
                "inst_id": self.instrument_meta.inst_id,
                "contract_value": self.instrument_meta.contract_value,
                "contract_value_ccy": self.instrument_meta.contract_value_ccy,
                "tick_size": self.instrument_meta.tick_size,
                "lot_size_contracts": self.instrument_meta.lot_size_contracts,
                "min_size_contracts": self.instrument_meta.min_size_contracts,
                "state": self.instrument_meta.state,
            },
            "last_result": self.last_result,
            "last_error": self.last_error,
            "execution_mode": "PAPER_ONLY",
            "private_api_enabled": False,
        }
