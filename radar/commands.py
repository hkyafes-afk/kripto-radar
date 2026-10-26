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


def handle(updates: list, state: dict, data: dict | None, on_scan=None) -> bool:
    """Telegram güncellemelerini işler. Döner: state değişti mi."""
    changed = False
    owner = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or str(state.get("owner_chat_id") or "")
    for u in updates:
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
        else:
            reply = "Bilinmeyen komut. /yardim yaz."
        buttons = None
        if data and data.get("dashboard_url") and cmd in ("/durum", "/sinyaller", "/karne", "/demo"):
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
