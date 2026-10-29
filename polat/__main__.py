"""Polat komut satırı.

  python -m polat calis  [--kok polat_veri] [--sure SANIYE] [--git-push-dk 1] [--devir] [--devam]
  python -m polat rapor  [--kok polat_veri]
  python -m polat devam  [--kok polat_veri]   → risk durdurmasını (zirveden −%15) kullanıcı onayıyla kaldırır

Nöbet ve devir kuralları: polat/lease.py. --sure, sürecin başından itibaren toplam bütçedir (halefin bekleme süresi
dahil); GitHub'ın 6 saatlik iş sınırı aşılmasın diye bitiş buna göre hesaplanır.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import time
from pathlib import Path

import yaml

from . import lease as nobet
from .agent import Polat
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


def apply_devam(root: Path) -> str:
    f = root / "durum.json"
    st = json.loads(f.read_text(encoding="utf-8"))
    eq = st["broker"]["cash"] + sum(p["margin"] for p in st["broker"]["positions"].values())
    st["risk"].update(halted=False, halt_reason="", peak=eq)   # zirve bugünden yeniden sayılır
    f.write_text(json.dumps(st, ensure_ascii=False, default=str), encoding="utf-8")
    return f"Risk durdurması kaldırıldı; zirve {eq:,.0f}$ olarak yeniden başlatıldı."


async def run(cfg: dict, root: Path, seconds: float | None, push_min: float, devir: bool = False, devam: bool = False,
              agent_factory=None):
    """Tek Polat koşusu. GitHub'da (ya da POLAT_LEASE_REMOTE ile testte) nöbet sistemiyle çalışır:
    bekçi → nöbet sağlıklıysa çıkar, değilse atomik olarak alır; halef → ısınır, nöbet boşalınca atomik olarak alır.
    Nöbet alındıktan sonra her dakika nabız; nöbetini kaybeden koşu hemen durur."""
    t_proc = time.time()
    on_gh = bool(os.environ.get("GITHUB_ACTIONS"))
    use_lease = on_gh or bool(os.environ.get("POLAT_LEASE_REMOTE"))
    dv = cfg["devir"]
    budget = float(seconds or 0)
    deadline = t_proc + budget if budget else None
    handoff_at = deadline - dv["halef_once_dk"] * 60 if (deadline and use_lease) else None
    hard_end = deadline + dv.get("uzatma_dk", 0) * 60 if deadline else None
    lease = nobet.Lease(root)
    box: dict = {"agent": None, "ready": False}
    hs = {"succ_ok": False, "succ_at": 0.0, "succ_warned": False, "report_at": 0.0, "big_warned": 0.0}
    loop = asyncio.get_running_loop()
    stop_ev, lost_ev = asyncio.Event(), asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_ev.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover
            pass

    def gate():
        return None if lease.valid() else "nöbet doğrulanamadı (nabız gönderilemiyor)"

    def new_agent() -> Polat:
        a = agent_factory() if agent_factory else Polat(cfg, root, make_rest(cfg))
        if use_lease:
            a.lease_gate = gate
        box["agent"] = a
        return a

    def jlog(rec: dict):
        a = box["agent"]
        if a is not None:
            try:
                a.journal.write("olaylar", rec)
            except Exception:  # noqa: BLE001
                pass

    async def notify(text: str):
        a = box["agent"]
        if a is not None:
            try:
                await a.notify.send(text)
            except Exception:  # noqa: BLE001
                pass

    async def heartbeat():
        """Her dakika: durumu kaydet, nabız gönder (nöbet kaybı burada anlaşılır), vakti gelince halefi çağır.
        Hiçbir hata bu döngüyü öldüremez (eskiden ölünce nabız da halef çağrısı da sessizce duruyordu)."""
        period = push_min * 60 if push_min > 0 else 60.0
        while True:
            await asyncio.sleep(period)
            try:
                a = box["agent"]
                if a is not None and box["ready"]:
                    a.save()
                    if time.time() - hs["report_at"] >= 300:          # rapor ağır: 5 dk'da bir
                        a.write_report()
                        hs["report_at"] = time.time()
                if not use_lease:
                    continue
                res = await asyncio.to_thread(lease.heartbeat)
                if res == "lost":
                    jlog({"olay": "nobet_kaybedildi", "hata": lease.last_error})
                    lost_ev.set()
                    return
                if res == "fail":
                    jlog({"olay": "nabiz_gonderilemedi", "seri": lease.fail_streak, "hata": lease.last_error})
                    print(f"Nabız gönderilemedi ({lease.fail_streak}): {lease.last_error}", flush=True)
                    if lease.fail_streak in (3, 30) or lease.fail_streak % 180 == 0:
                        await notify(f"⚠️ Nabız {lease.fail_streak} kez üst üste gönderilemedi; nöbet doğrulanamadığı için "
                                     f"yeni işlem açılmıyor.\n{lease.last_error[-200:]}")
                if lease.big_files and time.time() - hs["big_warned"] >= 6 * 3600:
                    hs["big_warned"] = time.time()
                    jlog({"olay": "buyuk_dosya", "dosyalar": lease.big_files, "cikarilan": lease.dropped})
                    await notify("⚠️ polat-veri'de GitHub'ın 100 MB sınırına yaklaşan dosya: "
                                 + ", ".join(f"{p} {s / 1e6:.0f} MB" for p, s in lease.big_files))
                if handoff_at and time.time() >= handoff_at and not hs["succ_ok"]:
                    ok, info = await asyncio.to_thread(nobet.dispatch_successor)
                    jlog({"olay": "halef_cagrildi", "ok": ok, "sonuc": info})
                    print(f"Halef çağrısı: {'tamam' if ok else 'BAŞARISIZ'} ({info})", flush=True)
                    if ok:
                        hs["succ_ok"], hs["succ_at"] = True, time.time()
                    elif not hs["succ_warned"]:
                        hs["succ_warned"] = True
                        await notify(f"⚠️ Halef başlatılamadı ({info}). Her dakika yeniden deneniyor; "
                                     f"olmazsa bu koşu {dv.get('uzatma_dk', 0)} dk uzar, sonra bekçi devralır.")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                jlog({"olay": "nabiz_hata", "hata": repr(e)[:300]})
                print(f"Nabız döngüsü hatası (devam ediyor): {e!r}", flush=True)

    async def start_agent():
        """Başlatma (OKX REST geçici hatalarına karşı 3 deneme; nabız bu sırada nöbeti canlı tutar)."""
        last = None
        for attempt in range(3):
            if stop_ev.is_set() or lost_ev.is_set():
                return
            a = new_agent()
            try:
                await a.start()
                box["ready"] = True
                return
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"Başlatma hatası ({attempt + 1}/3): {e!r}", flush=True)
                jlog({"olay": "baslatma_hata", "deneme": attempt + 1, "hata": repr(e)[:300]})
                try:
                    await a.stop("başlatma hatası")
                except Exception:  # noqa: BLE001
                    pass
                box["agent"] = None
                await asyncio.sleep(30 * (attempt + 1))
        raise RuntimeError(f"Polat başlatılamadı: {last!r}")

    hb_task = None
    acquired = False
    reason = "süre doldu"
    crash: BaseException | None = None
    try:
        if use_lease and not devir:
            # ---------------------------------------------------------------- bekçi / ilk başlatma
            ok, cur = False, None
            for i in range(3):
                ok, cur = await asyncio.to_thread(lease.fetch)
                if ok:
                    break
                await asyncio.sleep(5 * (i + 1))
            if not ok:
                print(f"Nöbet okunamadı ({lease.last_error}); nöbetçi ölü sayılmadı, bekçi çıkıyor.")
                reason = "nöbet okunamadı"
                return
            if nobet.healthy(cur) and cur.get("owner") != lease.run_id:
                print(f"Nöbette sağlıklı Polat var ({cur.get('owner')}); bekçi çıkıyor.")
                if devam:
                    print("::warning::Risk durdurmasını kaldırma isteği uygulanamadı: nöbette çalışan bir Polat var.")
                reason = "nöbet dolu"
                return
            crashed_before = bool(cur) and not cur.get("released")
            if cur and cur.get("released") and time.time() - float(cur.get("hb") or 0) < nobet.HALEF_ONCELIK_S:
                print("Nöbet az önce bırakıldı; önce ısınmış halefin devralması bekleniyor.", flush=True)
                t0 = time.time()
                while time.time() - t0 < nobet.BEKCI_BEKLEME_S and not stop_ev.is_set():
                    await asyncio.sleep(5)
                    ok2, cur2 = await asyncio.to_thread(lease.fetch)
                    if ok2 and nobet.healthy(cur2) and cur2.get("owner") != lease.run_id:
                        print(f"Halef devraldı ({cur2.get('owner')}); bekçi çıkıyor.")
                        reason = "nöbet dolu"
                        return
            if not await asyncio.to_thread(lease.acquire):
                print(f"Nöbet alınamadı: {lease.last_error}")
                reason = "nöbet alınamadı"
                return
            acquired = True
            print("Nöbet alındı" + (" (önceki koşu bırakmadan susmuştu: çökme sonrası devralma)." if crashed_before else "."),
                  flush=True)
            if devam:
                print(apply_devam(root))
            hb_task = asyncio.create_task(heartbeat())
            await start_agent()
            if crashed_before:
                jlog({"olay": "cokme_sonrasi_devralma", "onceki": (cur or {}).get("owner"),
                      "sessizlik_dk": round((time.time() - float((cur or {}).get("hb") or 0)) / 60, 1)})
                await notify(f"♻️ Önceki koşu nöbeti bırakmadan durmuştu; "
                             f"{(time.time() - float((cur or {}).get('hb') or 0)) / 60:.0f} dk sonra devralındı.")
        elif use_lease and devir:
            # ---------------------------------------------------------------- halef
            a = new_agent()
            await a.start(standby=True)
            wait_s = (dv["halef_once_dk"] + dv.get("uzatma_dk", 0) + 10) * 60
            t0, free = time.time(), False
            while time.time() - t0 < wait_s and not stop_ev.is_set():
                ok, cur = await asyncio.to_thread(lease.fetch)
                if ok and (not nobet.healthy(cur) or cur.get("owner") == lease.run_id):
                    free = True
                    break
                await asyncio.sleep(2)
            if not free:
                print("Selef bekleme süresinde nöbeti bırakmadı; ikinci Polat olmasın diye halef çıkıyor.")
                reason = "devir alınamadı"
                return
            a.journal.flush()
            if not await asyncio.to_thread(lease.acquire):
                print(f"Nöbet alınamadı ({lease.last_error}); halef çıkıyor.")
                reason = "devir alınamadı"
                return
            acquired = True
            hb_task = asyncio.create_task(heartbeat())
            info = await a.activate()
            box["ready"] = True
            print(f"Devralındı: boşluk {info.get('bosluk_sn')} sn", flush=True)
        else:
            # ---------------------------------------------------------------- yerel, nöbetsiz
            await start_agent()
            hb_task = asyncio.create_task(heartbeat()) if box["ready"] else None

        last_seen_check = 0.0
        while True:
            if stop_ev.is_set():
                reason = "sinyal"
                break
            if lost_ev.is_set():
                reason = "nöbet kaybedildi"
                break
            now = time.time()
            if deadline and now >= deadline:
                if not handoff_at:
                    break
                if hs["succ_ok"] and now - last_seen_check >= 30:
                    last_seen_check = now
                    seen = await asyncio.to_thread(nobet.successor_seen, hs["succ_at"])
                    if seen is not False:                       # görüldü ya da API okunamadı
                        break
                    jlog({"olay": "halef_gorulmedi"})
                    print("Halef koşusu görünmüyor; yeniden çağrılacak, süre uzatıldı.", flush=True)
                    hs["succ_ok"] = False
                if hard_end and now >= hard_end:
                    reason = "süre doldu (halef görülmedi; bekçi devralacak)"
                    break
            await asyncio.sleep(2)
    except Exception as e:  # noqa: BLE001
        crash = e
        reason = f"çökme: {e!r}"[:200]
    finally:
        if hb_task:
            hb_task.cancel()
        a = box["agent"]
        if a is not None and not a.stopping:
            try:
                await a.stop(reason)
            except Exception as e:  # noqa: BLE001
                print(f"Durdurma hatası: {e!r}")
        if reason == "nöbet kaybedildi":
            print(f"Nöbet başka koşuya geçti ({lease.last_error}); çift işlem olmasın diye bu koşu durdu, hiçbir şey göndermedi.")
            await notify("⚠️ Nöbet başka bir Polat koşusuna geçti; çift işlem olmasın diye bu koşu durdu.")
        elif acquired and not lease.lost:
            if a is not None and box["ready"] and reason != "sinyal":     # iptalde GitHub ~10 sn tanır: önce nöbet
                try:
                    a.write_report()
                except Exception as e:  # noqa: BLE001
                    print(f"Rapor yazılamadı: {e!r}")
            res = await asyncio.to_thread(lease.release, "çökme: nöbet bırakıldı" if crash else "nöbet bırakıldı")
            print(f"Nöbet bırakıldı: {res}", flush=True)
            if crash is not None and use_lease and time.time() - t_proc >= 600 and res == "ok":
                ok, info = await asyncio.to_thread(nobet.dispatch_successor)
                print(f"Çökme sonrası yeni koşu: {'başlatıldı' if ok else 'BAŞLATILAMADI'} ({info})")
        if a is not None and box["ready"]:
            print(json.dumps(a.status(), ensure_ascii=False, default=str)[:2000])
    if crash is not None:
        raise crash


def main():
    ap = argparse.ArgumentParser(prog="polat")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("calis")
    a.add_argument("--kok", default="polat_veri")
    a.add_argument("--sure", type=float, default=None, help="sürecin başından toplam süre (sn)")
    a.add_argument("--git-push-dk", type=float, default=1)
    a.add_argument("--devir", action="store_true")
    a.add_argument("--devam", action="store_true", help="nöbet alınınca risk durdurmasını kaldır (kullanıcı onayı)")
    b = sub.add_parser("rapor")
    b.add_argument("--kok", default="polat_veri")
    d = sub.add_parser("devam")
    d.add_argument("--kok", default="polat_veri")
    args = ap.parse_args()
    cfg = load_cfg()
    root = Path(args.kok)
    if args.cmd == "calis":
        asyncio.run(run(cfg, root, args.sure, args.git_push_dk, devir=args.devir or os.environ.get("DEVIR") == "1",
                        devam=args.devam))
    elif args.cmd == "devam":
        print(apply_devam(root))
    else:
        agent = Polat(cfg, root, rest=Rest(cfg))
        agent.write_report()
        print((root / "RAPOR.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
