import pytest

from app.options_only_policy import (
    assert_option_action,
    assert_option_contract,
    mark_underlying_reference,
    options_only_policy,
)


def test_options_only_policy_forbids_futures_execution():
    policy=options_only_policy()
    assert policy["trade_instruments"]==["OPTIONS"]
    assert policy["futures_execution_allowed"] is False
    assert policy["underlying_reference_allowed"] is True


def test_option_contract_guard_accepts_ce_pe_only():
    assert assert_option_contract({"option_type":"CE","trading_symbol":"COPPER23SEP261400CE"})["option_type"]=="CE"
    assert assert_option_contract({"option_type":"PE","trading_symbol":"COPPER23SEP261400PE"})["option_type"]=="PE"
    with pytest.raises(ValueError):
        assert_option_contract({"trading_symbol":"COPPER30SEP26FUT"})


def test_option_action_guard_rejects_futures_buy_sell():
    assert assert_option_action("BUY CE")=="BUY CE"
    assert assert_option_action("BUY PE")=="BUY PE"
    with pytest.raises(ValueError):
        assert_option_action("BUY")
    with pytest.raises(ValueError):
        assert_option_action("SELL")


def test_underlying_reference_is_never_execution_eligible():
    ref=mark_underlying_reference({"last_price":1400.5})
    assert ref["reference_only"] is True
    assert ref["execution_eligible"] is False
