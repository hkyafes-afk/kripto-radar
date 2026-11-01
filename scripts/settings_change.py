"""Validate a narrowly allow-listed settings change without rewriting YAML comments."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode, ScalarNode

# Exact paths only. These bounds are intentionally conservative and reviewed.
ALLOWLIST = {
    "tarama.coin_sayisi": ("integer", 20, 150),
    "tarama.min_hacim_usd": ("integer", 1_000_000, 100_000_000),
    "risk.risk_yuzde": ("number", 0.1, 1.0),
    "risk.max_spread_bp": ("integer", 1, 50),
    "risk.max_kayma_bp": ("integer", 1, 50),
    "sinyal.guclu_esik": ("integer", 55, 90),
    "sinyal.izle_esik": ("integer", 35, 70),
}
FORBIDDEN_PREFIXES = ("birlesik.", "borsa.", "ekip.", "demo.", "canli.", "telegram.", "bildirim.")


class SettingsChangeError(ValueError):
    pass


def _node_for_path(root: MappingNode, path: str) -> ScalarNode:
    current: Any = root
    for part in path.split("."):
        if not isinstance(current, MappingNode):
            raise SettingsChangeError(f"Yapı beklenenden farklı: {path}")
        found = None
        for key_node, value_node in current.value:
            if isinstance(key_node, ScalarNode) and key_node.value == part:
                found = value_node
                break
        if found is None:
            raise SettingsChangeError(f"Parametre bulunamadı: {path}")
        current = found
    if not isinstance(current, ScalarNode):
        raise SettingsChangeError("Yalnız mevcut scalar parametreler değiştirilebilir.")
    return current


def apply_change(text: str, path: str, raw_value: str) -> tuple[str, dict]:
    if path not in ALLOWLIST or path.startswith(FORBIDDEN_PREFIXES):
        raise SettingsChangeError(f"Bu parametre değişiklik listesinde yok: {path}")
    kind, minimum, maximum = ALLOWLIST[path]
    try:
        value = yaml.safe_load(raw_value)
    except yaml.YAMLError as exc:
        raise SettingsChangeError("Yeni değer YAML olarak okunamadı.") from exc
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsChangeError("Bu parametre tam sayı olmalı.")
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsChangeError("Bu parametre sayısal olmalı.")
        value = float(value)
        if not math.isfinite(value):
            raise SettingsChangeError("NaN/sonsuz değer kabul edilmez.")
    if not minimum <= value <= maximum:
        raise SettingsChangeError(f"İzin verilen aralık: {minimum}–{maximum}")

    try:
        config = yaml.safe_load(text)
        root = yaml.compose(text)
        if not isinstance(config, dict) or not isinstance(root, MappingNode):
            raise SettingsChangeError("Mevcut config YAML kökü bir nesne olmalı.")
        node = _node_for_path(root, path)
    except yaml.YAMLError as exc:
        raise SettingsChangeError("Mevcut config YAML bozuk; hiçbir dosya değiştirilmedi.") from exc
    current = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise SettingsChangeError(f"Parametre config içinde yok: {path}")
        current = current[part]
    if kind == "integer" and (isinstance(current, bool) or not isinstance(current, int)):
        raise SettingsChangeError("Mevcut config tipi integer değil.")
    if kind == "number" and (isinstance(current, bool) or not isinstance(current, (int, float))):
        raise SettingsChangeError("Mevcut config tipi number değil.")

    replacement = str(value)
    updated = text[:node.start_mark.index] + replacement + text[node.end_mark.index:]
    # Parse again before any write; malformed config and cross-field threshold
    # inversions are rejected before the atomic replace.
    try:
        updated_config = yaml.safe_load(updated)
    except yaml.YAMLError as exc:
        raise SettingsChangeError("Değişiklik sonrası config YAML geçersiz; hiçbir dosya değiştirilmedi.") from exc
    if not isinstance(updated_config, dict):
        raise SettingsChangeError("Değişiklik sonrası config kökü nesne değil.")
    signal = updated_config.get("sinyal") or {}
    if ("guclu_esik" in signal and "izle_esik" in signal
            and float(signal["izle_esik"]) >= float(signal["guclu_esik"])):
        raise SettingsChangeError("sinyal.izle_esik, guclu_esik değerinden küçük olmalı.")
    return updated, {"path": path, "old_value": current, "new_value": value,
                     "range": [minimum, maximum], "approval": "pull_request_required"}



@contextmanager
def _exclusive_config_lock(target: Path):
    """Serialize apply/rollback jobs that cooperate with this config change tool."""
    lock_path = target.with_name(target.name + ".settings.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    backend = None
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            backend = "fcntl"
        except ImportError:
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            backend = "msvcrt"
        yield
    finally:
        if backend == "fcntl":
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif backend == "msvcrt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", dir=str(target.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _append_audit(audit_path: Path, record: dict) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(audit_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        written = os.write(fd, line)
        if written != len(line):
            raise OSError("audit_record_write_incomplete")
        os.fsync(fd)
    finally:
        os.close(fd)


def _audit_records(audit_path: Path) -> list[dict]:
    try:
        lines = audit_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise SettingsChangeError(f"Ayar denetim kaydı okunamadı: {exc}") from exc
    records = []
    for number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise SettingsChangeError(f"Ayar denetim kaydı bozuk (satır {number}).") from exc
        if not isinstance(record, dict):
            raise SettingsChangeError(f"Ayar denetim kaydı nesne değil (satır {number}).")
        records.append(record)
    return records


def _commit_audited_change(
    target: Path, audit_path: Path, *, original: str, updated: str,
    summary: dict, action: str, actor: str | None = None,
    run_id: str | None = None, rollback_of: str | None = None,
) -> dict:
    if target.resolve() == audit_path.resolve():
        raise SettingsChangeError("Config dosyası ve denetim dosyası aynı olamaz.")
    before_hash, after_hash = _sha256(original), _sha256(updated)
    try:
        current = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsChangeError(f"Config yeniden okunamadı: {exc}") from exc
    if _sha256(current) != before_hash:
        raise SettingsChangeError("Config eşzamanlı değişti; yeni sürümü okuyup talebi yeniden oluştur.")
    record = {
        "event_id": f"{run_id or 'local'}-{time.time_ns()}",
        "action": action, "path": summary["path"],
        "target": str(target.resolve()), "old_value": summary["old_value"],
        "new_value": summary["new_value"], "before_sha256": before_hash,
        "after_sha256": after_hash,
        "actor": actor or os.environ.get("GITHUB_ACTOR") or "local",
        "run_id": run_id or os.environ.get("GITHUB_RUN_ID"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approval": "pull_request_required",
    }
    if rollback_of:
        record["rollback_of"] = rollback_of
    _atomic_write(target, updated)
    try:
        _append_audit(audit_path, record)
    except OSError as exc:
        try:
            if _sha256(target.read_text(encoding="utf-8")) == after_hash:
                _atomic_write(target, original)
        except OSError:
            pass
        raise SettingsChangeError(f"Denetim kaydı yazılamadı; config geri alınmaya çalışıldı: {exc}") from exc
    return {**summary, **record}


def apply_file_change(
    file_path: str | Path, path: str, raw_value: str, *,
    audit_file: str | Path, expected_sha256: str | None = None,
    actor: str | None = None, run_id: str | None = None,
) -> dict:
    target, audit_path = Path(file_path), Path(audit_file)
    with _exclusive_config_lock(target):
        original = target.read_text(encoding="utf-8")
        before_hash = _sha256(original)
        if expected_sha256 and before_hash != expected_sha256:
            raise SettingsChangeError("Config SHA-256 beklenen sürümle eşleşmiyor; eşzamanlı değişiklik reddedildi.")
        updated, summary = apply_change(original, path, raw_value)
        return _commit_audited_change(target, audit_path, original=original, updated=updated,
                                      summary=summary, action="apply", actor=actor, run_id=run_id)


def rollback_file_change(
    file_path: str | Path, audit_file: str | Path, rollback_id: str, *,
    actor: str | None = None, run_id: str | None = None,
) -> dict:
    target, audit_path = Path(file_path), Path(audit_file)
    with _exclusive_config_lock(target):
        if not rollback_id.strip():
            raise SettingsChangeError("Geri alma kimliği boş olamaz.")
        records = _audit_records(audit_path)
        source = next((r for r in records if r.get("event_id") == rollback_id), None)
        if source is None or source.get("action") != "apply":
            raise SettingsChangeError("Geri alınacak uygulama denetim kaydı bulunamadı.")
        if source.get("target") != str(target.resolve()):
            raise SettingsChangeError("Denetim kaydı farklı config dosyasına ait.")
        original = target.read_text(encoding="utf-8")
        if _sha256(original) != source.get("after_sha256"):
            raise SettingsChangeError("Config, uygulamadan sonra değişmiş; güvenli geri alma için önce farkı incele.")
        updated, summary = apply_change(
            original, str(source.get("path")),
            str(source.get("old_value")),
        )
        return _commit_audited_change(target, audit_path, original=original, updated=updated,
                                      summary=summary, action="rollback", actor=actor,
                                      run_id=run_id, rollback_of=rollback_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path")
    parser.add_argument("--value")
    parser.add_argument("--file", default="config.yaml")
    parser.add_argument("--audit-file", default=".settings-change-audit.jsonl")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--rollback-id")
    args = parser.parse_args()
    try:
        if args.rollback_id:
            if args.path is not None or args.value is not None:
                raise SettingsChangeError("--rollback-id ile --path/--value birlikte kullanılamaz.")
            summary = rollback_file_change(args.file, args.audit_file, args.rollback_id)
        else:
            if not args.path or args.value is None:
                raise SettingsChangeError("Uygulama için --path ve --value zorunludur.")
            summary = apply_file_change(args.file, args.path, args.value,
                                        audit_file=args.audit_file,
                                        expected_sha256=args.expected_sha256)
    except (SettingsChangeError, OSError) as exc:
        parser.error(str(exc))
    print(yaml.safe_dump(summary, allow_unicode=True, sort_keys=True).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
