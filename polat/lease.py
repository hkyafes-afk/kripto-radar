"""Nöbet (lease) ve sıcak devir: GitHub'ın 6 saatlik iş sınırına rağmen 7/24 TEK Polat.

  nöbetçi → polat-veri dalındaki lease.json'u her dakika "hb" ile yeniler; bitişten halef_once_dk önce halefi
            çağırır; bitişte halef görünmüyorsa uzatma_dk kadar uzar; sonra durumu kaydedip released=true yazar
  halef   → çağrılınca ısınır (veri, abonelikler, göstergeler) ama işlem yapmaz; nöbet boşalınca devralır
  bekçi   → zamanlanmış tetik: nöbet sağlıklıysa hiçbir şey yapmadan çıkar; nabız durmuşsa (çökme) devralır

28.10.2026 kesintilerinden çıkan kurallar:
  1. Nöbet ATOMİK alınır: lease.json uzak dalın başının üstüne yazılır ve yalnızca ileri-sarma (fast-forward)
     ile gönderilir. İki aday aynı anda denerse yalnızca birinin gönderimi kabul edilir; diğeri çekilir.
     (16:28'de selef bırakınca halef ve bekçi aynı anda devralmıştı.)
  2. Nöbetini kaybeden durur (fencing): nabız gönderimi reddedilir ve uzaktaki sahip başka koşuysa hemen durur,
     hiçbir şey göndermez. (09:50'de 100 MB dosya sınırı yüzünden nabız gönderilemedi, bekçi devraldı ve eski koşu
     3 saat daha işlem yaptı: iki Polat aynı anda.)
  3. Nöbetçi yalnızca son BAŞARILI nabızdan GECERLI_S saniye sonrasına kadar yeni işlem açar. Nabız
     gönderilemiyorsa, başka bir koşu devralabilmeden (HB_STALE_S) önce işlem açmayı keser.
  4. Uzak okunamazsa (ağ/git hatası) nöbetçi ölü SAYILMAZ; bekçi devralmaz.
  5. Nöbeti yalnızca sahibi bırakır (iptal/çökme adımları başka koşunun nöbetini bırakamaz).
  6. Halef çağrısının HTTP sonucu günlüğe yazılır, başarısızsa her dakika yeniden denenir.
     (Dün halef çağrısı 8 kez sessizce başarısız oldu; sebep, iş akışının devre dışı bırakılmasıydı.)
  7. GitHub'ın 100 MB dosya sınırına dayanan dosya gönderimden çıkarılır ve uyarılır; zincir kilitlenmez.

Yalnızca standart kütüphane: iş akışının ilk adımında (Python paketleri kurulmadan) da çalışır.
  python3 -m polat.lease kontrol                         → bu koşu çalışmalı mı? (GITHUB_OUTPUT: calis=0/1)
  python3 -m polat.lease birak --kok polat_veri --neden X [--yeniden-baslat --basla EPOCH]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BRANCH = "polat-veri"
WORKFLOW = "polat.yml"
HB_STALE_S = 180            # nabız bundan eskiyse nöbetçi ölü sayılır
GECERLI_S = 150             # nöbetçi kendi nöbetini son başarılı nabızdan bu kadar sonrasına kadar geçerli sayar
HALEF_ONCELIK_S = 300       # bu kadar yeni bırakılmış nöbette bekçi önce halefin devralmasını bekler
BEKCI_BEKLEME_S = 120
UYARI_DOSYA = 70_000_000    # bayt: bu boyuttaki dosya için uyarı
BUYUK_DOSYA = 95_000_000    # bayt: GitHub 100 MB'ta gönderimi reddeder → bu dosya takipten çıkarılır
IDENT = ["-c", "user.name=polat", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com"]


def _tail(s: str | None, n: int = 300) -> str:
    return (s or "").strip()[-n:]


def run_id() -> str:
    return os.environ.get("GITHUB_RUN_ID") or f"yerel-{os.getpid()}-{int(time.time())}"


def remote_url() -> str | None:
    if os.environ.get("POLAT_LEASE_REMOTE"):                     # testler: yerel çıplak depo
        return os.environ["POLAT_LEASE_REMOTE"]
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (tok and repo):
        return None
    return f"https://x-access-token:{tok}@github.com/{repo}.git"


def healthy(lease: dict | None, now: float | None = None) -> bool:
    """Nöbette yaşayan bir sahip var mı?"""
    if not lease or lease.get("released"):
        return False
    return (now or time.time()) - float(lease.get("hb") or 0) < HB_STALE_S


class Lease:
    """polat_veri klasörü (polat-veri dalının klonu) üzerinde nöbet işlemleri. Git işlemleri bir kilitle sıralanır:
    nabız iş parçacığı ile bırakma aynı anda depoya dokunmaz."""

    def __init__(self, root: Path, rid: str | None = None):
        self.root = Path(root)
        self.file = self.root / "lease.json"
        self.run_id = rid or run_id()
        self.ok_until = 0.0          # time.time() üst sınırı: bu ana kadar nöbetin bizde olduğu kanıtlı
        self.lost = False
        self.closed = False          # bırakıldıktan sonra geç kalmış bir nabız nöbeti yeniden canlandırmasın
        self.last_error = ""
        self.fail_streak = 0
        self.big_files: list[tuple[str, int]] = []
        self.dropped: list[str] = []
        self._fetched = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ git
    def _git(self, *args: str, timeout: float = 90) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(list(args), 124, "", f"zaman aşımı: git {' '.join(args[:2])}")

    def _clear_stale_lock(self):
        lk = self.root / ".git" / "index.lock"           # öldürülen bir git sürecinden kalan kilit
        try:
            if lk.exists() and time.time() - lk.stat().st_mtime > 60:
                lk.unlink()
        except OSError:
            pass

    def fetch(self) -> tuple[bool, dict | None]:
        """Uzak daldaki lease.json. Döner (okundu_mu, lease).
        okundu=False → ağ/git hatası: nöbetçi ölü SAYILMAZ. Dal ya da dosya yoksa (True, None)."""
        r = self._git("fetch", "-q", "origin", BRANCH)
        if r.returncode != 0:
            ls = self._git("ls-remote", "--exit-code", "--heads", "origin", BRANCH)
            if ls.returncode == 2:                          # dal hiç yok: ilk kurulum
                self._fetched = False
                return True, None
            self.last_error = _tail(r.stderr) or f"git fetch {r.returncode}"
            return False, None
        self._fetched = True
        r = self._git("show", "FETCH_HEAD:lease.json")
        if r.returncode != 0:
            return True, None
        try:
            return True, json.loads(r.stdout)
        except json.JSONDecodeError:
            return True, None

    def _guard_big_files(self):
        """100 MB'a dayanan dosya bütün gönderimleri kilitler (28.10: kararlar.jsonl). Uyar; sınıra gelirse takipten çıkar."""
        big = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            if Path(dirpath) == self.root and ".git" in dirnames:
                dirnames.remove(".git")
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    s = p.stat().st_size
                except OSError:
                    continue
                if s < UYARI_DOSYA:
                    continue
                rel = p.relative_to(self.root).as_posix()
                big.append((rel, s))
                if s >= BUYUK_DOSYA and rel not in self.dropped:
                    self._git("rm", "-q", "--cached", "--ignore-unmatch", rel)
                    ex = self.root / ".git" / "info" / "exclude"
                    ex.parent.mkdir(parents=True, exist_ok=True)
                    with ex.open("a", encoding="utf-8") as f:
                        f.write(f"\n/{rel}\n")
                    self.dropped.append(rel)
        self.big_files = big

    def _write(self, released: bool) -> float:
        hb = time.time()
        self.file.write_text(json.dumps({"owner": self.run_id, "hb": hb, "released": released}), encoding="utf-8")
        return hb

    def _commit(self, msg: str):
        self._clear_stale_lock()
        self._guard_big_files()
        self._git("add", "-A")
        self._git(*IDENT, "commit", "-q", "--no-verify", "-m", f"🎯 polat: {msg}")

    def _commit_push(self, released: bool, msg: str) -> bool:
        hb = self._write(released)
        self._commit(msg)
        r = self._git("push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")      # zorlamasız: yalnızca ileri-sarma
        if r.returncode == 0:
            self.ok_until = 0.0 if released else hb + GECERLI_S
            self.fail_streak = 0
            self.last_error = ""
            return True
        self.last_error = _tail(r.stderr) or f"git push {r.returncode}"
        return False

    # ------------------------------------------------------------------ nöbet
    def acquire(self, msg: str = "nöbet alındı", tries: int = 4) -> bool:
        """Atomik alma: uzak başa eşitlen, lease'i yaz, ileri-sarma ile gönder. Araya biri girerse gönderim reddedilir;
        yeniden okununca sağlıklı başka sahip görülürse çekilir. Yerel klasör uzak durumla değiştirilir."""
        with self._lock:
            for attempt in range(tries):
                ok, cur = self.fetch()
                if not ok:
                    time.sleep(min(10, 2 + 3 * attempt))
                    continue
                if healthy(cur) and cur.get("owner") != self.run_id:
                    self.last_error = f"nöbette başka koşu var ({cur.get('owner')})"
                    return False
                if self._fetched:
                    self._clear_stale_lock()
                    self._git("reset", "-q", "--hard", "FETCH_HEAD")
                    self._git("clean", "-qfd")
                if self._commit_push(False, msg):
                    self.lost = self.closed = False
                    return True
                time.sleep(1 + attempt)
            return False

    def heartbeat(self, released: bool = False, msg: str = "nabız") -> str:
        """Nabız (ya da released=True ile bırakma). Döner: ok | fail | lost | closed."""
        with self._lock:
            if self.lost:
                return "lost"
            if self.closed:
                return "closed"
            if self._commit_push(released, msg):
                self.closed = released
                return "ok"
            ok, cur = self.fetch()
            if not ok:
                self.fail_streak += 1
                return "fail"
            if cur is not None and cur.get("owner") != self.run_id:
                self.lost = True
                self.ok_until = 0.0
                self.last_error = f"nöbet başka koşuya geçti ({cur.get('owner')})"
                return "lost"
            if self._fetched:
                # Sahip hâlâ biziz ama uzakta bizde olmayan bir commit var (elle düzeltme vb.): bizim ağacımız
                # uzak başın üstüne tek commit olur. Yarım kalan rebase/çakışma durumu oluşmaz.
                self._git("reset", "-q", "--soft", "FETCH_HEAD")
                if self._commit_push(released, msg):
                    self.closed = released
                    return "ok"
            self.fail_streak += 1
            return "fail"

    def release(self, msg: str = "nöbet bırakıldı") -> str:
        return self.heartbeat(released=True, msg=msg)

    def valid(self) -> bool:
        return not self.lost and time.time() < self.ok_until


