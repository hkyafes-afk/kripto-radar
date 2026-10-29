"""Araştırma paneli: Binance Vision USDT-M günlük verisi (batanlar dahil) → geniş tablolar + özellikler.

Çıktı: panel.pkl  (dict: open, high, low, close, qv, vol, taker, trades, fund_d, info)
Kripto dışı kontratlar (hisse, emtia, endeks, stabil) ayıklanır: açık liste + hafta sonu davranışı testi
(hisse/emtia dayanağı hafta sonu kapalı → hafta sonu günlük hareketi hafta içinin çok altında kalır).
"""
import glob
import gzip
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd

S = sys.argv[1]
OUT = sys.argv[2]

# Açıkça kripto olmayan / endeks / stabil (Binance'in TradFi ve endeks vadelileri)
EXPLICIT = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "BTCDOMUSDT", "DEFIUSDT", "FOOTBALLUSDT",
            "BLUEBIRDUSDT", "XAUUSDT", "XAUTUSDT", "XAGUSDT", "XPTUSDT", "XPDUSDT", "PAXGUSDT", "MSFTUSDT", "TSLAUSDT",
            "AAPLUSDT", "NVDAUSDT", "AMZNUSDT", "GOOGLUSDT", "METAUSDT", "COINUSDT", "MSTRUSDT", "HOODUSDT",
            "CRCLUSDT", "RKLBUSDT", "HYUNDAIUSDT", "TENCENTUSDT", "WMTUSDT", "COSTUSDT", "MDBUSDT", "GTLBUSDT",
            "RDDTUSDT", "POPMARTUSDT", "CXMTUSDT", "BRKBUSDT", "PLTRUSDT", "AMDUSDT", "NFLXUSDT", "ORCLUSDT",
            "INTCUSDT", "BABAUSDT", "SPYUSDT", "QQQUSDT", "CLUSDT", "NATGASUSDT", "COPPERUSDT", "EWTUSDT", "VSTUSDT"}

cols = ["open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_volume"]
data = {c: {} for c in cols}
for f in sorted(glob.glob(f"{S}/um_1d/*.csv.gz")):
    sym = os.path.basename(f)[:-7]
    df = pd.read_csv(f)
    idx = pd.to_datetime(df["ts_ms"], unit="ms", utc=True).dt.normalize()
    df.index = idx
    df = df[~df.index.duplicated(keep="last")]
    for c in cols:
        data[c][sym] = df[c].astype(float)

W = {c: pd.DataFrame(v).sort_index() for c, v in data.items()}
# Zombi satırlar: listeden çıkan coinler için arşiv, çıkıştan sonra da fiyatı donmuş, hacmi sıfır satır üretiyor.
# Hacmi ya da işlem sayısı sıfır olan gün = işlem yok (NaN). Böylece çıkış günü ve batan coinler doğru bulunur.
dead = (W["quote_volume"].fillna(0) <= 0) | (W["trades"].fillna(0) <= 0)
zombie_cells = int((dead & W["close"].notna()).sum().sum())
for c in W:
    W[c] = W[c].mask(dead)
close = W["close"]
ret = np.log(close).diff()

# Hafta sonu testi: hisse/emtia dayanaklı kontratlarda Cmt/Paz hareketi hafta içinin çok altında
wk = ret.index.dayofweek
absr = ret.abs()
we = absr[wk >= 5].mean()
wd = absr[wk < 5].mean()
ratio = (we / wd)
nobs = ret.notna().sum()
weekend_flag = set(ratio[(ratio < 0.45) & (nobs >= 90)].index)
stable_flag = set(absr.median()[absr.median() < 0.002].index)
excluded = sorted((EXPLICIT & set(close.columns)) | weekend_flag | stable_flag)
keep = [c for c in close.columns if c not in excluded]

for c in W:
    W[c] = W[c][keep]

# Funding: 8s/4s/1s kesimlerin günlük toplamı (long'un o gün ödediği oran)
fund = {}
for f in sorted(glob.glob(f"{S}/funding/*.csv.gz")):
    sym = os.path.basename(f)[:-7]
    if sym not in keep:
        continue
    d = pd.read_csv(f)
    t = pd.to_datetime(d["ts_ms"], unit="ms", utc=True).dt.floor("D")
    fund[sym] = d.groupby(t)["rate"].sum()
fund_d = pd.DataFrame(fund).reindex(index=W["close"].index, columns=keep)

last_active = W["close"].apply(lambda col: col.last_valid_index())
end = W["close"].index[-1]
delisted = sorted(s for s, t in last_active.items() if t is not None and (end - t).days > 3)
info = {"zombi_hucre": zombie_cells, "batan": len(delisted), "batanlar": delisted, "semboller": len(keep), "ayiklanan": excluded, "hafta_sonu_orani": {k: round(float(ratio[k]), 2) for k in weekend_flag},
        "gun": len(W["close"]), "ilk": str(W["close"].index[0].date()), "son": str(W["close"].index[-1].date())}
pickle.dump({"open": W["open"], "high": W["high"], "low": W["low"], "close": W["close"], "qv": W["quote_volume"],
             "vol": W["volume"], "taker": W["taker_buy_volume"], "trades": W["trades"], "fund_d": fund_d, "info": info},
            open(OUT, "wb"))
print(json.dumps({k: v for k, v in info.items()}, ensure_ascii=False)[:3000])
