"""Mevcut OKX public WebSocket Feed'ine bağlanan COMBO ticker dinleyicisi."""
from __future__ import annotations

from typing import Any

from radar import ws
from .models import ComboQuote
from .strategy import ComboStrategy


class ComboWebSocketListener:
    """Özel anahtar kullanmaz; mevcut Feed'i enjekte edebilir veya yalnız public OKX feed açabilir."""

    def __init__(self, strategy: ComboStrategy, feed=None):
        self.strategy = strategy
        self.feed = feed if feed is not None else ws.OKXFeed()
        self.owns_feed = feed is None
        self.last_result: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.feed.add_tick_listener(self._on_tick_event)

    def _on_tick_event(self, tick: dict[str, Any]) -> None:
        if tick.get("exchange") != "okx" or tick.get("inst") != self.strategy.cfg.symbol:
            return
        try:
            quote = ComboQuote(
                symbol=tick["inst"], last=float(tick["last"]), bid=float(tick["bid"]),
                ask=float(tick["ask"]), ts_ms=int(tick["ts_ms"]),
                bid_size=tick.get("bid_size"), ask_size=tick.get("ask_size"),
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
            "last_result": self.last_result,
            "last_error": self.last_error,
            "execution_mode": "PAPER_ONLY",
            "private_api_enabled": False,
        }