# ---------------------------------------------------------------------- GitHub API
def _api(path: str, data: dict | None = None, timeout: float = 20):
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (tok and repo):
        raise RuntimeError("GITHUB_TOKEN/GITHUB_REPOSITORY yok")
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    req = urllib.request.Request(
        f"{base}/repos/{repo}/{path}", data=json.dumps(data).encode() if data is not None else None,
        method="POST" if data is not None else "GET",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "polat",
                 "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28"})
    return urllib.request.urlopen(req, timeout=timeout)


def dispatch_successor(devir: str = "1") -> tuple[bool, str]:
    """Halefi başlatır (GITHUB_TOKEN ile workflow_dispatch yeni koşu başlatabilir). Döner (başarılı, açıklama)."""
    ref = os.environ.get("GITHUB_REF_NAME") or "main"
    try:
        with _api(f"actions/workflows/{WORKFLOW}/dispatches", {"ref": ref, "inputs": {"devir": devir}}) as r:
            return 200 <= r.status < 300, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        try:
            body = e.read()[:300].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return False, f"HTTP {e.code}: {body}"
    except Exception as e:  # noqa: BLE001
        return False, repr(e)[:300]


def successor_seen(since_s: float) -> bool | None:
    """since_s'den (-2 dk) sonra açılmış, sırada ya da çalışan başka bir workflow_dispatch koşusu var mı?
    None: API okunamadı (bilinmiyor)."""
    me = os.environ.get("GITHUB_RUN_ID")
    try:
        with _api(f"actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&per_page=20") as r:
            runs = json.load(r).get("workflow_runs", [])
    except Exception:  # noqa: BLE001
        return None
    for x in runs:
        if str(x.get("id")) == str(me):
            continue
        if x.get("status") in ("queued", "in_progress", "waiting", "requested", "pending"):
            created = datetime.fromisoformat(str(x.get("created_at", "")).replace("Z", "+00:00")).timestamp()
            if created >= since_s - 120:
                return True
    return False


