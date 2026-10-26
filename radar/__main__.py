"""Komut satırı: python -m radar {scan,commands,backtest,demo}"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

from . import store
from .config import ROOT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="radar", description="Kripto Radar — perpetual sinyal tarayıcı")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="tam tarama + dashboard + bildirim")
    p.add_argument("--site", default="site", help="dashboard/state klasörü")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--demo", action="store_true", help="sahte veriyle çalış (test)")
    p.add_argument("--config", default=None)

    p = sub.add_parser("live", help="canlı döngü (sürekli tarama + demo hesap)")
    p.add_argument("--site", default="site")
    p.add_argument("--minutes", type=float, default=None)
    p.add_argument("--demo", action="store_true")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--config", default=None)

    p = sub.add_parser("commands", help="Telegram komutlarını işle")
    p.add_argument("--site", default="site")
    p.add_argument("--config", default=None)

    p = sub.add_parser("test8", help="v8: yöneticilerin 5 yıllık testi (canlı fonksiyonlarla, sert maliyet)")
    p.add_argument("--coins", type=int, default=30)
    p.add_argument("--years", type=float, default=5.0)
    p.add_argument("--deniz-days", type=int, default=365)
    p.add_argument("--kesintisiz", action="store_true", help="kesintisiz (gün içi) mod testi")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--config", default=None)

    p = sub.add_parser("ogren", help="haftalık ayar öğrenmesi (5 yıl, sınırlı ızgara, walk-forward)")
    p.add_argument("--coins", type=int, default=30)
    p.add_argument("--years", type=float, default=5.0)
    p.add_argument("--demo", action="store_true")
    p.add_argument("--config", default=None)

    p = sub.add_parser("backtest", help="walk-forward backtest")
    p.add_argument("--coins", type=int, default=30)
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--demo", action="store_true")
    p.add_argument("--config", default=None)

    a = ap.parse_args(argv)

    if a.cmd == "scan":
        from . import scan
        client = None
        if a.demo:
            from .demo import DemoClient
            client = DemoClient()
        try:
            payload = scan.run(Path(a.site), a.config, client=client,
                               notify=not a.no_notify, demo=a.demo)
        except Exception:  # noqa: BLE001
            err = traceback.format_exc()
            print(err, file=sys.stderr)
            # GitHub arayüzünde "annotation" olarak görünsün (log açmadan okunabilir)
            short = " | ".join(err.strip().splitlines()[-4:]).replace("%", "%25")
            print(f"::error title=Tarama hatası::{short[:900]}")
            scan.record_failure(Path(a.site), err, a.config)
            return 1
        # Haftalık karne → repoya (main dalına) yazılır; repo "aktif" kalır
        st = store.load(Path(a.site) / "state.json", {})
        if time.time() * 1000 - (st.get("last_karne") or 0) > 7 * 86400 * 1000 and not a.demo:
            (ROOT / "data").mkdir(exist_ok=True)
            (ROOT / "data" / "KARNE.md").write_text(scan.karne_md(payload), encoding="utf-8")
            st["last_karne"] = int(time.time() * 1000)
            store.dump(Path(a.site) / "state.json", st, compact=True)
            print("✓ Haftalık karne yazıldı: data/KARNE.md")
        return 0

    if a.cmd == "live":
        from .engine import Engine
        client = None
        if a.demo:
            from .demo import DemoClient
            client = DemoClient()
        eng = Engine(Path(a.site), a.config, client=client, notify=not a.no_notify,
                     demo=a.demo, publish=True)
        try:
            eng.run_loop(a.minutes)
        except Exception:  # noqa: BLE001
            err = traceback.format_exc()
            print(err, file=sys.stderr)
            short = " | ".join(err.strip().splitlines()[-4:]).replace("%", "%25")
            print(f"::error title=Canlı döngü hatası::{short[:900]}")
            try:
                eng.publish(force=True)
            except Exception:  # noqa: BLE001
                pass
            return 1
        return 0

    if a.cmd == "commands":
        from . import commands
        commands.run(Path(a.site), a.config)
        return 0

    if a.cmd == "test8":
        from . import bt2
        client = None
        if a.demo:
            from .demo import DemoClient
            client = DemoClient(hours=24 * 900)
        if a.kesintisiz:
            bt2.run_intraday(client=client, cfg_path=a.config, n_coins=a.coins, years=a.years)
        else:
            bt2.run(client=client, cfg_path=a.config, n_coins=a.coins, years=a.years, deniz_days=a.deniz_days)
        return 0

    if a.cmd == "ogren":
        from . import learn
        client = None
        if a.demo:
            from .demo import DemoClient
            client = DemoClient(hours=24 * 900)
        learn.run(client=client, cfg_path=a.config, n_coins=a.coins, years=a.years)
        return 0

    if a.cmd == "backtest":
        from . import backtest
        client = None
        if a.demo:
            from .demo import DemoClient
            client = DemoClient()
        backtest.run(a.coins, a.days, client=client, cfg_path=a.config)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
