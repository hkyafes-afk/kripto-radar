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

    p = sub.add_parser("yaris", help="v9 Faz 1: 5 yıllık varyant yarışı (çıkış, giriş, stop, firma kuralları)")
    p.add_argument("--coins", type=int, default=30)
    p.add_argument("--years", type=float, default=5.0)
    p.add_argument("--binance", default=None, help="Binance Vision arşiv klasörü (binance-veri dalı); verilirse OKX yerine bu kullanılır, "
                                                    "--coins = her gün hacme göre ilk N coin, batan coinler dahil")
    p.add_argument("--yoneticiler", default=None, help="virgülle ayrılmış yönetici listesi (ör. kemal,kaan)")
    p.add_argument("--cikti", default=None, help="çıktı klasörü (varsayılan data/; Binance için ayrı klasör önerilir)")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--config", default=None)

    p = sub.add_parser("etkinlik", help="birleşik etkinlik raporu: defter ve/veya yarış işlemleri → etkinlik.json")
    p.add_argument("--defter", default=None, help="legacy ledger.json (kapanmış işlemler)")
    p.add_argument("--birlesik", default=None, help="birleşik paper defteri yurutme.json")
    p.add_argument("--yaris", default=None, help="race_islemler.csv (geçmiş test işlemleri)")
    p.add_argument("--cikti", default="data/etkinlik.json")
    p.add_argument("--config", default=None)

    p = sub.add_parser("hakem", help="v9 Analist-Hakem modeli eğitimi (5 yıllık test + canlı/gölge işlemler)")
    p.add_argument("--coins", type=int, default=30)
    p.add_argument("--years", type=float, default=5.0)
    p.add_argument("--ledger", default=None, help="kalp dalındaki ledger.json (canlı işlemler)")
    p.add_argument("--demo", action="store_true")
    p.add_argument("--config", default=None)

    p = sub.add_parser("veri", help="v9 veri toplayıcı: OI, long/short, taker, likidasyon (saatlik, ayrı iş)")
    p.add_argument("--out", default="veri/okx")
    p.add_argument("--coins", type=int, default=40)

    p = sub.add_parser("backtest", help="walk-forward backtest")
    p.add_argument("--coins", type=int, default=30)
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--demo", action="store_true")
    p.add_argument("--config", default=None)

    a = ap.parse_args(argv)

    if a.cmd == "yaris":
        from . import race
        client = None
        if a.demo:
            from .demo import DemoClient
            client = DemoClient()
        out_dir = (Path("site") / "yaris_demo") if a.demo else (Path(a.cikti) if a.cikti else None)
        race.run(client=client, cfg_path=a.config, n_coins=a.coins, years=a.years, out_dir=out_dir,
                 binance_dir=a.binance, only_mgrs=[x for x in (a.yoneticiler or "").split(",") if x] or None)
        return 0

    if a.cmd == "etkinlik":
        import json as _json
        from .config import load_config
        from .unified import effectiveness as eff
        cfg = load_config(a.config)
        reports = {}
        if a.yaris:
            reports["yaris"] = eff.audit_race(a.yaris, cfg)
        if a.defter or a.birlesik:
            lc = (_json.loads(Path(a.defter).read_text(encoding="utf-8")).get("closed") or []) if a.defter else []
            uc = (_json.loads(Path(a.birlesik).read_text(encoding="utf-8")).get("closed") or []) if a.birlesik else []
            reports["canli"] = eff.EffectivenessAuditor(cfg).audit(lc, uc)
        if not reports:
            print("en az biri gerekli: --defter, --birlesik, --yaris")
            return 2
        out = Path(a.cikti)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_json.dumps(reports, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        for name, rep in reports.items():
            print(f"[{name}] {rep['ozet']}")
            for k, v in rep["kapsamlar"].items():
                if k.startswith("strateji:"):
                    print(f"  {k}: n={v['n_trades']} ort={v['avg_r']} GA=[{v['ci_low_r']}, {v['ci_high_r']}] → {v['karar']}")
        return 0

    if a.cmd == "hakem":
        from . import hakem
        client = None
        if a.demo:
            from .demo import DemoClient
            client = DemoClient()
        hakem.run(client=client, cfg_path=a.config, n_coins=a.coins, years=a.years,
                  ledger_path=Path(a.ledger) if a.ledger else None,
                  out=(Path("site") / "hakem_demo.json") if a.demo else None)
        return 0

    if a.cmd == "veri":
        from . import collect
        collect.run(out_dir=Path(a.out), n_coins=a.coins)
        return 0

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
