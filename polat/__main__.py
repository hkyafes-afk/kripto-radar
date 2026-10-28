"""Polat komut satırı.

  python -m polat calis  [--kok polat_veri] [--sure SANIYE] [--git-push-dk 15]
  python -m polat rapor  [--kok polat_veri]
  python -m polat devam  [--kok polat_veri]   → risk durdurmasını (zirveden −%15) kullanıcı onayıyla kaldırır
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import time
from pathlib import Path

import yaml

from .agent import Polat
from .lease import Lease
from .okx import Rest

HERE = Path(__file__).resolve().parent


def load_cfg(path: Path | None = None) -> dict:
    return yaml.safe_load((path or HERE / "config.yaml").read_text(encoding="utf-8"))


def make_rest(cfg: dict) -> Rest:
    mode = cfg["yurutme"]["mod"]
    if mode == "okx_canli":
        raise SystemExit("Gerçek para modu kapalı: kullanıcının açık onayı olmadan açılmaz.")
    if mode == "okx_demo":
        raise SystemExit("okx_demo modu için önce POLAT_OKX_* anahtarları ve uçtan uca demo testi gerekir.")
    return Rest(cfg, os.environ.get("POLAT_OKX_KEY", ""), os.environ.get("POLAT_OKX_SECRET", ""),
                os.environ.get("POLAT_OKX_PASSPHRASE", ""), demo=True)


async def run(cfg: dict, root: Path, seconds: float | None, push_min: float, devir: bool = False):
    script = HERE.parent / "scripts" / "polat_veri.sh"
    lease = Lease(root, script)
    on_gh = bool(os.environ.get("GITHUB_ACTIONS"))
    if on_gh and not devir:
        cur = await asyncio.to_thread(lease.remote)
        if lease.healthy(cur):
            print(f"Nöbette sağlıklı Polat var ({cur.get('owner')}); bekçi çıkıyor.")
            return
    agent = Polat(cfg, root, make_rest(cfg))
    loop = asyncio.get_running_loop()
    stop_ev = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_ev.set)
        except NotImplementedError:  # pragma: no cover
            pass
    if devir and on_gh:
        await agent.start(standby=True)
        t0 = time.time()
        released = False
        while time.time() - t0 < 15 * 60:                      # selefin bırakmasını bekle (en çok 15 dk)
            cur = await asyncio.to_thread(lease.remote)
            if not lease.healthy(cur):
                released = True
                break
            await asyncio.sleep(2)
        if not released:                                       # selef hâlâ sağlıklı: ikinci Polat olmasın
            print("Selef 15 dk içinde bırakmadı ve hâlâ sağlıklı; halef çıkıyor.")
            await agent.stop("devir alınamadı")
            return
        await asyncio.to_thread(lease.sync_from_remote)
        info = await agent.activate()
        print(f"Devralındı: boşluk {info.get('bosluk_sn')} sn")
    else:
        await agent.start()
    lease.write()
    await asyncio.to_thread(lease.push, "nöbet alındı")
    dur = seconds or 0
    handoff_at = (dur - cfg["devir"]["halef_once_dk"] * 60) if (on_gh and dur) else None
    started = time.time()

    async def heartbeat():
        sent = False
        while True:
            await asyncio.sleep(push_min * 60 if push_min > 0 else 60)
            agent.save()
            agent.write_report()
            lease.write()
            await asyncio.to_thread(lease.push, "nabız")
            if handoff_at and not sent and time.time() - started >= handoff_at:
                sent = await asyncio.to_thread(lease.dispatch_successor)
                agent.journal.write("olaylar", {"olay": "halef_cagrildi", "ok": sent})

    hb = asyncio.create_task(heartbeat())
    try:
        if dur:
            await asyncio.wait_for(stop_ev.wait(), timeout=dur)
        else:
            await stop_ev.wait()
    except asyncio.TimeoutError:
        pass
    hb.cancel()
    await agent.stop("süre doldu" if not stop_ev.is_set() else "sinyal")
    agent.write_report()
    lease.write(released=True)
    await asyncio.to_thread(lease.push, "nöbet bırakıldı")
    print(json.dumps(agent.status(), ensure_ascii=False, default=str)[:2000])


def main():
    ap = argparse.ArgumentParser(prog="polat")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("calis")
    a.add_argument("--kok", default="polat_veri")
    a.add_argument("--sure", type=float, default=None)
    a.add_argument("--git-push-dk", type=float, default=0)
    a.add_argument("--devir", action="store_true")
    b = sub.add_parser("rapor")
    b.add_argument("--kok", default="polat_veri")
    d = sub.add_parser("devam")
    d.add_argument("--kok", default="polat_veri")
    args = ap.parse_args()
    cfg = load_cfg()
    root = Path(args.kok)
    if args.cmd == "calis":
        asyncio.run(run(cfg, root, args.sure, args.git_push_dk, devir=args.devir or os.environ.get("DEVIR") == "1"))
    elif args.cmd == "devam":
        f = root / "durum.json"
        st = json.loads(f.read_text(encoding="utf-8"))
        eq = st["broker"]["cash"] + sum(p["margin"] for p in st["broker"]["positions"].values())
        st["risk"].update(halted=False, halt_reason="", peak=eq)   # zirve bugünden yeniden sayılır
        f.write_text(json.dumps(st, ensure_ascii=False, default=str), encoding="utf-8")
        print(f"Risk durdurması kaldırıldı; zirve {eq:,.0f}$ olarak yeniden başlatıldı.")
    else:
        agent = Polat(cfg, root, rest=Rest(cfg))
        agent.write_report()
        print((root / "RAPOR.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
