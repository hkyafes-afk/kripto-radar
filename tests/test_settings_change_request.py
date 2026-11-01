import hashlib
import json

import pytest

from scripts.settings_change import (SettingsChangeError, apply_change, apply_file_change, rollback_file_change)


CONFIG = """# preserve this comment
tarama:
  coin_sayisi: 100 # keep inline comment
  min_hacim_usd: 5000000
risk:
  risk_yuzde: 0.5
  max_spread_bp: 15
  max_kayma_bp: 20
sinyal:
  guclu_esik: 65
  izle_esik: 50
birlesik:
  karar_modu: unified
  yurutme_modu: paper
"""


def test_settings_change_preserves_comments_and_updates_only_target_scalar():
    updated, summary = apply_change(CONFIG, "risk.risk_yuzde", "0.75")
    assert "risk_yuzde: 0.75" in updated
    assert "# preserve this comment" in updated
    assert "# keep inline comment" in updated
    assert "karar_modu: unified" in updated
    assert summary["old_value"] == 0.5
    assert summary["new_value"] == 0.75
    assert summary["approval"] == "pull_request_required"


@pytest.mark.parametrize("path,value", [
    ("birlesik.yurutme_modu", "live"),
    ("telegram.bot_token", "secret"),
    ("risk.max_spread_bp", "0"),
    ("risk.max_spread_bp", "51"),
    ("tarama.coin_sayisi", "20.5"),
    ("risk.risk_yuzde", ".nan"),
    ("risk.risk_yuzde", ".inf"),
])
def test_settings_change_rejects_protected_unknown_or_out_of_range_values(path, value):
    with pytest.raises(SettingsChangeError):
        apply_change(CONFIG, path, value)


def test_settings_change_rejects_missing_parameter_and_non_scalar():
    with pytest.raises(SettingsChangeError):
        apply_change(CONFIG, "risk.unknown", "1")
    with pytest.raises(SettingsChangeError):
        apply_change(CONFIG, "tarama", "1")


def test_settings_file_change_writes_audit_and_can_be_verified_rolled_back(tmp_path):
    target = tmp_path / "config.yaml"
    audit = tmp_path / ".settings-change-audit.jsonl"
    target.write_text(CONFIG, encoding="utf-8")
    expected = hashlib.sha256(CONFIG.encode("utf-8")).hexdigest()

    applied = apply_file_change(
        target, "risk.risk_yuzde", "0.75", audit_file=audit,
        expected_sha256=expected, actor="reviewer", run_id="run-123",
    )
    assert "risk_yuzde: 0.75" in target.read_text(encoding="utf-8")
    assert applied["action"] == "apply"
    assert applied["before_sha256"] == expected
    assert applied["after_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["actor"] == "reviewer"
    assert records[0]["run_id"] == "run-123"
    assert records[0]["old_value"] == 0.5 and records[0]["new_value"] == 0.75

    reverted = rollback_file_change(target, audit, applied["event_id"], actor="reviewer", run_id="rollback-1")
    assert reverted["action"] == "rollback"
    assert reverted["rollback_of"] == applied["event_id"]
    assert target.read_text(encoding="utf-8") == CONFIG
    records = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2 and records[1]["action"] == "rollback"


def test_settings_file_change_rejects_stale_expected_sha_without_write(tmp_path):
    target = tmp_path / "config.yaml"
    audit = tmp_path / "audit.jsonl"
    target.write_text(CONFIG, encoding="utf-8")
    with pytest.raises(SettingsChangeError, match="SHA-256"):
        apply_file_change(target, "risk.risk_yuzde", "0.75", audit_file=audit,
                          expected_sha256="0" * 64)
    assert target.read_text(encoding="utf-8") == CONFIG
    assert not audit.exists()


def test_settings_rollback_refuses_to_overwrite_a_later_config_edit(tmp_path):
    target = tmp_path / "config.yaml"
    audit = tmp_path / "audit.jsonl"
    target.write_text(CONFIG, encoding="utf-8")
    applied = apply_file_change(target, "risk.risk_yuzde", "0.75", audit_file=audit)
    target.write_text(target.read_text(encoding="utf-8") + "# changed after proposal\\n", encoding="utf-8")
    changed = target.read_text(encoding="utf-8")
    with pytest.raises(SettingsChangeError, match="değişmiş"):
        rollback_file_change(target, audit, applied["event_id"])
    assert target.read_text(encoding="utf-8") == changed
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 1
