"""Polat nöbeti: gerçek git (yerel çıplak depo) ve ayrı süreçlerle 7/24 süreklilik testleri.

28.10.2026'daki kesintilerin her biri burada bir test:
  - 16:28 iki koşu aynı anda devraldı              → test_atomic_acquire_only_one_winner
  - 09:50 nabız gönderilemedi, iki Polat işlem yaptı → test_push_failure_closes_gate_before_takeover_and_old_owner_steps_down
  - iptal adımı başka koşunun nöbetini bıraktı       → test_release_only_by_owner_and_no_late_heartbeat
  - git okuma hatası 'ölü' sayıldı                  → test_fetch_failure_is_not_death_and_missing_branch_is_fresh
  - 100 MB dosya bütün gönderimleri kilitledi        → test_big_file_does_not_block_pushes
  - halef zinciri, bekçi, çökme sonrası devralma     → test_processes_handoff_bekci_and_crash_takeover
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from polat import lease as L  # noqa: E402

SCRIPT = ROOT / "scripts" / "polat_veri.sh"


def _bare(tmp: Path) -> str:
    remote = str(tmp / "origin.git")
    subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
    return remote


def _pull(remote: str, d: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, POLAT_LEASE_REMOTE=remote, POLAT_VERI_DIR=str(d))
    return subprocess.run(["bash", str(SCRIPT), "pull"], env=env, capture_output=True, text=True, timeout=120)


def _remote_lease(remote: str) -> dict | None:
    r = subprocess.run(["git", "--git-dir", remote, "show", f"{L.BRANCH}:lease.json"], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def _remote_files(remote: str) -> set[str]:
    r = subprocess.run(["git", "--git-dir", remote, "ls-tree", "-r", "--name-only", L.BRANCH],
                       capture_output=True, text=True)
    return set(r.stdout.split())


class _Consts:
    """Süreleri test için kısalt, sonra geri al."""
    def __init__(self, **kw):
        self.kw, self.old = kw, {}

    def __enter__(self):
        for k, v in self.kw.items():
            self.old[k] = getattr(L, k)
            setattr(L, k, v)

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(L, k, v)


# ------------------------------------------------------------------------------------------------ birim
def test_fetch_failure_is_not_death_and_missing_branch_is_fresh():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        remote = _bare(tmp)
        assert _pull(remote, tmp / "a").returncode == 0                 # dal yok → boş başla
        a = L.Lease(tmp / "a", "A")
        ok, cur = a.fetch()
        assert ok and cur is None
        assert L.quick_check(False, remote)[0] is True                   # ilk kurulum: çalış
        assert a.acquire()
        assert L.quick_check(False, remote)[0] is False                  # sağlıklı nöbet: bekçi çıkar
        assert L.quick_check(True, remote)[0] is True                    # halef her zaman ısınır
        # uzak okunamıyor: ölü SAYILMAZ, bekçi çalışmaz
        bad = str(tmp / "yok.git")
        b = L.Lease(tmp / "a", "B")
        subprocess.run(["git", "-C", str(tmp / "a"), "remote", "set-url", "origin", bad], check=True)
        ok, cur = b.fetch()
        assert not ok and cur is None
        calis, info = L.quick_check(False, bad)
        assert calis is False and "okunamadı" in info, info
        # dal varken indirilemeyen durum klasörü BOŞ başlatılmaz (gerçek defteri ezme riski)
        r = _pull(bad, tmp / "c")
        assert r.returncode != 0 and not (tmp / "c" / ".git").exists(), r.stdout + r.stderr
        r = _pull(remote, tmp / "d")
        assert r.returncode == 0 and (tmp / "d" / "lease.json").exists()


def test_atomic_acquire_only_one_winner():
    """Selef bırakınca halef ve bekçi aynı anda devralmaya çalışır: yalnızca biri kazanmalı (her turda)."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        remote = _bare(tmp)
        _pull(remote, tmp / "s")
        s = L.Lease(tmp / "s", "S")
        assert s.acquire()
        for rnd in range(6):
            assert s.release() == "ok"
            contenders = []
            for name in ("H", "K"):
                d = tmp / f"{name}{rnd}"
                assert _pull(remote, d).returncode == 0
                contenders.append(L.Lease(d, f"{name}{rnd}"))
            res = {}
            barrier = threading.Barrier(2)

            def go(x):
                barrier.wait()
                res[x.run_id] = x.acquire()
            ths = [threading.Thread(target=go, args=(x,)) for x in contenders]
            for th in ths:
                th.start()
            for th in ths:
                th.join(60)
            winners = [k for k, v in res.items() if v]
            assert len(winners) == 1, (rnd, res)
            cur = _remote_lease(remote)
            assert cur["owner"] == winners[0] and not cur["released"]
            loser = [x for x in contenders if x.run_id != winners[0]][0]
            assert not loser.valid() and "başka koşu" in loser.last_error
            s = [x for x in contenders if x.run_id == winners[0]][0]


