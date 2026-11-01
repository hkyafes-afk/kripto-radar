from radar.unified.settings import discover_settings


def test_discovers_nested_scalar_and_list_values_deterministically():
    config = {
        "risk": {"risk_yuzde": 0.5, "aktif": True},
        "gostergeler": {"rsi": 14},
        "tarama": {"haric": ["USDC", "DAI"]},
    }
    first = discover_settings(config)
    second = discover_settings(config)

    assert first == second
    assert first["mode"] == "read_only_discovery"
    assert first["write_enabled"] is False
    assert first["write_ready_count"] == 0
    assert first["parameter_count"] == 4
    values = {item["path"]: item for item in first["parameters"]}
    assert values["risk.aktif"]["value_type"] == "boolean"
    assert values["risk.risk_yuzde"]["value_type"] == "number"
    assert values["gostergeler.rsi"]["value_type"] == "integer"
    assert values["tarama.haric"]["value_type"] == "array"
    assert values["gostergeler.rsi"]["category"] == "indicators"
    assert values["risk.risk_yuzde"]["category"] == "risk"


def test_protects_execution_authority_and_secret_like_paths():
    config = {
        "birlesik": {
            "karar_modu": "unified",
            "yurutme_modu": "paper",
            "risk": {"max_kume": 2},
        },
        "telegram": {"bot_token": "must-not-be-exposed"},
    }
    result = discover_settings(config)
    values = {item["path"]: item for item in result["parameters"]}

    assert values["birlesik.karar_modu"]["protected"] is True
    assert values["birlesik.yurutme_modu"]["protected"] is True
    assert values["telegram.bot_token"]["protected"] is True
    assert values["telegram.bot_token"]["value"] == "***"
    assert "must-not-be-exposed" not in str(result)
    assert values["birlesik.risk.max_kume"]["protected"] is False
    assert result["write_enabled"] is False
    assert result["write_ready_count"] == 0
    assert result["unprotected_candidate_count"] == 1


def test_does_not_mutate_source_config():
    config = {"tarama": {"haric": ["USDC"]}}
    before = {"tarama": {"haric": ["USDC"]}}
    discover_settings(config)
    assert config == before



def test_catalog_exposes_only_verified_schema_metadata_without_guessing():
    result = discover_settings({"risk": {"risk_yuzde": 0.5}, "tarama": {"periyotlar": ["15m", "4H"]}})
    values = {item["path"]: item for item in result["parameters"]}
    risk = values["risk.risk_yuzde"]
    periods = values["tarama.periyotlar"]

    assert result["schema_version"] == 3
    assert "risk yüzdesi" in risk["description"]
    assert risk["schema"]["type"] == "number"
    assert risk["schema"]["minimum"] == 0.1 and risk["schema"]["maximum"] == 1.0
    assert risk["schema"]["unit"] == "%"
    assert risk["schema"]["validation"] == "fixed_allowlist_range"
    assert risk["schema"]["restart_requirement"] == "unknown"
    assert periods["description"].startswith("config.yaml parametresi:")
    assert periods["schema"]["type"] == "array"
    assert periods["schema"]["item_types"] == ["string"]
    assert periods["schema"]["enum"] is None
    assert all(not item["write_ready"] for item in values.values())


def test_verified_writable_candidates_expose_real_units_and_guardrails_but_remain_pr_only():
    result = discover_settings({
        "tarama": {"coin_sayisi": 100, "min_hacim_usd": 5_000_000},
        "risk": {"risk_yuzde": 0.5, "max_spread_bp": 15, "max_kayma_bp": 20},
        "sinyal": {"guclu_esik": 65, "izle_esik": 50},
    })
    params = {item["path"]: item for item in result["parameters"]}
    assert params["tarama.coin_sayisi"]["schema"]["unit"] == "coin"
    assert params["tarama.coin_sayisi"]["schema"]["minimum"] == 20
    assert params["tarama.coin_sayisi"]["schema"]["maximum"] == 150
    assert params["tarama.min_hacim_usd"]["schema"]["unit"] == "USD / 24 saat"
    assert params["risk.risk_yuzde"]["schema"]["unit"] == "%"
    assert params["risk.max_spread_bp"]["schema"]["unit"] == "bp"
    assert params["risk.max_kayma_bp"]["schema"]["maximum"] == 50
    assert params["sinyal.guclu_esik"]["schema"]["unit"] == "puan / 100"
    assert params["sinyal.izle_esik"]["schema"]["minimum"] == 35
    assert all(item["schema"]["validation"] == "fixed_allowlist_range" for item in params.values())
    assert all(item["write_ready"] is False for item in params.values())
    assert result["write_enabled"] is False
