"""v9 uzman ajanlar: her yöneticinin altında 5 uzman (anayasa, PLAN.md 9.8).

Her uzman tam bir ajandır:
  1) Birden çok fonksiyon (kendi alanının bütün araçları)
  2) Kendi görüşü: yön + güç + gerekçe → yöneticiye "büyüt / aynen / küçült / girme" önerisi (boyut çarpanı)
  3) Kendi karnesi: her görüş işlem kapanınca sonuçla karşılaştırılır. Katkı = (önerilen çarpan − 1) × R.
     Büyüt dediği işlem kazandıysa ya da küçült dediği işlem kaybettiyse katkı artıdır.
  4) Kendi öğrenmesi: Analist-Hakem'in modeli her gece yeniden eğitilir (radar/hakem.py); kurallı uzmanların eşikleri
     yarışta/öğrenmede sınanır.
  5) Etki ağırlığı: kanıtlanmamış uzmanın sözü yarım sayılır (0,5). Karnesi artıysa ağırlığı 1,5'e kadar büyür,
     eksiyse sıfıra iner (Bayes büzülmesi: tek işlem ağırlığı az, çok işlem çok oynatır).
Gölge işlemler de karneye girer (öğrenme yavaşlamaz).

Roller: Gözcü (fırsat + göreli güç), Balina Gözü (kalabalık: OI, fonlama, long/short, likidasyon, akış),
Analist-Hakem ("tutar mı?" modeli + otopsi), Risk Sorumlusu (boyut/kaldıraç/tavan; masanın kuralları),
Trader (emir tipi, kayma, pozisyon yönetimi). İlk üçü boyuta görüş verir; son ikisi kural uygular ve karnesi
uygulama kalitesidir (kayma, maker oranı, planlanan 1R'yi aşan kayıp).
"""
from __future__ import annotations

ROLES = ("gozcu", "balina", "hakem", "risk", "trader")
ROLE_NAME = {"gozcu": "Gözcü", "balina": "Balina Gözü", "hakem": "Analist-Hakem", "risk": "Risk Sorumlusu",
             "trader": "Trader"}
SIZERS = ("gozcu", "balina", "hakem")
STYLE = {"kemal": "trend", "kaan": "trend", "selin": "trend", "murat": "trend", "ortak": "trend", "ayse": "donus",
         "deniz": "kisa", "elif": "carry"}
W0, W_MIN, W_MAX = 0.5, 0.0, 1.5
MULT_MIN, MULT_MAX = 0.4, 1.5


def _op(role: str, mult: float, reasons: list[str], data: dict | None = None) -> dict:
    mult = float(min(max(mult, 0.5), 1.4))
    act = "büyüt" if mult > 1.02 else "küçült" if mult < 0.98 else "aynen"
    return {"role": role, "mult": round(mult, 3), "action": act, "reasons": reasons, "data": data or {}}


def _num(x):
    return float(x) if isinstance(x, (int, float)) and x == x else None


# ── Gözcü ────────────────────────────────────────────────────────────
def gozcu(ctx: dict, idea: dict, model: dict | None = None) -> dict:
    """Fırsatı yöneticinin kuralıyla bulan uzman; ek olarak BTC'ye göre 30 günlük göreli gücü yorumlar. Yorumun yönü
    varsayım değil, kanıt: Analist-Hakem eğitimi her gece 5 yıllık işlemlerde göreli güç dilimlerine göre ort. R'yi ölçer
    (hakem.json → gozcu.mode). 'momentum' = güçlüde büyüt, 'ters' = güçlüde küçült (aşırı uzamış), 'nötr' = dokunma."""
    sgn = 1 if idea["side"] == "long" else -1
    style = STYLE.get(idea["mgr"], "trend")
    rank = _num(ctx.get("rs_rank"))
    gz = (model or {}).get("gozcu") or {}
    mode = gz.get("mode", "nötr")
    m, why = 1.0, []
    if rank is not None and style == "trend":
        r = rank if sgn > 0 else 1 - rank
        band = "en güçlü %20" if r >= 0.8 else "zayıf %40" if r < 0.4 else "orta"
        ev = f" (5 yıllık kanıt: güçlü {gz.get('top_r')}R, zayıf {gz.get('bot_r')}R)" if gz.get("top_r") is not None else ""
        if mode == "momentum" and r >= 0.8:
            m *= 1.15
            why.append(f"göreli güçte {band}: güçlü coinler devam ediyor{ev}")
        elif mode == "momentum" and r < 0.4:
            m *= 0.85
            why.append(f"göreli güçte {band}: zayıf coinde trend az tutuyor{ev}")
        elif mode == "ters" and r >= 0.8:
            m *= 0.85
            why.append(f"göreli güçte {band}: aşırı uzamış, geçmişte daha kötü sonuç{ev}")
        elif mode == "ters" and r < 0.4:
            m *= 1.1
            why.append(f"göreli güçte {band}: geçmişte daha iyi sonuç{ev}")
        else:
            why.append(f"göreli güç {band}; kanıt bu dilimde fark göstermiyor{ev}")
    elif rank is not None:
        why.append("göreli güç bu tarzda belirleyici değil")
    else:
        why.append("göreli güç verisi yok")
    return _op("gozcu", m, why, {"rs_rank": rank})


