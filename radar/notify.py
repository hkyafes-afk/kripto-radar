"""Bildirimler: Telegram (ana) ve WhatsApp/CallMeBot (isteğe bağlı)."""
from __future__ import annotations

import html
import os
import queue
import threading
import time
import urllib.parse

import requests

TG_API = "https://api.telegram.org/bot{token}/{method}"


def tg_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def tg_chat(state: dict) -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip() or str(state.get("owner_chat_id") or "")


_local = threading.local()


def _session() -> requests.Session:
    """Thread başına kalıcı bağlantı (her mesajda yeni TLS el sıkışması yapmamak için)."""
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        _local.s = s
    return s


def tg_call(method: str, payload: dict, timeout: float = 20) -> dict:
    token = tg_token()
    if not token:
        return {"ok": False, "description": "TELEGRAM_BOT_TOKEN yok"}
    try:
        r = _session().post(TG_API.format(token=token, method=method), json=payload, timeout=timeout)
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "description": str(e)}


def _send_now(chat_id: str, chunk: str, buttons=None, tries: int = 5) -> bool:
    """Tek parça mesajı gönderir; 429'da Telegram'ın istediği kadar bekler, ağ hatasında tekrar dener."""
    payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    for i in range(tries):
        res = tg_call("sendMessage", payload)
        if res.get("ok"):
            return True
        code = res.get("error_code")
        if code == 429:
            time.sleep(float((res.get("parameters") or {}).get("retry_after", 2)) + 0.3)
            continue
        if code == 400 and "parse" in str(res.get("description", "")).lower():
            payload.pop("parse_mode", None)          # HTML bozuksa düz metin olarak gönder
            payload["text"] = _strip_html(chunk)
            continue
        if code in (400, 403):                        # kalıcı hata (sohbet yok, bot engellendi)
            print(f"[telegram] gönderilemedi: {res.get('description')}")
            return False
        time.sleep(min(2 ** i, 15))
    print(f"[telegram] {tries} denemede gönderilemedi")
    return False


def tg_send(chat_id: str, text: str, buttons: list[list[dict]] | None = None) -> bool:
    if not (tg_token() and chat_id):
        return False
    if WORKER is not None:
        WORKER.send(chat_id, text, buttons)
        return True
    ok = True
    for chunk in _split(text, 3900):
        ok = _send_now(chat_id, chunk, buttons) and ok
    return ok


def wa_send(text: str) -> bool:
    phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
    key = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if not (phone and key):
        return False
    if WORKER is not None:
        WORKER.q.put(("wa", text, None, time.time()))
        return True
    return _wa_now(text)


def _wa_now(text: str) -> bool:
    phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
    key = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    plain = _strip_html(text)[:1500]
    url = ("https://api.callmebot.com/whatsapp.php?phone=" + urllib.parse.quote(phone)
           + "&text=" + urllib.parse.quote(plain) + "&apikey=" + urllib.parse.quote(key))
    try:
        r = requests.get(url, timeout=25)
        if r.status_code != 200:
            print(f"[whatsapp] HTTP {r.status_code}: {r.text[:120]}")
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001
        print(f"[whatsapp] hata: {e}")
        return False


def broadcast(text: str, state: dict, cfg: dict, buttons=None) -> None:
    b = cfg["bildirim"]
    if b.get("telegram", True):
        tg_send(tg_chat(state), text, buttons)
    if b.get("whatsapp", True):
        wa_send(text)


class TelegramWorker:
    """İki thread: gönderici (kuyruk + yeniden deneme) ve dinleyici (long polling → anında komut)."""

    def __init__(self, on_updates):
        self.q: queue.Queue = queue.Queue(maxsize=1000)
        self.on_updates = on_updates
        self.stop_ev = threading.Event()
        self.offset = None
        self.sent = 0
        self.failed = 0
        self.lat_ms: list[float] = []
        self.polls = 0
        self.poll_errors = 0
        self.last_poll = 0.0
        self.updates_seen = 0
        self.bot = ""
        self.t_send = threading.Thread(target=self._sender, daemon=True, name="tg-send")
        self.t_poll = threading.Thread(target=self._poller, daemon=True, name="tg-poll")

    def start(self):
        global WORKER
        me = tg_call("getMe", {})
        self.bot = (me.get("result") or {}).get("username", "")
        wh = tg_call("getWebhookInfo", {})
        if wh.get("ok") and (wh.get("result") or {}).get("url"):
            tg_call("deleteWebhook", {})            # webhook varken getUpdates çalışmaz
        WORKER = self
        self.t_send.start()
        self.t_poll.start()
        return self

    def stop(self, flush_s: float = 15):
        global WORKER
        t0 = time.time()
        while not self.q.empty() and time.time() - t0 < flush_s:
            time.sleep(0.2)
        self.stop_ev.set()
        WORKER = None

    def send(self, chat_id, text, buttons=None):
        parts = _split(text, 3900)
        for i, chunk in enumerate(parts):
            try:
                self.q.put_nowait(("tg", chunk, (chat_id, buttons if i == len(parts) - 1 else None), time.time()))
            except queue.Full:
                self.failed += 1

    def _sender(self):
        while not self.stop_ev.is_set():
            try:
                kind, text, extra, t_in = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                ok = _send_now(extra[0], text, extra[1]) if kind == "tg" else _wa_now(text)
            except Exception as e:  # noqa: BLE001
                print(f"[telegram] gönderim hatası: {e}")
                ok = False
            if ok:
                self.sent += 1
                self.lat_ms.append((time.time() - t_in) * 1000)
                del self.lat_ms[:-50]
            else:
                self.failed += 1

    def _poller(self):
        while not self.stop_ev.is_set():
            payload = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if self.offset is not None:
                payload["offset"] = self.offset
            res = tg_call("getUpdates", payload, timeout=35)
            self.polls += 1
            if not res.get("ok"):
                self.poll_errors += 1
                if res.get("error_code") == 409:      # başka bir getUpdates/webhook çakışması
                    tg_call("deleteWebhook", {})
                time.sleep(3)
                continue
            self.last_poll = time.time()
            ups = res.get("result", [])
            if not ups:
                continue
            self.offset = ups[-1]["update_id"] + 1
            self.updates_seen += len(ups)
            try:
                self.on_updates(ups)
            except Exception as e:  # noqa: BLE001
                print(f"[telegram] komut işlenemedi: {e}")

    def status(self) -> dict:
        lat = sorted(self.lat_ms)
        return {"bot": self.bot, "sent": self.sent, "failed": self.failed, "queue": self.q.qsize(),
                "median_send_ms": round(lat[len(lat) // 2]) if lat else None,
                "listening": self.t_poll.is_alive() and time.time() - self.last_poll < 60,
                "polls": self.polls, "poll_errors": self.poll_errors, "updates": self.updates_seen}


WORKER: TelegramWorker | None = None


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def _strip_html(s: str) -> str:
    import re
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def _split(text: str, n: int) -> list[str]:
    if len(text) <= n:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > n:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def fmt_price(p: float) -> str:
    if p is None or p != p:
        return "-"
    if p >= 1000:
        return f"{p:,.0f}".replace(",", ".")
    if p >= 1:
        return f"{p:.3f}".rstrip("0").rstrip(".")
    if p >= 0.01:
        return f"{p:.4f}"
    return f"{p:.8f}".rstrip("0")