def test_push_failure_closes_gate_before_takeover_and_old_owner_steps_down():
    """09:50 senaryosu: nöbetçinin nabzı gönderilemiyor. Bekçi devralmadan ÖNCE eski nöbetçi işlem açmayı kesmeli;
    gönderim yeniden mümkün olunca eski nöbetçi nöbeti kaybettiğini anlamalı ve uzağı ezmemeli."""
    with _Consts(HB_STALE_S=3.0, GECERLI_S=2.0):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            remote = _bare(tmp)
            _pull(remote, tmp / "a")
            a = L.Lease(tmp / "a", "A")
            assert a.acquire() and a.valid()
            assert a.heartbeat() == "ok"
            last_ok = time.time()
            # A'nın ağı/gönderimi bozuluyor
            subprocess.run(["git", "-C", str(tmp / "a"), "remote", "set-url", "origin", str(tmp / "yok.git")], check=True)
            assert a.heartbeat() == "fail" and a.fail_streak == 1
            _pull(remote, tmp / "b")
            b = L.Lease(tmp / "b", "B")
            gate_closed_at = None
            while True:
                if gate_closed_at is None and not a.valid():
                    gate_closed_at = time.time()
                ok, cur = b.fetch()
                if not L.healthy(cur):
                    assert b.acquire()
                    took = time.time()
                    break
                time.sleep(0.1)
            assert gate_closed_at is not None and gate_closed_at < took, "eski nöbetçi devirden önce işlemi kesmeli"
            assert took - last_ok >= 3.0 - 0.05
            # A'nın gönderimi geri geldi: nöbeti kaybettiğini anlar, uzağı ezmez, bundan sonra hiçbir şey göndermez
            subprocess.run(["git", "-C", str(tmp / "a"), "remote", "set-url", "origin", remote], check=True)
            (tmp / "a" / "durum.json").write_text('{"eski": true}')
            assert a.heartbeat() == "lost" and a.lost and not a.valid()
            assert a.heartbeat() == "lost"
            cur = _remote_lease(remote)
            assert cur["owner"] == "B" and not cur["released"]
            assert "durum.json" not in _remote_files(remote) or b"eski" not in subprocess.run(
                ["git", "--git-dir", remote, "show", f"{L.BRANCH}:durum.json"], capture_output=True).stdout
            assert b.heartbeat() == "ok" and b.valid()