# ── Balina Gözü ──────────────────────────────────────────────────────
def balina(ctx: dict, idea: dict, crowd: dict | None = None) -> dict:
    """Kalabalık nerede? Açık pozisyon (OI), fonlama, hesap ve büyük hesap long/short oranı, son 1 saatin likidasyonları,
    alıcı/satıcı akışı. Trend tarzında 'yeni para giriyor mu', dönüş tarzında 'gerçek kapitülasyon mu' sorusu."""
    sgn = 1 if idea["side"] == "long" else -1
    style = STYLE.get(idea["mgr"], "trend")
    oi, chg, fa, imb = (_num(ctx.get(k)) for k in ("oi_chg24", "chg24", "funding_ann", "taker_imb"))
    cr = crowd or {}
    ls_a, ls_t = _num(cr.get("ls_acct")), _num(cr.get("ls_top"))
    lq = cr.get("liq_1h") or {}
    l_long, l_short = float(lq.get("long_usd") or 0), float(lq.get("short_usd") or 0)
    m, why = 1.0, []
    if style == "donus":
        burn = (l_long if sgn > 0 else l_short)
        conf = 0
        if burn >= 1_000_000:
            conf += 1
            why.append(f"son 1 saatte {burn / 1e6:.1f}M$ {'long' if sgn > 0 else 'short'} likidasyonu (zincirleme tasfiye)")
        if fa is not None and ((fa < 0) if sgn > 0 else (fa > 30)):
            conf += 1
            why.append(f"fonlama kalabalığın ters döndüğünü gösteriyor (yıllık %{fa:.0f})")
        if oi is not None and oi < -5:
            conf += 1
            why.append(f"açık pozisyon %{oi:.0f} eridi")
        m = {0: 0.7, 1: 0.9, 2: 1.1, 3: 1.3}[conf]
        if conf == 0:
            why.append("kapitülasyon izi yok (likidasyon, fonlama, OI teyit etmiyor)")
        return _op("balina", m, why, {"oi": oi, "fa": fa, "liq_long": l_long, "liq_short": l_short})
    if oi is not None and chg is not None:
        if oi > 3 and sgn * chg > 0:
            m *= 1.15
            why.append(f"yeni para trend yönünde giriyor (OI +%{oi:.0f}, fiyat {chg:+.1f}%)")
        elif oi < -3 and sgn * chg > 0:
            m *= 0.75
            why.append(f"fiyat gidiyor ama açık pozisyon eriyor (OI %{oi:.0f}): hareket ters tarafın kapanmasından, yakıt az")
    if fa is not None:
        if (sgn > 0 and fa > 50) or (sgn < 0 and fa < -30):
            m *= 0.75
            why.append(f"kalabalık zaten bu tarafta (fonlama yıllık %{fa:.0f})")
        elif (sgn > 0 and fa < -10) or (sgn < 0 and fa > 40):
            m *= 1.1
            why.append(f"kalabalık ters tarafta, sıkışma yakıtı (fonlama yıllık %{fa:.0f})")
    if ls_a is not None and ls_t is not None:
        retail_crowded = (ls_a > 2.5 and ls_t < ls_a * 0.7) if sgn > 0 else (ls_a < 0.6 and ls_t > ls_a * 1.4)
        whales_with = (ls_t > 1.5 and ls_t > ls_a) if sgn > 0 else (ls_t < 0.7 and ls_t < ls_a)
        if retail_crowded:
            m *= 0.85
            why.append(f"küçük hesaplar bu tarafa yığılmış (L/S {ls_a:.2f}), büyük hesaplar değil ({ls_t:.2f})")
        elif whales_with:
            m *= 1.1
            why.append(f"büyük hesaplar bu tarafta (top trader L/S {ls_t:.2f})")
    opp = l_short if sgn > 0 else l_long
    own = l_long if sgn > 0 else l_short
    if opp >= 500_000 and opp > 2 * own:
        m *= 1.05
        why.append(f"karşı taraf likide oluyor ({opp / 1e6:.1f}M$/saat)")
    if imb is not None and sgn * imb > 0.1:
        m *= 1.05
        why.append("alıcı/satıcı akışı aynı yönde")
    hl = cr.get("hl") or {}
    if (hl.get("n_long", 0) + hl.get("n_short", 0)) >= 3 and hl.get("net") is not None:
        net = sgn * float(hl["net"])
        if net >= 0.4:
            m *= 1.1
            why.append(f"Hyperliquid'de kârlı cüzdanlar bu tarafta ({hl.get('n_long', 0)} long / {hl.get('n_short', 0)} short)")
        elif net <= -0.4:
            m *= 0.9
            why.append(f"Hyperliquid'de kârlı cüzdanlar ters tarafta ({hl.get('n_long', 0)} long / {hl.get('n_short', 0)} short)")
    if not why:
        why.append("kalabalık nötr")
    return _op("balina", m, why, {"oi": oi, "fa": fa, "ls_acct": ls_a, "ls_top": ls_t, "liq_long": l_long,
                                  "liq_short": l_short, "imb": imb})


