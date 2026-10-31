"""Nöbet devri: iki ayrı süreç, yerel git deposu üzerinden gerçek devir testi."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

B_SCRIPT = r"""
import os, sys, json, time
sys.path.insert(0, %r)
from pathlib import Path
from radar.engine import Engine
from radar.demo import DemoClient
e = Engine(Path(%r), client=DemoClient(), notify=False, demo=True)
e.publish_on = True
t0 = time.time()
ok = e.acquire_lease()
print(json.dumps({"ok": ok, "waited": round(time.time() - t0, 1), "open": len(e.ledger["open"]),
                  "team": bool(e.state.get("team_started")), "marker": e.state.get("marker"),
                  "handoff_s": e.state.get("handoff_s"), "kind": e.state.get("handoff_kind"),
                  "last_error": e.state.get("last_error")}))
"""


def _env(remote, run_id, devir):
    env = dict(os.environ, LEASE_REMOTE=remote, GITHUB_RUN_ID=run_id, DEVIR=devir)
    env.pop("GITHUB_TOKEN", None)
    return env


def test_handoff_between_processes():
    from radar import lease
    from radar.demo import DemoClient
    from radar.engine import Engine
    from radar.config import load_config
    with tempfile.TemporaryDirectory() as tmp:
        remote = os.path.join(tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        os.environ.update(LEASE_REMOTE=remote, GITHUB_RUN_ID="A", DEVIR="0")
        try:
            cfg = load_config()
            cfg.setdefault("birlesik", {})["karar_modu"] = "legacy"
            cfg_path = Path(tmp) / "legacy-test.yaml"
            cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
            a = Engine(Path(tmp) / "a", cfg_path=cfg_path, client=DemoClient(), notify=False, demo=True)
            a.publish_on = True
            assert a.acquire_lease(), "boş depoda nöbet alınabilmeli"
            a.run_once()
            a.state["marker"] = "A-durumu"
            a.state["last_error"] = "eski hata"
            a.lease_heartbeat(force=True)
            n_open = len(a.ledger["open"])
            assert n_open > 0
            L, files = lease.read()
            assert L["holder"] == "A" and not L["released"] and "ledger.json" in files
            # bekçi (devir değil): A sağlıklıyken hemen çekilmeli
            r = subprocess.run([sys.executable, "-c", B_SCRIPT % (str(ROOT), str(Path(tmp) / "c"))],
                               env=_env(remote, "C", "0"), capture_output=True, text=True, timeout=120)
            out = json.loads(r.stdout.strip().splitlines()[-1])
            assert out["ok"] is False, r.stdout
            # halef (devir=1): bekler, A bırakınca A'nın durumunu devralır
            p = subprocess.Popen([sys.executable, "-c", B_SCRIPT % (str(ROOT), str(Path(tmp) / "b"))],
                                 env=_env(remote, "B", "1"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(8)
            assert p.poll() is None, "halef nöbet bırakılmadan devralmamalı"
            t_rel = time.time()
            a.lease_heartbeat(force=True, released=True)
            so, se = p.communicate(timeout=120)
            out = json.loads(so.strip().splitlines()[-1])
            assert out["ok"] and out["open"] == n_open and out["team"] and out["marker"] == "A-durumu", (so, se[-500:])
            took = time.time() - t_rel
            print(f"  devir süresi ≈ {took:.1f} sn (halefin ölçtüğü kesinti {out['handoff_s']} sn)")
            assert out["kind"] == "devir" and out["handoff_s"] is not None and out["handoff_s"] < 30, out
            assert out["last_error"] is None, "eski hata kaydı temizlenmeli"
            L, _ = lease.read()
            assert L["holder"] == "B" and not L["released"]
            # çift döngü koruması: A hâlâ çalışıyor sansa bile nabızda nöbeti kaybettiğini anlar, yazmaz
            a.t["lease"] = 0
            a.lease_heartbeat(force=True)
            assert a.lost_lease
            L, _ = lease.read()
            assert L["holder"] == "B", "kaybeden döngü nöbeti ezmemeli"
        finally:
            for k in ("LEASE_REMOTE", "GITHUB_RUN_ID", "DEVIR"):
                os.environ.pop(k, None)


def test_self_healing_watchdog_and_crash_restart():
    """GitHub zamanlayıcısına bağlı kalmadan: takılan döngü ve hatayla biten döngü halefini kendisi çağırır;
    elle durdurma (KeyboardInterrupt) kendini yeniden başlatmaz."""
    import threading
    from radar import config, lease
    from radar.demo import DemoClient
    from radar.engine import Engine
    calls = []
    orig = lease.dispatch_successor
    lease.dispatch_successor = lambda: calls.append(1) or True
    marker = config.ROOT / ".halef_gonderildi"
    with tempfile.TemporaryDirectory() as tmp:
        remote = os.path.join(tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        os.environ.update(LEASE_REMOTE=remote, GITHUB_RUN_ID="S", DEVIR="0")
        try:
            # 1) iç bekçi: ana döngü 5 dk+ ilerlemezse yedeği bir kez çağırır
            e = Engine(Path(tmp) / "w", client=DemoClient(), notify=False, demo=True)
            e.publish_on, e.WATCH_EVERY = True, 0.05
            e.loop_beat = time.time() - 400
            th = threading.Thread(target=e._inner_watchdog, daemon=True)
            th.start()
            t0 = time.time()
            while not calls and time.time() - t0 < 3:
                time.sleep(0.05)
            time.sleep(0.3)
            e.loop_done = True
            assert calls == [1] and e.successor_sent, calls
            assert marker.exists()
            marker.unlink()

            # 2) döngü hatayla biterse: nöbeti bırakır ve halefini çağırır
            calls.clear()
            e = Engine(Path(tmp) / "c", client=DemoClient(), notify=False, demo=True)
            e.publish_on, e.MIN_RUN_FOR_RESTART_S, e.fail_streak = True, 0, 29
            e.publish = lambda force=False: True
            e.process_hits = lambda: (_ for _ in ()).throw(RuntimeError("test çökmesi"))
            try:
                e.run_loop(minutes=30)
                raise AssertionError("hata yükselmeliydi")
            except RuntimeError:
                pass
            assert calls == [1], calls
            L, _ = lease.read()
            assert L["holder"] == "S" and L["released"], L
            marker.unlink(missing_ok=True)

            # 3) elle durdurma: halef çağrılmaz
            calls.clear()
            e = Engine(Path(tmp) / "k", client=DemoClient(), notify=False, demo=True)
            e.publish_on, e.MIN_RUN_FOR_RESTART_S = True, 0
            e.publish = lambda force=False: True
            e.process_hits = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
            try:
                e.run_loop(minutes=30)
            except KeyboardInterrupt:
                pass
            assert calls == [], calls
        finally:
            lease.dispatch_successor = orig
            marker.unlink(missing_ok=True)
            for k in ("LEASE_REMOTE", "GITHUB_RUN_ID", "DEVIR"):
                os.environ.pop(k, None)


if __name__ == "__main__":
    test_handoff_between_processes()
    print("✓ test_handoff_between_processes")
    test_self_healing_watchdog_and_crash_restart()
    print("✓ test_self_healing_watchdog_and_crash_restart")