def test_release_only_by_owner_and_no_late_heartbeat():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        remote = _bare(tmp)
        _pull(remote, tmp / "a")
        a = L.Lease(tmp / "a", "A")
        assert a.acquire()
        # başka bir koşunun iptal/çökme adımı (eski iş akışı bunu yapıp sahibin nöbetini bırakıyordu)
        _pull(remote, tmp / "c")
        os.environ["GITHUB_RUN_ID"] = "C"
        try:
            msg = L.release_if_owner(tmp / "c", "iptal")
        finally:
            os.environ.pop("GITHUB_RUN_ID", None)
        assert "bu koşuda değil" in msg, msg
        cur = _remote_lease(remote)
        assert cur["owner"] == "A" and not cur["released"]
        # sahibin kendi iptal adımı bırakır
        os.environ["GITHUB_RUN_ID"] = "A"
        try:
            assert L.release_if_owner(tmp / "a", "iptal") == "ok"
            assert "zaten" in L.release_if_owner(tmp / "a", "iptal")
        finally:
            os.environ.pop("GITHUB_RUN_ID", None)
        assert _remote_lease(remote)["released"]
        # bırakmadan sonra geç kalmış bir nabız nöbeti yeniden canlandırmamalı
        assert a.release() == "ok" and a.heartbeat() == "closed"
        assert _remote_lease(remote)["released"]


def test_owner_tree_wins_over_foreign_commit():
    """Uzakta elle bir commit varken sahibin nabzı: yarım rebase/çakışma kalmadan gönderilir, sahip değişmez."""
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        remote = _bare(tmp)
        _pull(remote, tmp / "a")
        a = L.Lease(tmp / "a", "A")
        (tmp / "a" / "durum.json").write_text('{"n": 1}')
        assert a.acquire()
        _pull(remote, tmp / "m")
        (tmp / "m" / "durum.json").write_text('{"elle": true}')
        subprocess.run(["git", "-C", str(tmp / "m"), *L.IDENT, "commit", "-qam", "elle"], check=True)
        subprocess.run(["git", "-C", str(tmp / "m"), "push", "-q", "origin", f"HEAD:{L.BRANCH}"], check=True)
        (tmp / "a" / "durum.json").write_text('{"n": 2}')
        assert a.heartbeat() == "ok"
        out = subprocess.run(["git", "--git-dir", remote, "show", f"{L.BRANCH}:durum.json"], capture_output=True, text=True)
        assert json.loads(out.stdout) == {"n": 2}
        assert not (tmp / "a" / ".git" / "rebase-merge").exists() and not (tmp / "a" / ".git" / "rebase-apply").exists()
        assert _remote_lease(remote)["owner"] == "A"


def test_big_file_does_not_block_pushes():
    with _Consts(UYARI_DOSYA=1_000, BUYUK_DOSYA=5_000):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            remote = _bare(tmp)
            _pull(remote, tmp / "a")
            a = L.Lease(tmp / "a", "A")
            assert a.acquire()
            (tmp / "a" / "gunluk").mkdir()
            (tmp / "a" / "gunluk" / "kararlar.jsonl").write_text("x" * 3_000)
            assert a.heartbeat() == "ok"
            assert a.big_files and a.big_files[0][0] == "gunluk/kararlar.jsonl" and not a.dropped
            assert "gunluk/kararlar.jsonl" in _remote_files(remote)
            (tmp / "a" / "gunluk" / "kararlar.jsonl").write_text("x" * 8_000)
            assert a.heartbeat() == "ok"
            assert a.dropped == ["gunluk/kararlar.jsonl"]
            assert "gunluk/kararlar.jsonl" not in _remote_files(remote)
            assert (tmp / "a" / "gunluk" / "kararlar.jsonl").exists(), "dosya diskte kalmalı"
            assert a.heartbeat() == "ok"


