"""OKX istemcisi ve Telegram komutları — gerçek API formatında sahte cevaplarla test."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar import okx  # noqa: E402


class FakeResp:
    def __init__(self, status, js=None, text=""):
        self.status_code, self._js, self.text = status, js, text

    def json(self):
        if self._js is None:
            raise ValueError("no json")
        return self._js


def test_okx_parsing_and_host_fallback():
    c = okx.OKXClient()
    now = int(time.time() * 1000) // 3600_000 * 3600_000
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if url.startswith("https://www.okx.com"):
            return FakeResp(403, None, "cloudflare 1010")          # ilk host engelli
        if "candles" in url:
            rows = [[str(now - i * 4 * 3600_000), "10", "11", "9", "10.5", "1000", "100", "1050", "0" if i == 0 else "1"]
                    for i in range(5)]
            return FakeResp(200, {"code": "0", "msg": "", "data": rows})
        if "funding-rate-history" in url:
            rows = [{"instId": "X", "fundingRate": "0.0001", "realizedRate": "0.00011",
                     "fundingTime": str(now - i * 8 * 3600_000)} for i in range(30)]
            return FakeResp(200, {"code": "0", "data": rows})
        if "funding-rate" in url:
            if params.get("instId") == "ANY":
                return FakeResp(200, {"code": "51001", "msg": "Instrument ID does not exist", "data": []})
            return FakeResp(200, {"code": "0", "data": [{"instId": params["instId"], "fundingRate": "0.0002",
                                                          "fundingTime": str(now), "nextFundingTime": str(now + 4 * 3600_000)}]})
        if "open-interest-history" in url:
            return FakeResp(200, {"code": "0", "data": [[str(now - i * 3600_000), "5", "50", str(1e6 + i)] for i in range(30)]})
        if "taker-volume" in url:
            return FakeResp(200, {"code": "0", "data": [[str(now - i * 3600_000), "100", "150"] for i in range(48)]})
        if "open-interest" in url:
            return FakeResp(200, {"code": "0", "data": [{"instId": "BTC-USDT-SWAP", "oi": "1", "oiCcy": "2", "oiUsd": "123456"}]})
        return FakeResp(200, {"code": "0", "data": []})

    c.s.get = fake_get
    df = c.candles("BTC-USDT-SWAP", "4H", 300)
    assert len(df) == 4, "kapanmamış mum (confirm=0) atılmalı"
    assert df.index.is_monotonic_increasing
    assert df["volume"].iloc[0] == 100 and df["quote_volume"].iloc[0] == 1050
    assert c.host_idx == 1 and calls[0].startswith("https://www.okx.com") and calls[1].startswith("https://us.okx.com")
    assert c.funding_rates_all() is None                      # ANY desteklenmiyorsa None
    fr = c.funding_rate("BTC-USDT-SWAP")
    from radar.scan import _interval_h
    assert _interval_h(fr) == 4.0
    fh = c.funding_history("BTC-USDT-SWAP", 7)
    assert 18 <= len(fh) <= 22 and abs(fh["rate"].iloc[-1] - 0.00011) < 1e-12
    assert c.open_interest_all() == {"BTC-USDT-SWAP": 123456.0}
    s = c.oi_history("BTC-USDT-SWAP")
    assert len(s) == 30 and s.index.is_monotonic_increasing
    tv = c.taker_volume("BTC-USDT-SWAP")
    assert tv["buy"].sum() > tv["sell"].sum()


def test_rate_limit_retry():
    c = okx.OKXClient()
    seq = [FakeResp(429), FakeResp(200, {"code": "50011", "msg": "Too Many Requests"}),
           FakeResp(200, {"code": "0", "data": [1]})]
    c.s.get = lambda *a, **k: seq.pop(0)
    t = time.time()
    assert c.get("/api/v5/market/tickers", {"instType": "SWAP"}) == [1]
    assert time.time() - t < 10


def test_telegram_commands():
    import os
    from radar import commands, notify, scan, store
    from radar.demo import DemoClient
    sent, confirmed = [], []
    with tempfile.TemporaryDirectory() as d:
        scan.run(Path(d), client=DemoClient(), notify=False, demo=True)
        os.environ["TELEGRAM_BOT_TOKEN"] = "TEST"
        os.environ.pop("TELEGRAM_CHAT_ID", None)

        def fake_call(method, payload, timeout=20):
            if method == "getUpdates" and "offset" not in payload:
                msgs = [("/start", 111, "private"), ("/durum", 111, "private"), ("/coin sol", 111, "private"),
                        ("/sessiz 2", 111, "private"), ("/funding", 999, "private"), ("/karne", 111, "private")]
                return {"ok": True, "result": [{"update_id": 10 + i, "message": {"text": t, "chat": {"id": cid, "type": ty}}}
                                               for i, (t, cid, ty) in enumerate(msgs)]}
            if method == "getUpdates":
                confirmed.append(payload["offset"])
                return {"ok": True, "result": []}
            if method in ("getMe", "getWebhookInfo", "deleteWebhook"):
                return {"ok": True, "result": {"username": "test_bot"}}
            sent.append((payload["chat_id"], payload["text"]))
            return {"ok": True}

        notify.tg_call = fake_call
        commands.nt.tg_call = fake_call
        changed = commands.run(Path(d))
        st = store.load(Path(d) / "state.json", {})
        assert changed and st["owner_chat_id"] == "111" and st["mute_until"] > time.time() * 1000
        assert confirmed == [16]
        texts = [t for cid, t in sent if cid == "111"]
        assert any("sahibi" in t for t in texts) and any("Durum" in t for t in texts)
        assert any("SOL" in t for t in texts) and any("Karne" in t for t in texts)
        assert any(cid == "999" and "özel" in t for cid, t in sent)   # yabancıya cevap yok
        # sessizdeyken tarama bildirim göndermemeli
        sent.clear()
        scan.run(Path(d), client=DemoClient(), notify=True, demo=True)
        assert not sent
    # bildirim açıkken yeni sinyal mesajı ve günlük özet gönderilmeli
    with tempfile.TemporaryDirectory() as d2:
        os.environ["TELEGRAM_BOT_TOKEN"] = "TEST"
        os.environ["TELEGRAM_CHAT_ID"] = "111"
        sent.clear()
        scan.run(Path(d2), client=DemoClient(), notify=True, demo=True)
        assert any("demo işlem açıldı" in t and "20$" in t for _, t in sent), sent[:1]
        os.environ.pop("TELEGRAM_CHAT_ID", None)
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)


def test_message_templates():
    from radar import scan
    from radar.demo import DemoClient
    with tempfile.TemporaryDirectory() as d:
        p = scan.run(Path(d), client=DemoClient(), notify=False, demo=True)
        strong = [s for s in p["signals"] if s["score"] >= 65]
        m = scan.msg_new_signals(strong[:3], p, scan.load_config())
        assert "Stop" in m and "Hedef" in m and len(m) < 3900
        assert "Günlük" in scan.msg_daily(p, scan.load_config())
        assert "Haftalık karne" in scan.karne_md(p)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