# ── Analist-Hakem ────────────────────────────────────────────────────
def hakem(idea: dict, model: dict | None) -> dict:
    """'Bu sinyal tutar mı?' Benzer koşullardaki geçmiş işlemlerden öğrenilmiş model (meta-labeling). Model doğrulamada
    kendini kanıtlamadıysa söz hakkı yok (aynen), sadece tahminini yazar."""
    feat = idea.get("feat") or {}
    if not model or not model.get("groups"):
        return _op("hakem", 1.0, ["model henüz eğitilmedi (her gece eğitilir)"])
    g = model["groups"].get(idea["mgr"]) or model["groups"].get("_hepsi")
    if not g:
        return _op("hakem", 1.0, ["bu yönetici için yeterli geçmiş yok"])
    x = []
    for k, mu, sd in zip(model["features"], g["mean"], g["sd"]):
        v = feat.get(k)
        v = mu if v is None else float(v)
        x.append((v - mu) / sd if sd > 0 else 0.0)
    pred = g["b0"] + sum(c * v for c, v in zip(g["coef"], x))
    z = (pred - g["pred_mean"]) / g["pred_sd"] if g.get("pred_sd") else 0.0
    m = 1.0 + max(-0.5, min(0.5, 0.25 * z))
    tag = f"beklenen {pred:+.2f}R (ortalama {g['pred_mean']:+.2f}R)"
    if not g.get("validated"):
        return _op("hakem", 1.0, [tag + "; model doğrulamada henüz kanıtlanmadı → söz hakkı yok"], {"pred": pred, "z": z})
    return _op("hakem", m, [tag + (" → benzer koşullarda geçmiş daha iyiydi" if z > 0.3 else
                                   " → benzer koşullarda geçmiş daha kötüydü" if z < -0.3 else "")],
               {"pred": round(pred, 3), "z": round(z, 2)})


# ── birleştirme, karne ve ağırlık ─────────────────────────────────────
def weight(st: dict, mgr: str, role: str) -> float:
    rec = (st.get("experts") or {}).get(f"{mgr}|{role}") or {}
    return float(rec.get("w", W0))


def combine(opinions: list[dict], st: dict, mgr: str) -> tuple[float, list[dict]]:
    m = 1.0
    out = []
    for o in opinions:
        if o["role"] not in SIZERS:
            out.append(o)
            continue
        w = weight(st, mgr, o["role"])
        eff = 1.0 + w * (o["mult"] - 1.0)
        m *= eff
        out.append({**o, "w": round(w, 2), "eff": round(eff, 3)})
    return float(min(max(m, MULT_MIN), MULT_MAX)), out