# ------------------------------------------------------------------------------------------------ süreçler
CHILD = r"""
import asyncio, json, os, sys, time, subprocess
sys.path.insert(0, %(root)r)
from pathlib import Path
import polat.lease as L
L.HB_STALE_S, L.GECERLI_S, L.HALEF_ONCELIK_S, L.BEKCI_BEKLEME_S = 4.0, 3.0, 8.0, 4.0
from polat import __main__ as M

ME = os.environ["GITHUB_RUN_ID"]
LOG = os.environ["TEST_LOG"]

def log(kind, **kw):
    with open(LOG, "a") as f:
        f.write(json.dumps({"k": kind, "who": ME, "t": time.time(), **kw}) + "\n")

class J:
    def write(self, kind, rec):
        log("olay", **{"olay": rec.get("olay")})
    def flush(self):
        pass

class N:
    async def send(self, text):
        log("bildirim", text=text)

class FakeAgent:
    def __init__(self, root):
        self.root = Path(root); self.journal = J(); self.notify = N()
        self.stopping = False; self.lease_gate = None; self.standby = False; self.task = None; self.n = 0
    def _load(self):
        p = self.root / "durum.json"
        self.n = json.loads(p.read_text())["n"] if p.exists() else 0
    async def _trade(self):
        while True:
            await asyncio.sleep(0.1)
            if not self.standby and (self.lease_gate is None or self.lease_gate() is None):
                log("islem")
    async def start(self, standby=False):
        self.standby = standby
        if not standby:
            self._load(); log("basladi", n=self.n)
        self.task = asyncio.create_task(self._trade())
    async def activate(self):
        self._load(); self.standby = False; log("devraldi", n=self.n)
        return {"bosluk_sn": 0.0}
    async def stop(self, reason="?"):
        self.stopping = True
        if self.task:
            self.task.cancel()
        log("durdu", reason=reason, n=self.n)
    def save(self):
        if not self.standby:
            self.n += 1
            (self.root / "durum.json").write_text(json.dumps({"n": self.n, "by": ME}))
    def write_report(self):
        pass
    def status(self):
        return {"n": self.n}

def spawn(rid, devir):
    env = dict(os.environ, GITHUB_RUN_ID=rid, DEVIR=devir, NEXT_ID="", CHILD_ROOT=os.environ["CHILD_ROOT"] + "_" + rid)
    subprocess.run(["bash", %(script)r, "pull"], env=dict(env, POLAT_VERI_DIR=env["CHILD_ROOT"]), check=True,
                   capture_output=True)
    p = subprocess.Popen([sys.executable, "-c", os.environ["CHILD_CODE"]], env=env)
    with open(os.environ["PIDS"], "a") as f:
        f.write(f"{p.pid}\n")

nxt = os.environ.get("NEXT_ID")
def disp(devir="1"):
    if nxt:
        spawn(nxt, "1"); log("halef_cagrildi")
        return True, "HTTP 204"
    return False, "HTTP 422: test zincir sonu"
L.dispatch_successor = disp
L.successor_seen = lambda since: True if nxt else False

cfg = M.load_cfg()
cfg["devir"].update(halef_once_dk=float(os.environ.get("HALEF_ONCE_DK", "0.05")), uzatma_dk=0.05, kayit_sn=1)
root = Path(os.environ["CHILD_ROOT"])
asyncio.run(M.run(cfg, root, float(os.environ["BUDGET"]), 0.02, devir=os.environ.get("DEVIR") == "1",
                  agent_factory=lambda: FakeAgent(root)))
log("cikti")
"""


