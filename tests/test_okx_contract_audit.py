from tools.okx_contract_audit import audit, render


def test_okx_audit_classifies_live_contract_minimums():
    instruments = [
        {"instId": "BTC-USDT-SWAP", "state": "live", "ctType": "linear", "settleCcy": "USDT",
         "ctValCcy": "BTC", "ctVal": "0.001", "minSz": "0.01", "lotSz": "0.01"},
        {"instId": "DOGE-USDT-SWAP", "state": "live", "ctType": "linear", "settleCcy": "USDT",
         "ctValCcy": "DOGE", "ctVal": "10", "minSz": "1", "lotSz": "1"},
        {"instId": "BIG-USDT-SWAP", "state": "live", "ctType": "linear", "settleCcy": "USDT",
         "ctValCcy": "BIG", "ctVal": "1", "minSz": "10", "lotSz": "1"},
        {"instId": "OLD-USDT-SWAP", "state": "suspend", "ctType": "linear", "settleCcy": "USDT",
         "ctValCcy": "OLD", "ctVal": "1", "minSz": "1", "lotSz": "1"},
    ]
    tickers = [
        {"instId": "BTC-USDT-SWAP", "last": "60000"},
        {"instId": "DOGE-USDT-SWAP", "last": "0.1"},
        {"instId": "BIG-USDT-SWAP", "last": "10"},
    ]
    result = audit(instruments, tickers)
    assert result["live_usdt_swap_count"] == 3
    assert result["eligible_count"] == 2
    assert result["too_large_minimum_count"] == 1
    assert result["risk_target_fit_count"] == 2
    assert {x["instId"] for x in result["eligible"]} == {"BTC-USDT-SWAP", "DOGE-USDT-SWAP"}
    assert result["eligible"][0]["min_notional_usdt"] <= result["eligible"][1]["min_notional_usdt"]
    assert "does not mean" in render(result)


def test_unknown_metadata_is_not_guessed():
    result = audit(
        [{"instId": "X-USDT-SWAP", "state": "live", "ctType": "inverse", "settleCcy": "BTC"}],
        [],
    )
    assert result["unknown_metadata_count"] == 1
    assert result["eligible_count"] == 0