def score_closed(st: dict, trades: list[dict]) -> list[str]:
    """Kapanan işlemlerle uzman karnelerini günceller. Döner: önemli ağırlık değişimleri (bildirim)."""
    E = st.setdefault("experts", {})
    msgs = []
    for t in trades:
        ops = t.get("experts") or []
        r = t.get("r")
        if r is None or t.get("expert_scored"):
            continue
        rr = max(-1.5, min(3.0, float(r)))
        for o in ops:
            if o.get("role") not in SIZERS:
                continue
            key = f"{t['mgr']}|{o['role']}"
            rec = E.setdefault(key, {"n": 0, "contrib": 0.0, "agree": 0, "w": W0})
            c = (float(o.get("mult", 1.0)) - 1.0) * rr
            rec["n"] += 1
            rec["contrib"] += c
            if (o.get("mult", 1) > 1.02 and rr > 0) or (o.get("mult", 1) < 0.98 and rr <= 0):
                rec["agree"] += 1
            n = rec["n"]
            mean = rec["contrib"] / n
            old = rec["w"]
            shrink = n / (n + 30.0)
            rec["w"] = round(min(W_MAX, max(W_MIN, W0 + shrink * max(-0.5, min(1.0, mean * 10)))), 3)
            rec["mean"] = round(mean, 4)
            if n >= 30 and (rec["w"] >= 1.0 > old or rec["w"] <= 0.2 < old):
                msgs.append(f"🧑‍💼 <b>Uzman karnesi</b>: {t['mgr']} · {ROLE_NAME[o['role']]} ağırlığı {old:.2f} → {rec['w']:.2f} "
                            f"({n} işlem, işlem başına katkı {mean:+.3f}R)")
        exe = E.setdefault(f"{t['mgr']}|trader", {"n": 0, "slip_bp": 0.0, "maker": 0})
        exe["n"] += 1
        exe["slip_bp"] += float(t.get("slip_bp") or 0) + float(t.get("stop_slip_bp") or 0)
        exe["maker"] += 1 if "maker" in str(t.get("entry_src") or "") else 0
        rk = E.setdefault(f"{t['mgr']}|risk", {"n": 0, "over_1r": 0, "worst_r": 0.0})
        rk["n"] += 1
        if rr < -1.05:
            rk["over_1r"] += 1
        rk["worst_r"] = round(min(rk["worst_r"], float(r)), 3)
        t["expert_scored"] = True
    return msgs


def report(st: dict) -> dict:
    E = st.get("experts") or {}
    out = {}
    for key, rec in E.items():
        mgr, role = key.split("|")
        n = rec.get("n", 0)
        row = {"n": n, "role": ROLE_NAME.get(role, role)}
        if role in SIZERS:
            row.update({"w": rec.get("w", W0), "mean": rec.get("mean"),
                        "agree_pct": round(rec.get("agree", 0) / n * 100, 1) if n else None})
        elif role == "trader":
            row.update({"slip_bp": round(rec.get("slip_bp", 0) / n, 1) if n else None,
                        "maker_pct": round(rec.get("maker", 0) / n * 100, 1) if n else None})
        elif role == "risk":
            row.update({"over_1r_pct": round(rec.get("over_1r", 0) / n * 100, 1) if n else None, "worst_r": rec.get("worst_r")})
        out.setdefault(mgr, {})[role] = row
    return out


def kelly_mult(desk, mid: str, min_n: int = 30) -> tuple[float, str]:
    """Genel Müdür · Sermaye Dağıtıcı: çeyrek Kelly. 30+ gerçek işlemden sonra yöneticinin riski kanıtına göre
    0,5×–1,5× arası ayarlanır (kazanan büyür, kötüleşen küçülür). Öncesinde 1×."""
    m = desk.by_id.get(mid)
    if not m or m.kind != "directional":
        return 1.0, ""
    cl = [t for t in desk.ledger["closed"] if t.get("mgr") == mid and not t.get("shadow") and t.get("r") is not None]
    if len(cl) < min_n:
        return 1.0, ""
    rs = [float(t["r"]) for t in cl[-200:]]
    wins = [r for r in rs if r > 0]
    loss = [-r for r in rs if r <= 0]
    if not wins or not loss:
        return 1.0, ""
    p = len(wins) / len(rs)
    b = (sum(wins) / len(wins)) / (sum(loss) / len(loss))
    f = p - (1 - p) / b                       # Kelly oranı (1R risk birimi başına)
    q = max(0.0, f / 4) * 100                 # çeyrek Kelly, sermayenin %'si
    base = (m.base_risk + m.max_risk) / 2
    mult = float(min(1.5, max(0.5, q / base))) if base > 0 else 1.0
    return mult, f"çeyrek Kelly: isabet %{p * 100:.0f}, kazanç/kayıp {b:.2f} → risk ×{mult:.2f} ({len(rs)} işlem)"


def summary_line(ops: list[dict]) -> str:
    """Panel için tek satır özet."""
    parts = []
    for o in ops:
        if o["role"] in SIZERS:
            parts.append(f"{ROLE_NAME[o['role']]} {o['action']}" + (f" ×{o['mult']:.2f}" if o["action"] != "aynen" else ""))
    return " · ".join(parts)
