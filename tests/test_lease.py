"""Nöbet devri: iki ayrı süreç, yerel git deposu üzerinden gerçek devir testi."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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
    with tempfile.TemporaryDirectory() as tmp:
        remote = os.path.join(tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        os.environ.update(LEASE_REMOTE=remote, GITHUB_RUN_ID="A", DEVIR="0")
        try:
            a = Engine(Path(tmp) / "a", client=DemoClient(), notify=False, demo=True)
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


if __name__ == "__main__":
    test_handoff_between_processes()
    print("✓ test_handoff_between_processes")
