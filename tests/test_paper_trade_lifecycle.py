import unittest
from datetime import date, datetime, timedelta, timezone

from app.paper_trade_lifecycle import (
    ExactOptionContract,
    PaperTrade,
    PremiumObservation,
    fetch_live_option_observation,
    mark_paper_trade,
    open_paper_trade,
)
from app.risk_discipline import OperationalGates, ProposedTrade, RiskDisciplineRequest


NOW = datetime(2026, 8, 25, 5, 0, tzinfo=timezone.utc)


def gates(**overrides):
    values = {
        "account_state_verified": True,
        "executable_nse_session": True,
        "fresh_intraday_candles": True,
        "universe_scan_complete": True,
        "fno_confirmation_complete": True,
        "quality_checks_complete": True,
        "liquidity_passed": True,
    }
    values.update(overrides)
    return OperationalGates(**values)


def contract(**overrides):
    values = {
        "symbol": "RELIANCE",
        "expiry": date(2026, 8, 27),
        "strike": 3000,
        "option_type": "CE",
        "lot_size": 25,
    }
    values.update(overrides)
    return ExactOptionContract(**values)


def observation(price=100, at=NOW, source_id="obs-1", **overrides):
    values = {
        "provider": "GROWW",
        "data_status": "LIVE",
        "symbol": "RELIANCE",
        "expiry": date(2026, 8, 27),
        "strike": 3000,
        "option_type": "CE",
        "premium_price": price,
        "observed_at": at,
        "source_id": source_id,
    }
    values.update(overrides)
    return PremiumObservation(**values)


