import unittest

from app.options_only_policy import (
    assert_option_action,
    assert_option_contract,
    mark_underlying_reference,
    options_only_policy,
)


class OptionsOnlyPolicyTests(unittest.TestCase):
    def test_options_only_policy_forbids_futures_execution(self):
        policy=options_only_policy()
        self.assertEqual(policy["trade_instruments"],["OPTIONS"])
        self.assertFalse(policy["futures_execution_allowed"])
        self.assertTrue(policy["underlying_reference_allowed"])

    def test_option_contract_guard_accepts_ce_pe_only(self):
        self.assertEqual(
            assert_option_contract({
                "option_type":"CE",
                "trading_symbol":"COPPER23SEP261400CE",
            })["option_type"],
            "CE",
        )
        self.assertEqual(
            assert_option_contract({
                "option_type":"PE",
                "trading_symbol":"COPPER23SEP261400PE",
            })["option_type"],
            "PE",
        )
        with self.assertRaises(ValueError):
            assert_option_contract({"trading_symbol":"COPPER30SEP26FUT"})

    def test_option_action_guard_rejects_futures_buy_sell(self):
        self.assertEqual(assert_option_action("BUY CE"),"BUY CE")
        self.assertEqual(assert_option_action("BUY PE"),"BUY PE")
        with self.assertRaises(ValueError):
            assert_option_action("BUY")
        with self.assertRaises(ValueError):
            assert_option_action("SELL")

    def test_underlying_reference_is_never_execution_eligible(self):
        ref=mark_underlying_reference({"last_price":1400.5})
        self.assertTrue(ref["reference_only"])
        self.assertFalse(ref["execution_eligible"])


if __name__=="__main__":
    unittest.main()
