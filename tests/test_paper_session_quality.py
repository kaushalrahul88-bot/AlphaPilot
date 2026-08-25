import unittest
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError

from app.paper_session_quality import (
    CriticalHealthChecks,
    PaperSessionAttestationRequest,
    SessionDataIncident,
    SessionHealthSnapshot,
    SessionPaperTrade,
    evaluate_paper_session,
)


SESSION_DATE = date(2026, 8, 25)
AFTER_CLOSE = datetime(2026, 8, 25, 10, 10, tzinfo=timezone.utc)


def snapshot(at, **overrides):
    checks = {
        "api": True,
        "quote": True,
        "candles": True,
        "options": True,
    }
    checks.update(overrides)
    return SessionHealthSnapshot(
        captured_at=at,
        symbol="RELIANCE",
        expiry=date(2026, 8, 27),
        checks=CriticalHealthChecks(**checks),
    )


def healthy_snapshots():
    return [
        snapshot(datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc)),
        snapshot(datetime(2026, 8, 25, 6, 30, tzinfo=timezone.utc)),
        snapshot(datetime(2026, 8, 25, 9, 0, tzinfo=timezone.utc)),
    ]


def closed_trade(**overrides):
    values = {
        "trade_id": "paper-123",
        "symbol": "RELIANCE",
        "expiry": date(2026, 8, 27),
        "status": "CLOSED",
        "paper_only": True,
        "live_execution_enabled": False,
        "order_endpoint_called": False,
        "opened_at": datetime(2026, 8, 25, 5, 0, tzinfo=timezone.utc),
        "closed_at": datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc),
        "mark_sequence": 3,
        "last_source_id": "groww-chain-source",
    }
    values.update(overrides)
    return SessionPaperTrade(**values)


def request(**overrides):
    values = {
        "session_date": SESSION_DATE,
        "evaluated_at": AFTER_CLOSE,
        "health_snapshots": healthy_snapshots(),
        "paper_trades": [closed_trade()],
    }
    values.update(overrides)
    return PaperSessionAttestationRequest(**values)


class PaperSessionQualityTests(unittest.TestCase):
    def test_clean_session_requires_full_evidence(self):
        result = evaluate_paper_session(request())

        self.assertEqual(result["status"], "CLEAN_SESSION_ATTESTED")
        self.assertEqual(result["clean_session_count_increment"], 1)
        self.assertTrue(result["eligible_for_controlled_live_evidence"])
        self.assertFalse(result["live_execution_enabled"])
        self.assertFalse(result["order_endpoint_called"])

    def test_missing_early_phase_blocks(self):
        result = evaluate_paper_session(request(health_snapshots=healthy_snapshots()[1:]))

        self.assertIn("MISSING_EARLY_HEALTH_COVERAGE", result["blockers"])
        self.assertEqual(result["clean_session_count_increment"], 0)

    def test_any_critical_failure_blocks(self):
        rows = healthy_snapshots() + [
            snapshot(datetime(2026, 8, 25, 7, 30, tzinfo=timezone.utc), options=False)
        ]
        result = evaluate_paper_session(request(health_snapshots=rows))

        self.assertIn("CRITICAL_HEALTH_FAILURE_RECORDED", result["blockers"])

    def test_data_incident_blocks(self):
        incident = SessionDataIncident(
            captured_at=datetime(2026, 8, 25, 6, 45, tzinfo=timezone.utc),
            source="AlphaPilot API",
            code="Groww option chain HTTP 502",
        )
        result = evaluate_paper_session(request(data_incidents=[incident]))

        self.assertIn("DATA_INCIDENT_RECORDED", result["blockers"])

    def test_session_must_be_finished(self):
        result = evaluate_paper_session(
            request(evaluated_at=datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc))
        )

        self.assertIn("SESSION_NOT_FINISHED", result["blockers"])

    def test_attestation_date_must_match(self):
        result = evaluate_paper_session(
            request(evaluated_at=AFTER_CLOSE + timedelta(days=1))
        )

        self.assertIn("ATTESTATION_DATE_MISMATCH", result["blockers"])

    def test_weekend_cannot_be_attested(self):
        saturday = date(2026, 8, 29)
        result = evaluate_paper_session(
            request(
                session_date=saturday,
                evaluated_at=datetime(2026, 8, 29, 10, 10, tzinfo=timezone.utc),
                health_snapshots=[],
                paper_trades=[],
            )
        )

        self.assertIn("NON_TRADING_WEEKDAY", result["blockers"])

    def test_completed_paper_trade_is_required(self):
        result = evaluate_paper_session(request(paper_trades=[]))

        self.assertIn("NO_COMPLETED_PAPER_TRADE", result["blockers"])

    def test_open_paper_position_blocks(self):
        opened = closed_trade(status="OPEN", closed_at=None)
        result = evaluate_paper_session(request(paper_trades=[opened]))

        self.assertIn("UNRESOLVED_PAPER_POSITION", result["blockers"])

    def test_verified_mark_and_source_are_required(self):
        result = evaluate_paper_session(
            request(paper_trades=[closed_trade(mark_sequence=0, last_source_id="manual")])
        )

        self.assertIn("PAPER_TRADE_HAS_NO_VERIFIED_MARK", result["blockers"])
        self.assertIn("PAPER_TRADE_SOURCE_UNVERIFIED", result["blockers"])

    def test_trade_outside_session_blocks(self):
        result = evaluate_paper_session(
            request(
                paper_trades=[
                    closed_trade(opened_at=datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc))
                ]
            )
        )

        self.assertIn("PAPER_TRADE_OPENED_OUTSIDE_SESSION", result["blockers"])

    def test_coverage_span_must_be_sufficient(self):
        rows = [
            snapshot(datetime(2026, 8, 25, 5, 31, tzinfo=timezone.utc)),
            snapshot(datetime(2026, 8, 25, 6, 0, tzinfo=timezone.utc)),
            snapshot(datetime(2026, 8, 25, 8, 30, tzinfo=timezone.utc)),
        ]
        result = evaluate_paper_session(request(health_snapshots=rows))

        self.assertIn("MISSING_EARLY_HEALTH_COVERAGE", result["blockers"])
        self.assertIn("INSUFFICIENT_SESSION_COVERAGE", result["blockers"])

    def test_health_must_match_trade_contract_expiry(self):
        rows = [
            row.model_copy(update={"expiry": date(2026, 9, 3)})
            for row in healthy_snapshots()
        ]
        result = evaluate_paper_session(request(health_snapshots=rows))

        self.assertIn("CONTRACT_HEALTH_COVERAGE_INCOMPLETE", result["blockers"])

    def test_attestation_id_is_deterministic(self):
        first = evaluate_paper_session(request())
        second = evaluate_paper_session(request())

        self.assertEqual(first["attestation_id"], second["attestation_id"])

    def test_execution_flags_cannot_be_enabled(self):
        with self.assertRaises(ValidationError):
            SessionPaperTrade(
                trade_id="paper-bad",
                symbol="RELIANCE",
                expiry=date(2026, 8, 27),
                status="CLOSED",
                paper_only=True,
                live_execution_enabled=True,
                order_endpoint_called=False,
                opened_at=datetime(2026, 8, 25, 5, 0, tzinfo=timezone.utc),
                closed_at=datetime(2026, 8, 25, 6, 0, tzinfo=timezone.utc),
                mark_sequence=1,
                last_source_id="groww-chain-source",
            )


if __name__ == "__main__":
    unittest.main()
