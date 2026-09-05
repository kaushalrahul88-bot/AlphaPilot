import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.crypto_btc_experience_runtime import (
    ENV_DATABASE_URL,
    ENV_EXPERIENCE_ENABLED,
    BtcExperienceRuntimeConfig,
    architecture_contract,
    build_btc_experience_runtime,
    persist_resolved_experience,
    runtime_status,
)


def _t(minutes=0):
    return datetime(2026, 9, 5, 11, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)


def _entry():
    return {
        "click_id": "runtime-exp-1",
        "instrument_type": "OPTIONS",
        "decision_at": _t().isoformat(),
        "future_outcome_may_rewrite_decision": False,
        "futures_route_invoked": False,
        "futures_trade_generated": False,
        "outcome_type": "TRADE_CLOSED",
        "trade_outcome": {
            "status": "SHADOW_TRADE_CLOSED",
            "exit_at": _t(30).isoformat(),
            "actual_quote_used_for_pnl": True,
            "model_reference_used_as_fill": False,
            "net_pnl_account": 10.0,
        },
        "performance_eligible": True,
    }


class _FakeStore:
    def __init__(self, database_url):
        self.database_url = database_url
        self.records = []

    async def insert_resolved(self, record):
        self.records.append(record)
        return {
            "status": "INSERTED_RESOLVED_EXPERIENCE",
            "natural_key": record.natural_key,
            "record_fingerprint": record.record_fingerprint,
        }


class CryptoBtcExperienceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_environment_disables_experience_persistence(self):
        config = BtcExperienceRuntimeConfig.from_env({})
        self.assertFalse(config.enabled)
        runtime = build_btc_experience_runtime(config)
        self.assertEqual(runtime["status"], "BTC_EXPERIENCE_RUNTIME_DISABLED")
        self.assertIsNone(runtime["store"])

    def test_enabled_runtime_requires_database(self):
        with self.assertRaises(ValueError):
            BtcExperienceRuntimeConfig.from_env({ENV_EXPERIENCE_ENABLED: "true"})

    def test_explicit_enabled_runtime_builds_store_without_connecting(self):
        config = BtcExperienceRuntimeConfig.from_env({
            ENV_EXPERIENCE_ENABLED: "true",
            ENV_DATABASE_URL: "postgresql://example/test",
        })
        runtime = build_btc_experience_runtime(config)
        self.assertEqual(runtime["status"], "BTC_EXPERIENCE_RUNTIME_READY")
        self.assertEqual(runtime["store"].database_url, "postgresql://example/test")

    async def test_disabled_persistence_writes_nothing_and_generates_no_trade(self):
        result = await persist_resolved_experience(
            BtcExperienceRuntimeConfig(),
            entry=_entry(),
            resolved_at=_t(30),
        )
        self.assertEqual(result["status"], "BTC_EXPERIENCE_PERSISTENCE_DISABLED")
        self.assertFalse(result["persisted"])
        self.assertFalse(result["trade_generated"])

    async def test_enabled_persistence_accepts_only_resolved_record_boundary(self):
        config = BtcExperienceRuntimeConfig(enabled=True, database_url="postgresql://example/test")
        fake = _FakeStore(config.database_url)
        with patch("app.crypto_btc_experience_runtime.PostgresBtcExperienceStore", return_value=fake):
            result = await persist_resolved_experience(config, entry=_entry(), resolved_at=_t(30))
        self.assertEqual(result["status"], "INSERTED_RESOLVED_EXPERIENCE")
        self.assertTrue(result["persisted"])
        self.assertFalse(result["trade_generated"])
        self.assertEqual(len(fake.records), 1)

    async def test_enabled_persistence_rejects_unresolved_entry_before_store_write(self):
        config = BtcExperienceRuntimeConfig(enabled=True, database_url="postgresql://example/test")
        fake = _FakeStore(config.database_url)
        entry = _entry()
        entry["outcome_type"] = "TRADE_UNRESOLVED"
        entry["performance_eligible"] = False
        with patch("app.crypto_btc_experience_runtime.PostgresBtcExperienceStore", return_value=fake):
            with self.assertRaises(ValueError):
                await persist_resolved_experience(config, entry=entry, resolved_at=_t(30))
        self.assertEqual(fake.records, [])

    def test_invalid_boolean_fails_closed(self):
        with self.assertRaises(ValueError):
            BtcExperienceRuntimeConfig.from_env({ENV_EXPERIENCE_ENABLED: "maybe"})

    def test_status_and_contract_have_no_implicit_activation(self):
        status = runtime_status(BtcExperienceRuntimeConfig())
        self.assertFalse(status["enabled"])
        self.assertFalse(status["automatic_startup_registration"])
        self.assertFalse(status["automatic_collection"])
        self.assertFalse(status["automatic_network_request"])
        self.assertFalse(status["market_data_capture_switch_enables_experience"])
        self.assertFalse(status["trade_generation_enabled"])

        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertTrue(contract["database_required_when_enabled"])
        self.assertFalse(contract["automatic_startup_registration"])
        self.assertFalse(contract["market_data_capture_switch_enables_experience"])
        self.assertTrue(contract["resolved_entry_and_resolved_at_required"])
        self.assertFalse(contract["unresolved_case_auto_promoted"])
        self.assertFalse(contract["market_data_pit_archive_used_for_outcomes"])
        self.assertFalse(contract["trade_generation_enabled"])


if __name__ == "__main__":
    unittest.main()
