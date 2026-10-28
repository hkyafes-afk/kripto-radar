"""Nöbet (lease) ve devir: 7/24 kesintisiz çalışma için.

GitHub'da tek bir iş en fazla ~6 saat sürebildiği için döngüler birbirini devralır:
  - Nöbetteki döngü her dakika 'kalp' dalına nabız + tam durum yedeği (defter, durum) yazar.
  - Bitiş öncesi halefini başlatır; halef ısınıp bekler.
  - Nöbetteki döngü bitişte nöbeti bırakır (released); halef birkaç saniye içinde en taze durumla devralır.
  - Döngü çökerse nabız durur; bekçi (zamanlanmış iş) 3 dakikadan eski nabzı görünce yeni döngü başlatır.

'kalp' dalı GitHub Pages'e bağlı değildir; sık yazmak yayın sınırını tüketmez. Her yazım tek commit'e
sıfırlanır (force push), dal büyümez.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .config import ROOT

BRANCH = "kalp"
STALE_S = 180


def _remote() -> str | None:
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if os.environ.get("LEASE_REMOTE"):          # testler için yerel depo
        return os.environ["LEASE_REMOTE"]
    if not (tok and repo):
        return None
    return f"https://x-access-token:{tok}@github.com/{repo}.git"


def run_id() -> str:
    return os.environ.get("GITHUB_RUN_ID") or f"yerel-{os.getpid()}"


def read() -> tuple[dict | None, dict]:
    """Döner: (nöbet bilgisi, {dosya_adı: içerik})."""
    remote = _remote()
    if not remote:
        return None, {}
    d = tempfile.mkdtemp(prefix="kalp_")
    try:
        r = subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", BRANCH, remote, d],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None, {}
        files = {}
        for name in ("ledger.json", "state.json"):
            p = Path(d) / name
            if p.exists():
                files[name] = p.read_text(encoding="utf-8")
        lp = Path(d) / "lease.json"
        lease = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else None
        return lease, files
    except Exception as e:  # noqa: BLE001
        print(f"[nöbet] okunamadı: {e}")
        return None, {}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def write(lease: dict, files: dict[str, str] | None = None) -> bool:
    remote = _remote()
    if not remote:
        return False
    d = tempfile.mkdtemp(prefix="kalp_w_")
    try:
        (Path(d) / "lease.json").write_text(json.dumps(lease, ensure_ascii=False), encoding="utf-8")
        for name, content in (files or {}).items():
            (Path(d) / name).write_text(content, encoding="utf-8")
        cmds = [["git", "init", "-q", "-b", BRANCH],
                ["git", "-c", "user.name=kripto-radar", "-c",
                 "user.email=41898282+github-actions[bot]@users.noreply.github.com", "add", "-A"],
                ["git", "-c", "user.name=kripto-radar", "-c",
                 "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                 "commit", "-q", "-m", f"nabız {lease.get('holder')} {time.strftime('%H:%M:%S')}"],
                ["git", "push", "-q", "-f", remote, BRANCH]]
        for c in cmds:
            r = subprocess.run(c, cwd=d, capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                print(f"[nöbet] yazılamadı: {r.stderr[-200:]}")
                return False
        return True
    finally:
        shutil.rmtree(d, ignore_errors=True)


def is_live(lease: dict | None, me: str | None = None, now: float | None = None) -> bool:
    """Başka bir döngü şu an nöbette mi?"""
    if not lease or lease.get("released"):
        return False
    if me and lease.get("holder") == me:
        return False
    now = now or time.time()
    return now - (lease.get("hb") or 0) / 1000 < STALE_S


def dispatch_successor() -> bool:
    """Bu döngü bitmeden halefini başlatır (GITHUB_TOKEN ile workflow_dispatch yeni iş başlatabilir)."""
    import requests
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (tok and repo):
        return False
    try:
        r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/canli.yml/dispatches",
                          headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"},
                          json={"ref": os.environ.get("GITHUB_REF_NAME", "main"), "inputs": {"devir": "1"}}, timeout=20)
        print(f"[nöbet] halef başlatma: HTTP {r.status_code}")
        return r.status_code in (201, 204)
    except Exception as e:  # noqa: BLE001
        print(f"[nöbet] halef başlatılamadı: {e}")
        return False
