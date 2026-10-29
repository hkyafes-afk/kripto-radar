"""Binance Vision arşivinden test verisi (tools/binance_arsiv.py çıktısı: `binance-veri` dalı).

OKX yerine bu veriyle yarış çalıştırmak için bt2.load_data ile aynı biçimde günlük mum döndürür, ayrıca:
  - vol[sym]  : son 30 günün ortalama kote hacmi (Series) → "vol24_usd" bağlamı, gün gün
  - elig[sym] : o gün, son 30 günlük hacme göre canlı coinler arasında ilk `top_n` içinde mi (bool Series)
  - ages[sym] : coinin arşivdeki ilk günü (kanıt sınıfı A/B/C için)
Evren BATAN COİNLERİ de içerir ve gün gün kurulur (point-in-time): bir coin yalnızca listeliyken ve o günkü hacim sırası
yeterliyken işlem sinyali üretebilir. Böylece bugünün en likit coinlerini seçmenin yarattığı hayatta kalma yanlılığı kalkar.
"""
from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pandas as pd

TRAIL = 30


def _sym(name: str) -> str:
    return name[:-4] if name.endswith("USDT") else name


def _read(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt") as fh:
        df = pd.read_csv(fh)
    df.index = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # Zombi satırlar: arşiv, listeden çıkan coinler için çıkıştan sonra da fiyatı donmuş, hacmi/işlem sayısı sıfır satır
    # üretiyor (ör. FTT 2022-11'den bugüne). Bunlar işlem günü değil: atılır, böylece çıkış günü ve batanlar doğru bulunur.
    live = (df["quote_volume"].astype(float) > 0) & (df["trades"].astype(float) > 0 if "trades" in df else True)
    out = df.loc[live, ["open", "high", "low", "close", "volume", "quote_volume"]].astype(float)
    return out[(out["close"] > 0) & (out["high"] >= out["low"])]


# Kripto olmayan kontratlar (Binance TradFi hisse/emtia/endeks vadelileri ve stabil): açık liste + hafta sonu testi
NON_CRYPTO = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "BTCDOMUSDT", "DEFIUSDT", "FOOTBALLUSDT",
              "BLUEBIRDUSDT", "XAUUSDT", "XAUTUSDT", "XAGUSDT", "XPTUSDT", "XPDUSDT", "PAXGUSDT", "CLUSDT", "NATGASUSDT",
              "COPPERUSDT"}


def _weekend_ratio(df: pd.DataFrame) -> float:
    """Hafta sonu ort. |getiri| / hafta içi. Hisse/emtia dayanaklıda hafta sonu dayanak kapalı → oran çok düşük."""
    r = np.log(df["close"]).diff().abs()
    wk = r.index.dayofweek
    wd = r[wk < 5].mean()
    return float(r[wk >= 5].mean() / wd) if wd and wd == wd else 1.0


def load(veri_dir: str | Path, top_n: int = 100, min_days: int = 260, only: list[str] | None = None):
    """Döner: daily {sym: (inst, df, vol_series)}, elig {sym: bool Series}, ages {sym: Timestamp}, info dict."""
    root = Path(veri_dir) / "um_1d"
    files = sorted(root.glob("*.csv.gz"))
    frames = {}
    for f in files:
        s = _sym(f.name[: -len(".csv.gz")])
        if only and s not in only and s != "BTC":
            continue
        if f.name[: -len(".csv.gz")] in NON_CRYPTO:
            continue
        df = _read(f)
        if len(df) >= 90 and _weekend_ratio(df) < 0.45:          # hisse/emtia/endeks vadelisi
            continue
        if len(df) >= min_days or s == "BTC":
            frames[s] = df
    if "BTC" not in frames:
        raise RuntimeError("BTC verisi yok (hava durumu için gerekli)")
    qv = pd.DataFrame({s: d["quote_volume"] for s, d in frames.items()})
    trail = qv.rolling(TRAIL, min_periods=10).mean()
    alive = qv.notna()
    rank = trail.where(alive).rank(axis=1, ascending=False, method="first")
    elig_df = (rank <= top_n) & alive
    daily, elig, ages = {}, {}, {}
    for s, df in frames.items():
        daily[s] = (f"{s}-USDT-SWAP", df, trail[s].reindex(df.index))
        elig[s] = elig_df[s].reindex(df.index).fillna(False)
        ages[s] = df.index[0]
    last = max(d.index[-1] for d in frames.values())
    gone = [s for s, d in frames.items() if (last - d.index[-1]).days > 3]
    info = {"coin": len(frames), "batan": len(gone), "ilk_gun": str(min(d.index[0] for d in frames.values()).date()),
            "son_gun": str(last.date()), "top_n": top_n}
    return daily, elig, ages, info
