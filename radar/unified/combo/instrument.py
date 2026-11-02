"""OKX public perpetual instrument metadata; no authentication/private endpoint."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class OKXInstrumentMetadata:
    inst_id: str
    contract_type: str
    contract_value: float
    contract_value_ccy: str
    tick_size: float
    lot_size_contracts: float
    min_size_contracts: float
    state: str

    def __post_init__(self) -> None:
        values = (
            self.contract_value, self.tick_size,
            self.lot_size_contracts, self.min_size_contracts,
        )
        if not all(math.isfinite(float(x)) and float(x) > 0 for x in values):
            raise ValueError("OKX kontrat metadata sayıları sonlu ve pozitif olmalı")
        if self.contract_type != "linear" or self.contract_value_ccy.upper() != "BTC":
            raise ValueError("COMBO yalnız BTC bazlı linear BTC-USDT perpetual kabul eder")
        if self.state != "live":
            raise ValueError(f"OKX enstrüman canlı değil: {self.state}")

    def contracts_to_btc(self, contracts: float) -> float:
        if not math.isfinite(float(contracts)) or contracts < 0:
            raise ValueError("Kontrat miktarı sonlu ve negatif olmayan değer olmalı")
        return float(contracts) * self.contract_value

    def btc_to_contracts(self, qty_btc: float) -> float:
        """Gelecekteki borsa adaptörü için; bu PAPER modülü bu miktarı borsaya göndermez."""
        if not math.isfinite(float(qty_btc)) or qty_btc <= 0:
            raise ValueError("BTC miktarı pozitif ve sonlu olmalı")
        contracts = qty_btc / self.contract_value
        steps = contracts / self.lot_size_contracts
        if contracts + 1e-12 < self.min_size_contracts or not math.isclose(
            steps, round(steps), rel_tol=0.0, abs_tol=1e-8
        ):
            raise ValueError("BTC miktarı OKX kontrat minSz/lotSz kuralına uymuyor")
        return contracts

    @classmethod
    def from_record(cls, record: dict) -> "OKXInstrumentMetadata":
        return cls(
            inst_id=str(record.get("instId") or ""),
            contract_type=str(record.get("ctType") or ""),
            contract_value=float(record.get("ctVal") or 0),
            contract_value_ccy=str(record.get("ctValCcy") or ""),
            tick_size=float(record.get("tickSz") or 0),
            lot_size_contracts=float(record.get("lotSz") or 0),
            min_size_contracts=float(record.get("minSz") or 0),
            state=str(record.get("state") or ""),
        )

    @classmethod
    def fetch_public(cls, inst_id: str, *, timeout_s: float = 8.0) -> "OKXInstrumentMetadata":
        """GET /api/v5/public/instruments; requires no API key or private account access."""
        query = urlencode({"instType": "SWAP", "instId": inst_id})
        url = f"https://www.okx.com/api/v5/public/instruments?{query}"
        request = Request(url, headers={"User-Agent": "kripto-radar-combo-paper/1"})
        try:
            with urlopen(request, timeout=timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"OKX public instrument metadata alınamadı: {type(exc).__name__}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("code") != "0":
            raise RuntimeError("OKX public instrument metadata cevabı başarısız")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != 1:
            raise RuntimeError(f"OKX enstrüman kaydı tekil değil: {inst_id}")
        info = cls.from_record(data[0])
        if info.inst_id != inst_id:
            raise RuntimeError("OKX metadata enstrüman kimliği beklenen sembolle uyuşmuyor")
        return info