def _events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def test_processes_handoff_bekci_and_crash_takeover():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        remote = _bare(tmp)
        log = tmp / "log.jsonl"
        pids = tmp / "pids"
        code = CHILD % {"root": str(ROOT), "script": str(SCRIPT)}

        def env(rid, budget, devir="0", nxt="", **kw):
            e = dict(os.environ, POLAT_LEASE_REMOTE=remote, GITHUB_RUN_ID=rid, DEVIR=devir, TEST_LOG=str(log),
                     CHILD_ROOT=str(tmp / f"d_{rid}"), CHILD_CODE=code, PIDS=str(pids), BUDGET=str(budget), NEXT_ID=nxt,
                     **kw)
            e.pop("GITHUB_ACTIONS", None)
            e.pop("GITHUB_TOKEN", None)
            return e

        def start(rid, budget, devir="0", nxt="", **kw):
            e = env(rid, budget, devir, nxt, **kw)
            assert subprocess.run(["bash", str(SCRIPT), "pull"], env=dict(e, POLAT_VERI_DIR=e["CHILD_ROOT"]),
                                  capture_output=True).returncode == 0
            return subprocess.Popen([sys.executable, "-c", code], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True)

        # 1) A (bekçi, boş depo) nöbeti alır; zincir: A → H1 (halef) ; H1'in halefi başlatılamaz → uzar, sonra bırakır
        pa = start("A", 9, nxt="H1")
        time.sleep(3)
        # 2) A sağlıklıyken gelen bekçi hemen çıkar
        pk = start("K", 30)
        so, se = pk.communicate(timeout=60)
        assert "bekçi çıkıyor" in so, (so, se[-800:])
        so, se = pa.communicate(timeout=90)
        assert pa.returncode == 0, se[-1500:]
        # H1'in bitmesini bekle (bütçe 8 sn + uzatma 3 sn)
        deadline = time.time() + 90
        while time.time() < deadline and not any(e["k"] == "cikti" and e["who"] == "H1" for e in _events(log)):
            time.sleep(0.5)
        ev = _events(log)
        assert any(e["k"] == "cikti" and e["who"] == "H1" for e in ev), ev[-20:]
        trades = {}
        for e in ev:
            if e["k"] == "islem":
                trades.setdefault(e["who"], []).append(e["t"])
        assert set(trades) == {"A", "H1"}, set(trades)
        assert max(trades["A"]) < min(trades["H1"]), "devirde iki Polat aynı anda işlem yapmamalı"
        dev = [e for e in ev if e["k"] == "devraldi" and e["who"] == "H1"][0]
        a_stop = [e for e in ev if e["k"] == "durdu" and e["who"] == "A"][0]
        assert dev["n"] >= a_stop["n"] - 1 and dev["n"] > 0, (dev, a_stop)          # durum kaldığı yerden
        gap = min(trades["H1"]) - max(trades["A"])
        print(f"  devir boşluğu (son işlem → ilk işlem) ≈ {gap:.1f} sn")
        assert gap < 15, gap
        h1_events = [e.get("olay") for e in ev if e["who"] == "H1" and e["k"] == "olay"]
        assert "halef_cagrildi" in h1_events, "başarısız halef çağrısı da günlüğe yazılmalı"
        assert any(e["k"] == "bildirim" and "Halef başlatılamadı" in e["text"] and "422" in e["text"] for e in ev)
        cur = _remote_lease(remote)
        assert cur["owner"] == "H1" and cur["released"], cur

        # 3) çökme: C nöbeti alır ve öldürülür (bırakamaz) → bekçi D nabız eskiyince devralır, durum kaybolmaz
        pc = start("C", 120)
        t0 = time.time()
        while time.time() - t0 < 60 and not any(e["k"] == "islem" and e["who"] == "C" for e in _events(log)):
            time.sleep(0.2)
        time.sleep(2.5)
        pc.send_signal(signal.SIGKILL)
        pc.communicate(timeout=30)
        killed_at = time.time()
        assert not _remote_lease(remote)["released"]
        time.sleep(4.5)                                     # bekçi ancak nabız eskiyince gelir (HB_STALE_S=4)
        pd_ = start("D", 6)
        so, se = pd_.communicate(timeout=120)
        assert pd_.returncode == 0, se[-1500:]
        assert "çökme sonrası devralma" in so, so
        ev = _events(log)
        c_tr = [e["t"] for e in ev if e["k"] == "islem" and e["who"] == "C"]
        d_tr = [e["t"] for e in ev if e["k"] == "islem" and e["who"] == "D"]
        assert c_tr and d_tr and max(c_tr) < min(d_tr) and min(d_tr) >= killed_at
        assert any(e["k"] == "bildirim" and "devralındı" in e["text"] and e["who"] == "D" for e in ev)
        d_start = [e for e in ev if e["k"] == "basladi" and e["who"] == "D"][0]
        assert d_start["n"] >= 1, "çöken koşunun son gönderilmiş durumu devralınmalı"
        cur = _remote_lease(remote)
        assert cur["owner"] == "D" and cur["released"]
        for pid in (pids.read_text().split() if pids.exists() else []):
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass


