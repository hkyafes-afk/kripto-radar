import pytest

from scripts.settings_change import SettingsChangeError, apply_change


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
