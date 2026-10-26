"""Telegram komut dinleyici (GitHub Actions'ta ~10 dakikada bir çalışır).

Telegram'ın getUpdates kuyruğunu okur, komutlara son tarama verisiyle cevap verir,
sonra offset ile kuyruğu onaylar (kalıcı offset saklamaya gerek kalmaz).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

from . import notify as nt
from . import store
from .config import load_config
from .signals import SETUP_LABELS

HELP = (
    "🛰 <b>Kripto Radar komutları</b>\n"
    "/durum — BTC rejimi ve en iyi sinyaller\n"
    "/sinyaller — tüm güçlü sinyaller\n"
    "/coin SOL — tek coin detayı\n"
    "/funding — funding harvest listesi\n"
    "/demo — demo hesap: bakiye, kâr/zarar\n"
    "/pozisyonlar — açık demo işlemler\n"
    "/ekip — 7 yöneticinin durumu\n"
    "/yonetici kaan — bir yöneticinin ayrıntısı ve son kararları\n"
    "/emirler — bekleyen limit emirler\n"
    "/haber — Haber Masası: kırmızı/sarı uyarılar, Korku-Açgözlülük\n"
    "/hava — piyasa havası ve olay takvimi (Fed, TÜFE)\n"
    "/test — 5 yıllık test sonuçları ve sermaye politikası\n"
    "/kapat SOL — demo işlemi elle kapat\n"
    "/borsalar — borsalar arası funding ve fiyat farkı\n"
    "/saglik — canlı veri ve bağlantı durumu\n"
    "/risk — risk durumu ve limitler\n"
    "/devam — devre kesiciyi kaldır\n"
    "/karne — setup ve periyot karnesi\n"
    "/tara — hemen yeni tarama başlat\n"
    "/sessiz 8 — 8 saat bildirimleri kapat\n"
    "/ac — bildirimleri aç\n\n"
    "<i>Bot canlı döngüdeyken cevaplar birkaç saniye içinde gelir.</i>"
)


def _age(ms) -> str:
    m = int((time.time() * 1000 - ms) / 60000)
    return f"{m} dk önce" if m < 60 else f"{m // 60} sa {m % 60} dk önce"


def cmd_durum(d: dict) -> str:
    b = d["btc"]
    sigs = [s for s in d["signals"] if s["score"] >= d["thresholds"]["strong"]]
    L = [f"📡 <b>Durum</b> · {_age(d['generated_at'])}",
         f"BTC {nt.fmt_price(b.get('price'))} ({(b.get('chg24') or 0):+.1f}%) · {b.get('regime_text', '')}",
         f"{d['counts']['scanned']} coin · {len(sigs)} güçlü sinyal · defter {len(d['ledger']['open'])} açık", ""]
    for s in sigs[:6]:
        L.append(f"{'🟢' if s['side'] == 'long' else '🔴'} <b>{nt.esc(s['sym'])}</b> {s['label']} {s['score']}"
                 f" · {nt.fmt_price(s['entry'])}")
    if not sigs:
        L.append("Şu an güçlü sinyal yok.")
    return "\n".join(L)


def cmd_sinyaller(d: dict) -> str:
    sigs = [s for s in d["signals"] if s["score"] >= d["thresholds"]["strong"]]
    if not sigs:
        return "Şu an eşik üstü sinyal yok."
    L = [f"🚦 <b>Güçlü sinyaller</b> ({len(sigs)}) · {_age(d['generated_at'])}"]
    for s in sigs[:15]:
        L.append(f"\n{'🟢' if s['side'] == 'long' else '🔴'} <b>{nt.esc(s['sym'])}</b> · {s['label']} · <b>{s['score']}</b>\n"
                 f"Giriş {nt.fmt_price(s['entry'])} · Stop {nt.fmt_price(s['sl'])} · Hedef {nt.fmt_price(s['tp'])}")
    return "\n".join(L)


def cmd_coin(d: dict, sym: str) -> str:
    sym = sym.upper().replace("USDT", "").replace("-", "").strip()
    c = next((x for x in d["coins"] if x["sym"] == sym), None)
    if not c:
        return f"{nt.esc(sym)} son taramada yok (ilk {d['counts']['universe']} likit coin taranıyor)."
    tr = lambda v: "▲" if v == 1 else "▼" if v == -1 else "•"
    L = [f"🔎 <b>{sym}</b> · {nt.fmt_price(c['price'])} ({(c.get('chg24') or 0):+.1f}%)",
         f"Trend 4S {tr(c['st'])} · günlük {tr(c['d_trend'])} · momentum {c['tsmom']:+.2f}",
         f"RSI {c['rsi']:.0f} · ADX {c['adx']:.0f} · Bollinger %B {c['bb_pct']:.2f}" if c.get("rsi") is not None else "",
         f"Funding {c['funding_ann']:.0f}%/yıl · OI 24s {(c.get('oi_chg24') or 0):+.1f}% · hacim ${c['vol24_usd'] / 1e6:.0f}M"
         if c.get("funding_ann") is not None else ""]
    sigs = [s for s in d["signals"] if s["sym"] == sym]
    for s in sigs:
        L.append(f"\n<b>{s['label']}</b> {s['score']}/100 (ham {s['raw']})")
        L += [f"<i>{nt.esc(r)}</i>" for r in s["reasons"][:6]]
    if not sigs:
        L.append("\nŞu an bu coinde aktif setup yok.")
    return "\n".join(x for x in L if x)


def cmd_funding(d: dict) -> str:
    H = d["harvest"]
    if not H:
        return "Şu an filtreleri geçen istikrarlı funding harvest adayı yok."
    L = ["💰 <b>Funding harvest</b> (spot al + perp short)"]
    for h in H[:10]:
        L.append(f"<b>{nt.esc(h['sym'])}</b> 30g %{h['ann_30d']:.0f} · şimdi %{h['ann_now']:.0f} · "
                 f"stabilite {h['stability']} · başabaş {h['breakeven_days']}g" + (" · soğuyor" if h["cooling"] else ""))
    L.append("\n<i>Getiri garanti değil; funding tersine dönebilir.</i>")
    return "\n".join(L)


def cmd_karne(d: dict) -> str:
    st = d["ledger"]["stats"]
    o = st["overall"]
    L = ["📒 <b>Karne</b> (setup ve periyot bazında)"]
    if o.get("n"):
        L.append(f"{o['n']} kapanmış · isabet %{o['win_rate']} · ort. {o['avg_r']:+.2f}R · toplam {o['sum_r']:+.1f}R · PF {o.get('pf') or '-'}")
    else:
        L.append(f"Henüz kapanmış işlem yok · {st['open_count']} açık")
    for k, s in st["by_setup"].items():
        if s.get("n"):
            L.append(f"• {SETUP_LABELS[k]}: {s['n']} işlem, %{s['win_rate']}, {s['avg_r']:+.2f}R → {s['verdict']['text']}")
        else:
            L.append(f"• {SETUP_LABELS[k]}: veri yok ({s['open']} açık)")
    for tf, s in (st.get("by_tf") or {}).items():
        if s.get("n"):
            L.append(f"⏱ {tf}: {s['n']} işlem, %{s['win_rate']}, {s['avg_r']:+.2f}R, {s.get('pnl_usd', 0):+.2f}$ → {s['verdict']['text']}")
    return "\n".join(L)


def _tr(v, d=2, sign=False) -> str:
    s = f"{v:+,.{d}f}" if sign else f"{v:,.{d}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _usd(v) -> str:
    return _tr(v, 2, True) + "$"


def cmd_demo(d: dict) -> str:
    st = d["ledger"]["stats"]
    dm = st.get("demo") or {}
    o = st["overall"]
    L = [f"💼 <b>Demo hesap</b> · işlem başı {dm.get('trade_size', 20):g}$ · {dm.get('lev', 1):g}x",
         f"Bakiye: <b>{_tr(dm.get('equity', 0))}$</b> (başlangıç {_tr(dm.get('start', 0), 0)}$) · getiri <b>%{_tr(dm.get('return_pct', 0), 2, True)}</b>",
         f"Gerçekleşen {_usd(dm.get('realized', 0))} · açık pozisyon K/Z {_usd(dm.get('unrealized', 0))}",
         f"Ödenen komisyon {_tr(dm.get('fees_usd', 0))}$ · funding {_usd(dm.get('funding_usd', 0))}"]
    if o.get("n"):
        L.append(f"{o['n']} işlem kapandı · isabet %{o['win_rate']} · ort. {o['avg_r']:+.2f}R")
    for tf, s in (st.get("by_tf") or {}).items():
        if s.get("n") or s.get("open"):
            L.append(f"• {tf}: {s.get('n', 0)} kapanmış, {s.get('open', 0)} açık"
                     + (f", {_usd(s['pnl_usd'])}, %{s['win_rate']} isabet" if s.get("n") else ""))
    return "\n".join(L)


def cmd_pozisyonlar(d: dict) -> str:
    op = d["ledger"]["open"]
    if not op:
        return "Açık demo pozisyon yok."
    L = [f"📂 <b>Açık demo pozisyonlar</b> ({len(op)})"]
    for t in op[:25]:
        L.append(f"{'🟢' if t['side'] == 'long' else '🔴'} <b>{nt.esc(t['sym'])}</b> {t.get('tf', '4H')} "
                 f"{SETUP_LABELS[t['setup']]} · giriş {nt.fmt_price(t['entry'])} → {nt.fmt_price(t.get('last'))}"
                 f" · <b>{_usd(t.get('upnl_usd', 0))}</b> ({t.get('r_now', 0):+.2f}R)")
    return "\n".join(L)


def _strip(t: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", t)


def cmd_borsalar(d: dict) -> str:
    arb, spr = d.get("arbitrage") or [], d.get("spreads") or []
    L = ["🌏 <b>Borsalar arası</b>"]
    if arb:
        L.append("\n<b>Funding farkı</b> (yıllık %, düşük olanda long + yüksek olanda short):")
        for a in arb[:8]:
            L.append(f"• <b>{nt.esc(a['sym'])}</b> {a['spread']:.0f}% → long {a['long_at']} ({a['lo']:.0f}%), "
                     f"short {a['short_at']} ({a['hi']:.0f}%)")
    else:
        L.append("Şu an anlamlı funding farkı yok.")
    if spr:
        L.append("\n<b>OKX ↔ Bitget fiyat farkı</b>:")
        for x in spr[:6]:
            L.append(f"• {nt.esc(x['sym'])} {x['bps']:+.1f} bp")
    L.append("\n<i>Farklar komisyon, transfer ve funding aralığı farkını içermez.</i>")
    return "\n".join(L)


def cmd_ekip(d: dict) -> str:
    tm = d.get("team")
    if not tm:
        return "Ekip henüz kurulmadı."
    L = ["🏢 <b>Yatırım ekibi</b>"]
    for m in sorted(tm.values(), key=lambda x: -x["equity"]):
        s = m.get("stats") or {}
        perf = (f" · {s['n']} işlem, %{_tr(s['win_rate'], 0)} isabet, {_tr(s['avg_r'], 2, True)}R" if s.get("n") else " · henüz kapanmış işlem yok")
        L.append(f"{m['emoji']} <b>{m['name']}</b> ({m['title']}) {_tr(m['equity'])}$ (%{_tr(m['return_pct'], 1, True)})"
                 + (f" · {m['open']} açık" if m["open"] else "") + (f" · {m['orders']} emir" if m["orders"] else "")
                 + perf + (" · 👤 gölgede" if m["benched"] else ""))
    L.append("\nAyrıntı: /yonetici kaan")
    return "\n".join(L)


def cmd_yonetici(d: dict, mid: str) -> str:
    tm = d.get("team") or {}
    mid = {"ayşe": "ayse"}.get(mid, mid)
    m = tm.get(mid)
    if not m:
        return "Böyle bir yönetici yok. Ekip: " + ", ".join(x["name"] for x in tm.values())
    s = m.get("stats") or {}
    L = [f"{m['emoji']} <b>{m['name']} · {m['title']}</b>", f"<i>{nt.esc(m['style'])}</i>",
         f"Sermaye {_tr(m['equity'])}$ (başlangıç {_tr(m['capital'])}$) · getiri %{_tr(m['return_pct'], 1, True)} · zirveden %{_tr(m['dd_pct'], 1)}",
         f"Risk: işlem başı %{_tr(m['base_risk'], 1)}–%{_tr(m['max_risk'], 1)} · yedek kulübesi limiti %{_tr(m['dd_bench'], 0)}"]
    if s.get("n"):
        L.append(f"{s['n']} işlem · isabet %{_tr(s['win_rate'], 0)} · ort. {_tr(s['avg_r'], 2, True)}R · PF {s.get('pf') or '-'}")
    op = [t for t in (d.get("ledger") or {}).get("open", []) if t.get("mgr") == mid]
    for t in op[:6]:
        L.append(f"• {nt.esc(t['sym'])} {t['side']} {_usd(t.get('upnl_usd', 0))} ({_tr(t.get('r_now', 0), 2, True)}R)")
    j = [x for x in d.get("journal", []) if x["mgr"] == mid][-6:]
    if j:
        L.append("\n<b>Son kararlar</b>")
        for x in reversed(j):
            L.append(f"• {nt.esc(x['sym'])} {x['action']}: {nt.esc(x['reason'])}")
    return "\n".join(L)


def cmd_haber(d: dict) -> str:
    n = d.get("news") or {}
    L = ["📰 <b>Haber Masası</b>"]
    f = n.get("fng")
    if f:
        L.append(f"Korku-Açgözlülük: <b>{f.get('value')}</b> ({nt.esc(str(f.get('label', '')))})")
    flags = n.get("flags") or {}
    if flags:
        for sym, x in sorted(flags.items(), key=lambda kv: kv[1]["level"] != "kirmizi"):
            tag = "🔴" if x["level"] == "kirmizi" else "🟡"
            L.append(f"{tag} <b>{nt.esc(sym)}</b>: {nt.esc(x['title'][:120])} <i>({nt.esc(x['src'])}, {_age(x['ts'])})</i>")
        L.append("<i>Kırmızı: o coinde yeni işlem yok. Sarı: yükselişin peşine düşülmez (long yok).</i>")
    else:
        L.append("Aktif uyarı yok.")
    st = n.get("status") or {}
    if st:
        L.append("\nKaynaklar: " + " · ".join(f"{k}: {v}" for k, v in st.items()))
    return "\n".join(L)


def cmd_hava(d: dict) -> str:
    w = d.get("weather") or {}
    tr = {"boga": "🟢 boğa (BTC 50 günlük ortalamanın üstünde ve yükseliyor)",
          "ayi": "🔴 ayı (BTC 50 günlük ortalamanın altında ve düşüyor)", "yatay": "🟡 yatay / kararsız"}
    L = ["🌦 <b>Piyasa havası</b>", tr.get(w.get("trend"), "henüz hesaplanmadı")]
    if w:
        L.append(f"Oynaklık: {'yüksek (risk ×0,75)' if w.get('high_vol') else 'normal'} · yıllık sıralama %{_tr((w.get('rv_rank') or 0) * 100, 0)}")
        L.append("Etkisi: ayı piyasasında long, boğada short yarım riskle; short işlemler sadece BTC 50 günlüğün altındayken.")
    c = d.get("calendar") or {}
    if c.get("block"):
        L.append(f"\n⛔ Şu an yeni işlem yok: {nt.esc(c['block'])}")
    up = c.get("upcoming") or []
    if up:
        L.append("\n<b>Yaklaşan önemli olaylar</b> (±60 dk yeni işlem yok)")
        from datetime import datetime
        from zoneinfo import ZoneInfo
        for e in up:
            L.append(f"• {datetime.fromtimestamp(e['ts'] / 1000, tz=ZoneInfo('Europe/Istanbul')).strftime('%d.%m %H:%M')} — {nt.esc(e['title'])}")
    if c.get("status"):
        L.append(f"<i>{nt.esc(c['status'])}</i>")
    return "\n".join(L)


def cmd_test(d: dict) -> str:
    bt = d.get("backtest_v8") or {}
    if not bt:
        return "5 yıllık test sonucu henüz yok."
    L = [f"🧪 <b>5 yıllık test</b> ({bt.get('test_start')} → bugün, {bt.get('n_coins')} coin, sert maliyet)"]
    for mid, b in (bt.get("managers") or {}).items():
        a, e = b.get("all") or {}, b.get("equity") or {}
        L.append(f"• {nt.esc(b['name'])}: {a.get('n', 0)} işlem, ort. {_tr(a.get('avg_r') or 0, 2, True)}R, "
                 f"yıllık %{_tr(e.get('cagr_pct') or 0, 1, True)}, maks. düşüş %{_tr(e.get('max_dd_pct') or 0, 1)}")
    pol = bt.get("policy_active")
    if pol:
        L.append(f"\nSermaye alanlar: {', '.join(pol)} + Elif (funding). Diğerleri gölgede kendini kanıtlıyor.")
    L.append("<i>Ayrıntı: dashboard → Ekip sekmesi / data/BACKTEST.md</i>")
    return "\n".join(L)


def cmd_emirler(d: dict) -> str:
    od = d.get("orders") or []
    if not od:
        return "Bekleyen limit emir yok."
    tm = d.get("team") or {}
    L = ["📌 <b>Bekleyen limit emirler</b>"]
    for o in od:
        m = tm.get(o["mgr"], {})
        L.append(f"{m.get('emoji', '')} {m.get('name', o['mgr'])} · {nt.esc(o['sym'])} {o['side']} {nt.fmt_price(o['px'])} "
                 f"(şu an {nt.fmt_price(o.get('market'))})")
    return "\n".join(L)


def cmd_risk(d: dict) -> str:
    r = d.get("risk") or {}
    c = r.get("cfg") or {}
    dm = (d.get("ledger") or {}).get("stats", {}).get("demo", {})
    pf = (d.get("ledger") or {}).get("stats", {}).get("perf", {})
    halt = {"dusus": "🛑 zirveden düşüş limiti (yeniden başlatmak için /devam)", "gunluk": "⏸ günlük kayıp limiti"}.get(r.get("halt"))
    L = ["🛡 <b>Risk durumu</b>",
         f"Durum: {halt or '🟢 işlem açık'}",
         f"Bakiye {_tr(dm.get('equity', 0))}$ · bugün {_usd(r.get('day_pnl', 0))} · zirveden %{_tr(r.get('dd_pct', 0), 1)}",
         f"Açık risk {_tr(dm.get('heat_usd', 0))}$ (%{_tr(dm.get('heat_pct', 0), 1)} / limit %{c.get('max_isi_yuzde')}) · "
         f"long {dm.get('long_n', 0)} · short {dm.get('short_n', 0)}",
         f"İşlem başı risk %{c.get('risk_yuzde')} × {r.get('risk_mult', 1):g}" + (f" (kayıp serisi {r.get('loss_streak')})" if r.get("loss_streak") else ""),
         f"Limitler: günlük kayıp %{c.get('gunluk_kayip_yuzde')} · zirveden düşüş %{c.get('max_dusus_yuzde')}"]
    if pf:
        L.append(f"Performans: maks. düşüş %{_tr(pf.get('max_dd_pct', 0), 1)}"
                 + (f" · Sharpe {pf['sharpe']}" if pf.get("sharpe") is not None else "")
                 + (f" · Sortino {pf['sortino']}" if pf.get("sortino") is not None else ""))
    if r.get("blocked"):
        L.append("Kapalı setup'lar: " + ", ".join(r["blocked"]))
    return "\n".join(L)


def cmd_saglik(d: dict) -> str:
    f = d.get("feeds") or {}
    L = ["🩺 <b>Sistem sağlığı</b>"]
    for k, v in f.items():
        if k == "telegram":
            L.append(f"Telegram: {'dinliyor' if v.get('listening') else 'bekliyor'} · gönderilen {v.get('sent')} · "
                     f"hata {v.get('failed')} · gecikme {v.get('median_send_ms')} ms")
        else:
            L.append(f"{k}: {'🟢 canlı' if v.get('healthy') else '🔴 kopuk'} · son veri {v.get('last_msg_age_s')} sn önce · "
                     f"{v.get('ticks')} fiyat · {v.get('reconnects')} yeniden bağlanma")
    lp = d.get("loop") or {}
    L.append(f"Döngü: {lp.get('scans', 0)} tarama · {lp.get('opened', 0)} açılış · {lp.get('closed', 0)} kapanış")
    return "\n".join(L)


def handle(updates: list, state: dict, data: dict | None, on_scan=None, on_close=None, on_resume=None) -> bool:
    """Telegram güncellemelerini işler. Döner: state değişti mi.

    on_close(hedef) → cevap metni. hedef: işlem kimliği (butondan) ya da coin sembolü (/kapat SOL).
    """
    changed = False
    owner = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or str(state.get("owner_chat_id") or "")
    for u in updates:
        cq = u.get("callback_query")
        if cq:
            cid = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
            dat = cq.get("data") or ""
            if cid != owner:
                nt.tg_call("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": "Bu bot özel."})
                continue
            if dat.startswith("kapat:") and on_close:
                reply = on_close(dat.split(":", 1)[1])
            else:
                reply = "Bu buton artık geçerli değil."
            nt.tg_call("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": _strip(reply)[:190]})
            nt.tg_send(cid, reply)
            continue
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        cid = str(chat.get("id", ""))
        text = (msg.get("text") or "").strip()
        if not cid or not text.startswith("/"):
            continue
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if not owner and chat.get("type") == "private" and cmd == "/start":
            owner = cid
            state["owner_chat_id"] = cid
            changed = True
            nt.tg_send(cid, "✅ Bu sohbet Kripto Radar'ın sahibi olarak kaydedildi. Bildirimler buraya gelecek.\n\n" + HELP)
            continue
        if cid != owner:
            nt.tg_send(cid, "Bu bot özel. 🙂")
            continue
        if cmd in ("/start", "/yardim", "/help"):
            reply = HELP
        elif cmd == "/tara":
            reply = on_scan() if on_scan else dispatch_scan()
        elif cmd == "/sessiz":
            hours = float(arg) if arg.strip().replace(".", "", 1).isdigit() else 8.0
            state["mute_until"] = int((time.time() + hours * 3600) * 1000)
            changed = True
            reply = f"🔕 {hours:g} saat boyunca bildirim gönderilmeyecek. /ac ile açabilirsin."
        elif cmd == "/ac":
            state["mute_until"] = 0
            changed = True
            reply = "🔔 Bildirimler açık."
        elif data is None:
            reply = "Henüz tarama verisi yok, birkaç dakika içinde hazır olur."
        elif cmd == "/durum":
            reply = cmd_durum(data)
        elif cmd == "/sinyaller":
            reply = cmd_sinyaller(data)
        elif cmd == "/coin":
            reply = cmd_coin(data, arg) if arg.strip() else "Kullanım: /coin SOL"
        elif cmd == "/funding":
            reply = cmd_funding(data)
        elif cmd in ("/karne", "/istatistik"):
            reply = cmd_karne(data)
        elif cmd in ("/demo", "/bakiye", "/hesap"):
            reply = cmd_demo(data)
        elif cmd in ("/pozisyonlar", "/pozisyon", "/acik"):
            reply = cmd_pozisyonlar(data)
        elif cmd == "/kapat":
            if not arg.strip():
                reply = "Kullanım: /kapat SOL  (açık demo işlemi piyasa fiyatından kapatır)"
            elif on_close:
                reply = on_close(arg.strip().upper())
            else:
                reply = "Kapatma şu an sadece canlı döngü açıkken yapılabilir."
        elif cmd in ("/ekip", "/yoneticiler"):
            reply = cmd_ekip(data)
        elif cmd in ("/yonetici", "/yönetici"):
            reply = cmd_yonetici(data, arg.strip().lower()) if arg.strip() else "Kullanım: /yonetici kaan"
        elif cmd in ("/emirler", "/emir"):
            reply = cmd_emirler(data)
        elif cmd == "/devam":
            reply = on_resume() if on_resume else "Bu komut canlı döngü açıkken çalışır."
        elif cmd == "/risk":
            reply = cmd_risk(data)
        elif cmd in ("/borsalar", "/arbitraj"):
            reply = cmd_borsalar(data)
        elif cmd == "/saglik":
            reply = cmd_saglik(data)
        elif cmd in ("/haber", "/haberler"):
            reply = cmd_haber(data)
        elif cmd in ("/hava", "/takvim"):
            reply = cmd_hava(data)
        elif cmd in ("/test", "/backtest"):
            reply = cmd_test(data)
        else:
            reply = "Bilinmeyen komut. /yardim yaz."
        buttons = None
        if data and data.get("dashboard_url") and cmd in ("/durum", "/sinyaller", "/karne", "/demo", "/ekip"):
            buttons = [[{"text": "📊 Dashboard", "url": data["dashboard_url"]}]]
        nt.tg_send(cid, reply, buttons)
    return changed


def poll(state: dict, data: dict | None, on_scan=None, first: bool = False) -> bool:
    """getUpdates → işle → onayla. Döner: state değişti mi."""
    if not nt.tg_token():
        return False
    if first:
        me = nt.tg_call("getMe", {})
        wh = nt.tg_call("getWebhookInfo", {})
        if wh.get("ok") and (wh.get("result") or {}).get("url"):
            nt.tg_call("deleteWebhook", {})   # webhook varsa getUpdates çalışmaz
        print(f"::notice title=Telegram::bot=@{(me.get('result') or {}).get('username')} "
              f"sahip_kayıtlı={bool(state.get('owner_chat_id') or os.environ.get('TELEGRAM_CHAT_ID'))}")
    res = nt.tg_call("getUpdates", {"timeout": 0, "allowed_updates": ["message"]}, timeout=15)
    if not res.get("ok"):
        if first:
            print(f"::warning title=Telegram::getUpdates hata: {res.get('description')}")
        return False
    updates = res.get("result", [])
    if not updates:
        return False
    # Önce onayla: bir komut hata verse bile aynı mesaj tekrar tekrar işlenmesin
    nt.tg_call("getUpdates", {"offset": updates[-1]["update_id"] + 1, "timeout": 0})
    return handle(updates, state, data, on_scan)


def dispatch_scan() -> str:
    repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not (repo and tok):
        return "Tarama başlatılamadı (GitHub bağlamı yok)."
    r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/canli.yml/dispatches",
                      headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                      json={"ref": os.environ.get("GITHUB_REF_NAME", "main")}, timeout=20)
    if r.status_code in (201, 204):
        return "⟳ Canlı döngü başlatıldı; birkaç dakika içinde tarama yapılır."
    return f"Başlatılamadı: HTTP {r.status_code} {r.text[:120]}"


def run(site: Path, cfg_path=None) -> bool:
    """Tek seferlik: dosyadaki son veriyle komutları işle (döngü dışı kullanım)."""
    site = Path(site)
    state = store.load(site / "state.json", {})
    data = store.load(site / "data.json", None)
    if not nt.tg_token():
        print("TELEGRAM_BOT_TOKEN yok, komut dinleyici atlandı.")
        return False
    changed = poll(state, data, first=True)
    if changed:
        store.dump(site / "state.json", state, compact=True)
    return changed
