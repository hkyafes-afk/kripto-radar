"""Nöbet (lease) ve sıcak devir — GitHub'da 6 saatlik iş sınırına rağmen kesintiye yakın süreklilik.

  nöbetçi  → lease.json'u (polat-veri dalı) her dakika "hb" ile günceller; bitişten halef_once_dk önce halefi
             çağırır; bitişte işlemi durdurur, durumu kaydeder, released=true yazıp gönderir
  halef    → çağrılınca hemen başlar, veriyi/abonelikleri/göstergeleri ısıtır ama işlem yapmaz; released görünce
             selefin son durumunu çeker, aradaki birkaç saniyeyi 1 dk mumlarla kapatır ve devralır
  bekçi    → zamanlanmış tetik: nabız taze ise hiçbir şey yapmadan çıkar; nabız durmuşsa (çökme) devralır
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

HB_STALE_S = 180


class Lease:
    def __init__(self, root: Path, script: Path):
        self.root = Path(root)
        self.script = script
        self.file = self.root / "lease.json"
        self.run_id = os.environ.get("GITHUB_RUN_ID", f"yerel-{int(time.time())}")

    def _git(self, *args, check=False):
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=check)

    def remote(self) -> dict | None:
        """Daldaki güncel lease.json (yerel kopyaya dokunmadan)."""
        r = self._git("fetch", "-q", "origin", "polat-veri")
        if r.returncode != 0:
            return None
        r = self._git("show", "FETCH_HEAD:lease.json")
        if r.returncode != 0:
            return None
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def healthy(lease: dict | None) -> bool:
        return bool(lease) and not lease.get("released") and time.time() - lease.get("hb", 0) < HB_STALE_S

    def write(self, released: bool = False):
        self.file.write_text(json.dumps({"owner": self.run_id, "hb": time.time(), "released": released}), encoding="utf-8")

    def push(self, msg: str):
        return subprocess.run(["bash", str(self.script), "push", msg], capture_output=True, text=True,
                              env={**os.environ, "POLAT_VERI_DIR": str(self.root)})

    def sync_from_remote(self):
        """Selefin son hâlini al (halefin ısınırken yazdıkları atılır)."""
        self._git("fetch", "-q", "origin", "polat-veri")
        self._git("reset", "-q", "--hard", "FETCH_HEAD")
        self._git("clean", "-qfd")

    def dispatch_successor(self) -> bool:
        repo, tok = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
        ref = os.environ.get("GITHUB_REF_NAME", "main")
        if not repo or not tok:
            return False
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/actions/workflows/polat.yml/dispatches",
            data=json.dumps({"ref": ref, "inputs": {"devir": "1"}}).encode(), method="POST",
            headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status in (200, 204)
        except Exception:  # noqa: BLE001
            return False
