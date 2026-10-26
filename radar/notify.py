"""Bildirimler: Telegram (ana) ve WhatsApp/CallMeBot (isteğe bağlı)."""
from __future__ import annotations

import html
import os
import urllib.parse

import requests

TG_API = "https://api.telegram.org/bot{token}/{method}"


def tg_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def tg_chat(state: dict) -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip() or str(state.get("owner_chat_id") or "")


def tg_call(method: str, payload: dict, timeout: float = 20) -> dict:
    token = tg_token()
    if not token:
        return {"ok": False, "description": "TELEGRAM_BOT_TOKEN yok"}
    try:
        r = requests.post(TG_API.format(token=token, method=method), json=payload, timeout=timeout)
        return r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "description": str(e)}


def tg_send(chat_id: str, text: str, buttons: list[list[dict]] | None = None) -> bool:
    if not (tg_token() and chat_id):
        return False
    ok = True
    for chunk in _split(text, 3900):
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                   "disable_web_page_preview": True}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        res = tg_call("sendMessage", payload)
        if not res.get("ok"):
            print(f"[telegram] gönderilemedi: {res.get('description')}")
            ok = False
    return ok


def wa_send(text: str) -> bool:
    phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
    key = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if not (phone and key):
        return False
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