# ---------------------------------------------------------------------- iş akışı komutları
def quick_check(devir: bool, url: str | None = None) -> tuple[bool, str]:
    """İş akışının ilk adımı: Python paketleri kurulmadan önce, bu koşunun çalışması gerekip gerekmediği.
    Asıl karar `calis` içinde atomik alma ile verilir; bu yalnızca sağlıklı nöbette bekçiyi ucuzca kapatır."""
    if devir:
        return True, "devir koşusu: halef olarak ısınacak"
    url = url or remote_url()
    if not url:
        return True, "uzak depo yok (yerel)"
    d = tempfile.mkdtemp(prefix="polat_nobet_")
    try:
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout",
                            "--branch", BRANCH, url, d], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            ls = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", url, BRANCH],
                                capture_output=True, text=True, timeout=60)
            if ls.returncode == 2:
                return True, "polat-veri dalı yok: ilk kurulum"
            return False, f"nöbet okunamadı ({_tail(r.stderr, 200)}); ölü sayılmadı, sonraki bekçi bakacak"
        r = subprocess.run(["git", "-C", d, "show", "HEAD:lease.json"], capture_output=True, text=True, timeout=60)
        lease = None
        if r.returncode == 0:
            try:
                lease = json.loads(r.stdout)
            except json.JSONDecodeError:
                lease = None
        if healthy(lease):
            return False, f"nöbette sağlıklı Polat var ({lease.get('owner')}, nabız {time.time() - lease['hb']:.0f} sn önce)"
        if not lease:
            return True, "nöbet kaydı yok"
        age = time.time() - float(lease.get("hb") or 0)
        return True, (f"nöbet boş ({'bırakılmış' if lease.get('released') else 'nabız durmuş'}, "
                      f"{age / 60:.1f} dk önce, sahip {lease.get('owner')})")
    except subprocess.TimeoutExpired:
        return False, "nöbet okunamadı (zaman aşımı); ölü sayılmadı"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def release_if_owner(root: Path, reason: str) -> str:
    """İptal/çökme adımı: nöbet BU koşudaysa (yerel son durumla birlikte) bırakır; değilse dokunmaz."""
    lease = Lease(root)
    if not (Path(root) / ".git").exists():
        return "durum klasörü yok"
    ok, cur = lease.fetch()
    if not ok:
        return f"nöbet okunamadı: {lease.last_error}"
    if not cur or cur.get("owner") != lease.run_id:
        return f"nöbet bu koşuda değil ({(cur or {}).get('owner')}); dokunulmadı"
    if cur.get("released"):
        return "nöbet zaten bırakılmış"
    return lease.release(f"{reason}: nöbet bırakıldı")


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="python3 -m polat.lease")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("kontrol")
    b = sub.add_parser("birak")
    b.add_argument("--kok", default="polat_veri")
    b.add_argument("--neden", default="iptal")
    b.add_argument("--yeniden-baslat", action="store_true")
    b.add_argument("--basla", type=float, default=0.0, help="işin başladığı an (epoch sn)")
    a = ap.parse_args(argv)
    if a.cmd == "kontrol":
        devir = os.environ.get("DEVIR") == "1"
        calis, info = quick_check(devir)
        print(f"Nöbet kontrolü: {info} → {'ÇALIŞ' if calis else 'çık'}")
        if not calis and os.environ.get("DEVAM") == "true":
            print("::warning::Risk durdurmasını kaldırma isteği uygulanamadı: nöbette çalışan bir Polat var.")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"calis={'1' if calis else '0'}\n")
        return
    res = release_if_owner(Path(a.kok), a.neden)
    print(f"Nöbet bırakma ({a.neden}): {res}")
    if a.yeniden_baslat:
        ran = time.time() - a.basla if a.basla else 0
        if res != "ok":
            print("Yeniden başlatma yok: nöbet bu koşuda değildi ya da bırakılamadı.")
        elif ran < 600:
            print(f"::warning::Polat {ran:.0f} sn içinde çöktü; döngüsel çökmeye karşı yeniden başlatma bekçiye bırakıldı.")
        else:
            ok, info = dispatch_successor()
            print(f"Çökme sonrası yeni koşu: {'başlatıldı' if ok else 'BAŞLATILAMADI'} ({info})")


if __name__ == "__main__":
    main(sys.argv[1:])