def request(**overrides):
    proposed = ProposedTrade(
        symbol="RELIANCE",
        option_type="CE",
        correlation_group="NIFTY_LARGE_CAP",
        entry_price=100,
        stop_price=90,
        target_price=117,
        lot_size=25,
        estimated_cost_rupees=50,
    )
    values = {
        "mode": "PAPER",
        "capital_rupees": 100_000,
        "proposed_trade": proposed,
        "operational_gates": gates(),
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return RiskDisciplineRequest(**values)


class PaperTradeLifecycleTests(unittest.TestCase):
    def open_trade(self):
        result = open_paper_trade(request(), contract(), observation())
        self.assertEqual(result["status"], "OPENED_PAPER")
        return PaperTrade(**result["paper_trade"])

    def test_approved_decision_opens_exact_paper_contract(self):
        result = open_paper_trade(request(), contract(), observation())

        self.assertEqual(result["status"], "OPENED_PAPER")
        self.assertEqual(result["paper_trade"]["quantity"], 75)
        self.assertEqual(result["paper_trade"]["lots"], 3)
        self.assertEqual(result["paper_trade"]["strike"], 3000)
        self.assertFalse(result["live_execution_enabled"])
        self.assertFalse(result["order_endpoint_called"])

    def test_failed_operational_gate_blocks_open(self):
        blocked_request = request(operational_gates=gates(fresh_intraday_candles=False))
        result = open_paper_trade(blocked_request, contract(), observation())

        self.assertEqual(result["status"], "OPEN_BLOCKED")
        self.assertIsNone(result["paper_trade"])
        self.assertIn("STALE_INTRADAY_CANDLES", result["blockers"])

    def test_contract_mismatch_blocks_open(self):
        result = open_paper_trade(request(), contract(strike=3020), observation())

        self.assertEqual(result["status"], "OPEN_BLOCKED")
        self.assertIn("OBSERVATION_CONTRACT_MISMATCH", result["blockers"])

    def test_stale_live_risk_decision_blocks_open(self):
        old_request = request(evaluated_at=NOW - timedelta(minutes=3))
        result = open_paper_trade(old_request, contract(), observation())

        self.assertIn("RISK_DECISION_STALE", result["blockers"])

    def test_live_price_is_re_evaluated_not_trusted_from_old_entry(self):
        result = open_paper_trade(request(), contract(), observation(price=130))

        self.assertEqual(result["status"], "OPEN_BLOCKED")
        self.assertIn("INVALID_LONG_OPTION_TARGET_GEOMETRY", result["blockers"])

    def test_target_mark_closes_and_deducts_costs(self):
        trade = self.open_trade()
        result = mark_paper_trade(
            trade,
            observation(price=117, at=NOW + timedelta(minutes=1), source_id="obs-2"),
        )

        self.assertEqual(result["status"], "CLOSED_PAPER")
        self.assertEqual(result["paper_trade"]["exit_reason"], "TARGET")
        self.assertEqual(result["paper_trade"]["realized_pnl_rupees"], 1225)
        self.assertEqual(result["verified_closed_trade"]["pnl_rupees"], 1225)
        self.assertFalse(result["live_execution_enabled"])

    def test_stop_mark_closes_with_defined_loss(self):
        trade = self.open_trade()
        result = mark_paper_trade(
            trade,
            observation(price=90, at=NOW + timedelta(minutes=1), source_id="obs-stop"),
        )

        self.assertEqual(result["paper_trade"]["exit_reason"], "STOP")
        self.assertEqual(result["paper_trade"]["realized_pnl_rupees"], -800)
        self.assertEqual(result["paper_trade"]["r_multiple"], -1.0)

    def test_open_mark_projects_conservative_initial_risk(self):
        trade = self.open_trade()
        result = mark_paper_trade(
            trade,
            observation(price=110, at=NOW + timedelta(minutes=1), source_id="obs-open"),
        )

        self.assertEqual(result["status"], "MARKED_OPEN")
        self.assertEqual(result["open_position_risk"]["risk_rupees"], 800)
        self.assertEqual(result["paper_trade"]["unrealized_pnl_rupees"], 700)

    def test_duplicate_observation_is_idempotent(self):
        trade = self.open_trade()
        result = mark_paper_trade(trade, observation(source_id=trade.last_source_id))

        self.assertEqual(result["status"], "IGNORED_DUPLICATE")
        self.assertEqual(result["paper_trade"]["mark_sequence"], 0)

    def test_manual_exit_is_explicit_and_cost_adjusted(self):
        trade = self.open_trade()
        result = mark_paper_trade(
            trade,
            observation(price=105, at=NOW + timedelta(minutes=1), source_id="obs-manual"),
            manual_exit=True,
        )

        self.assertEqual(result["paper_trade"]["exit_reason"], "MANUAL")
        self.assertEqual(result["paper_trade"]["realized_pnl_rupees"], 325)

    def test_inconsistent_browser_state_is_rejected(self):
        trade = self.open_trade()
        tampered = trade.model_copy(update={"quantity": trade.quantity + trade.lot_size})

        with self.assertRaisesRegex(ValueError, "whole-lot"):
            mark_paper_trade(
                tampered,
                observation(price=105, at=NOW + timedelta(minutes=1), source_id="obs-tampered"),
            )

    def test_historical_observation_can_exercise_pure_engine(self):
        replay = observation(provider="HISTORICAL_REPLAY", data_status="HISTORICAL")
        result = open_paper_trade(request(), contract(), replay)

        self.assertEqual(result["status"], "OPENED_PAPER")
        self.assertFalse(result["live_execution_enabled"])


class LiveObservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_groww_option_ltp_is_extracted(self):
        class Provider:
            async def option_chain(self, symbol, expiry):
                return {
                    "provider": "GROWW",
                    "expiry": expiry,
                    "data": {
                        "payload": {
                            "strikes": {
                                "3000": {
                                    "CE": {"ltp": 101.5},
                                    "PE": {"ltp": 88.0},
                                }
                            }
                        }
                    },
                }

        found = await fetch_live_option_observation(Provider(), contract())

        self.assertEqual(found.premium_price, 101.5)
        self.assertEqual(found.data_status, "LIVE")
        self.assertEqual(found.option_type, "CE")

    async def test_mock_provider_is_rejected(self):
        class Provider:
            async def option_chain(self, symbol, expiry):
                return {"provider": "MOCK", "expiry": expiry, "rows": []}

        with self.assertRaisesRegex(ValueError, "LIVE Groww"):
            await fetch_live_option_observation(Provider(), contract())


if __name__ == "__main__":
    unittest.main()
