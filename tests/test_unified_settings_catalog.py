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

    assert result["schema_version"] == 2
    assert risk["description"].startswith("config.yaml parametresi:")
    assert risk["schema"]["type"] == "number"
    assert risk["schema"]["minimum"] is None and risk["schema"]["maximum"] is None
    assert risk["schema"]["unit"] is None
    assert risk["schema"]["restart_requirement"] == "unknown"
    assert periods["schema"]["type"] == "array"
    assert periods["schema"]["item_types"] == ["string"]
    assert periods["schema"]["enum"] is None
    assert all(not item["write_ready"] for item in values.values())
