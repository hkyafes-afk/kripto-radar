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


def test_settings_file_change_rejects_malformed_existing_yaml_without_write(tmp_path):
    target = tmp_path / "config.yaml"
    audit = tmp_path / "audit.jsonl"
    malformed = "risk:\\n  risk_yuzde: [unterminated\\n"
    target.write_text(malformed, encoding="utf-8")
    with pytest.raises(SettingsChangeError, match="YAML bozuk"):
        apply_file_change(target, "risk.risk_yuzde", "0.75", audit_file=audit)
    assert target.read_text(encoding="utf-8") == malformed
    assert not audit.exists()


def test_settings_file_change_rejects_audit_path_collision(tmp_path):
    target = tmp_path / "config.yaml"
    target.write_text(CONFIG, encoding="utf-8")
    with pytest.raises(SettingsChangeError, match="aynı olamaz"):
        apply_file_change(target, "risk.risk_yuzde", "0.75", audit_file=target)
    assert target.read_text(encoding="utf-8") == CONFIG


def test_concurrent_settings_writers_serialize_and_reject_stale_second_writer(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    target = tmp_path / "config.yaml"
    audit = tmp_path / "audit.jsonl"
    target.write_text(CONFIG, encoding="utf-8")
    expected = hashlib.sha256(CONFIG.encode("utf-8")).hexdigest()

    def apply(value):
        try:
            return apply_file_change(target, "risk.risk_yuzde", value, audit_file=audit,
                                     expected_sha256=expected, run_id="race-" + value)
        except SettingsChangeError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(apply, ("0.75", "0.8")))
    outcomes = [first, second]
    assert sum(isinstance(item, dict) for item in outcomes) == 1
    rejected = next(item for item in outcomes if isinstance(item, SettingsChangeError))
    assert "SHA-256" in str(rejected)
    audit_rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert len(audit_rows) == 1
    assert audit_rows[0]["action"] == "apply"
    assert ".settings.lock" in " ".join(p.name for p in tmp_path.iterdir())


def test_settings_audit_write_failure_restores_original_config(tmp_path, monkeypatch):
    import scripts.settings_change as settings_change

    target = tmp_path / "config.yaml"
    audit = tmp_path / "audit.jsonl"
    target.write_text(CONFIG, encoding="utf-8")

    def fail_audit(_path, _record):
        raise OSError("disk full")

    monkeypatch.setattr(settings_change, "_append_audit", fail_audit)
    with pytest.raises(SettingsChangeError, match="geri alınmaya çalışıldı"):
        apply_file_change(target, "risk.risk_yuzde", "0.75", audit_file=audit)
    assert target.read_text(encoding="utf-8") == CONFIG


def test_settings_workflow_yaml_has_unique_keys_and_keeps_pr_review_boundary():
    import yaml
    from pathlib import Path

    workflow_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "settings-change-request.yml"
    raw = workflow_path.read_text(encoding="utf-8")

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_unique_mapping(loader, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping", node.start_mark,
                    f"duplicate key {key!r}", key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping,
    )
    document = yaml.load(raw, Loader=UniqueKeyLoader)
    dispatch = document["on"]["workflow_dispatch"]["inputs"]
    assert set(dispatch["change_mode"]["options"]) == {"apply", "rollback"}
    assert dispatch["parameter_path"]["required"] is False
    assert set(document["permissions"]) == {"contents", "pull-requests"}
    assert document["concurrency"]["cancel-in-progress"] is False
    propose = document["jobs"]["propose"]
    assert propose["if"] == "github.ref == 'refs/heads/main'"
    create_pr = next(step for step in propose["steps"] if step["name"] == "Open a draft pull request for human review")
    assert create_pr["with"]["draft"] is True
    assert create_pr["with"]["base"] == "main"
    assert "config.yaml" in create_pr["with"]["add-paths"]
    assert ".settings-change-audit.jsonl" in create_pr["with"]["add-paths"]