def test_real_agent_bekci_then_halef_activate():
    """Gerçek Polat (sahte borsa/akışla): bekçi nöbeti alır, çalışır, halefi başlatamayınca uzar ve bırakır;
    ardından halef ısınır, devralır (günlük ve durum diskten yeniden), çalışır ve bırakır."""
    import asyncio
    import copy
    sys.path.insert(0, str(ROOT / "tests"))
    import test_polat as TP
    from polat import __main__ as M
    from polat.agent import Polat
    from polat.journal import Journal
    saved = (L.dispatch_successor, L.successor_seen)
    calls = []
    L.dispatch_successor = lambda devir="1": calls.append(1) or (False, "HTTP 422: test")
    L.successor_seen = lambda since: False
    old_env = {k: os.environ.get(k) for k in ("GITHUB_RUN_ID", "POLAT_LEASE_REMOTE", "GITHUB_ACTIONS")}
    try:
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            remote = _bare(tmp)
            os.environ["POLAT_LEASE_REMOTE"] = remote
            os.environ.pop("GITHUB_ACTIONS", None)
            data = {"X-USDT-SWAP": TP._uptrend_data(), "BTC-USDT-SWAP": TP._uptrend_data(50_000)}
            cfg = copy.deepcopy(TP.CFG)
            cfg["secici"]["min_hacim_usd"] = 0
            cfg["secici"]["sabit"] = ["BTC-USDT-SWAP"]
            cfg["bildirim"]["telegram"] = False
            cfg["devir"].update(halef_once_dk=0.02, uzatma_dk=0.02, kayit_sn=1)

            def factory(root):
                return lambda: Polat(cfg, root, rest=TP.FakeRest(data), feed_cls=TP.FakeFeed)

            ra = tmp / "a"
            assert _pull(remote, ra).returncode == 0
            os.environ["GITHUB_RUN_ID"] = "A"
            asyncio.run(M.run(copy.deepcopy(cfg), ra, 5, 0.02, devir=False, agent_factory=factory(ra)))
            cur = _remote_lease(remote)
            assert cur["owner"] == "A" and cur["released"], cur
            assert calls, "halef çağrısı denenmeli"
            files = _remote_files(remote)
            assert {"durum.json", "gunluk/olaylar.jsonl", "lease.json"} <= files, files
            olay = [json.loads(x)["olay"] for x in subprocess.run(
                ["git", "--git-dir", remote, "show", f"{L.BRANCH}:gunluk/olaylar.jsonl"],
                capture_output=True, text=True).stdout.splitlines() if x.strip()]
            assert "basladi" in olay and "halef_cagrildi" in olay and "durdu" in olay, olay

            rh = tmp / "h"
            assert _pull(remote, rh).returncode == 0
            os.environ["GITHUB_RUN_ID"] = "H"
            asyncio.run(M.run(copy.deepcopy(cfg), rh, 4, 0.02, devir=True, agent_factory=factory(rh)))
            cur = _remote_lease(remote)
            assert cur["owner"] == "H" and cur["released"], cur
            j = Journal(rh / "gunluk")
            ev = [e["olay"] for e in j.read("olaylar")]
            assert "devraldi" in ev and ev.count("basladi") == 1, ev     # halef 'basladi' değil 'devraldi' yazar
            st = json.loads((rh / "durum.json").read_text())
            assert st["broker"]["cash"] == cfg["sermaye_usd"] or st["broker"]["cash"] > 0
    finally:
        L.dispatch_successor, L.successor_seen = saved
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            t0 = time.time()
            fn()
            print(f"✓ {name} ({time.time() - t0:.1f} sn)")
