from app.fno_candle_only_symbol_alias_v1 import (
    architecture_contract,
    current_cash_symbol_aliases,
)


class StubProvider:
    def _instrument(self, symbol):
        return ("NSE", "CASH", symbol, f"NSE-{symbol}")


def test_ltim_uses_current_ltm_market_data_symbol_without_changing_logical_identity():
    provider = StubProvider()
    provider_class = provider.__class__

    before = provider._instrument("LTIM")
    assert before == ("NSE", "CASH", "LTIM", "NSE-LTIM")

    with current_cash_symbol_aliases(provider) as scoped:
        assert scoped is provider
        assert provider.__class__ is provider_class
        assert provider._instrument("LTIM") == (
            "NSE",
            "CASH",
            "LTM",
            "NSE-LTM",
        )
        assert provider._instrument("ONGC") == (
            "NSE",
            "CASH",
            "ONGC",
            "NSE-ONGC",
        )

    assert provider.__class__ is provider_class
    assert provider._instrument("LTIM") == before


def test_alias_contract_does_not_change_strategy_or_derivative_mapping():
    contract = architecture_contract()
    assert contract["scope"] == "CANDLE_ONLY_REPLAY_PROVIDER_INSTANCE"
    assert contract["aliases"]["LTIM"]["trading_symbol"] == "LTM"
    assert contract["aliases"]["LTIM"]["groww_symbol"] == "NSE-LTM"
    assert contract["provider_class_changed"] is False
    assert contract["strategy_identity_changed"] is False
    assert contract["deterministic_click_seed_changed"] is False
    assert contract["option_mapping_changed"] is False
    assert contract["futures_mapping_changed"] is False
