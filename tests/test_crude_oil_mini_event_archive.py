from __future__ import annotations

import unittest

from app.crude_oil_mini_event_archive import (
    archive_contract,
    archive_record_to_context,
    event_context_records,
    visible_event_context,
)
from app.crude_oil_mini_event_reaction_v2 import build_event_reaction_family_from_records


def _archive_record(*, event_id: str, available: str, published: str, series: str = "CRUDE_NEWS", payload=None):
    return {
        "event_id": event_id,
        "series": series,
        "event_type": "TEST_EVENT",
        "published_at_utc": published,
        "available_at_ist": available,
        "timestamp_quality": "EXACT_TEST",
        "pit_usable": True,
        "headline": "Synthetic headline must never imply direction",
        "facts": "Synthetic fact",
        "mechanism_tags": ["TEST"],
        "source": "synthetic_test",
        "source_url": "https://example.test/event",
        "event_payload": payload or {},
    }


class CrudeOilMiniEventArchiveTests(unittest.TestCase):
    def test_utc_date_does_not_override_ist_availability(self):
        archive = {
            "records": [
                _archive_record(
                    event_id="late_aug30_utc",
                    published="2026-08-30T22:12:07Z",
                    available="2026-08-31T03:42:07+05:30",
                )
            ]
        }
        self.assertEqual(visible_event_context(archive, "2026-08-30T23:00:00+05:30"), [])
        visible = visible_event_context(archive, "2026-08-31T03:42:07+05:30")
        self.assertEqual([row["event_id"] for row in visible], ["late_aug30_utc"])

    def test_at_release_consensus_is_not_pre_release(self):
        raw = _archive_record(
            event_id="eia",
            series="EIA_CRUDE_INVENTORY",
            published="2026-08-05T14:30:00Z",
            available="2026-08-05T20:00:00+05:30",
            payload={
                "expectations": [
                    {"source": "before", "available_at_utc": "2026-08-05T14:15:00Z", "crude_change_mmbbl": -1.0},
                    {"source": "at_release", "available_at_utc": "2026-08-05T14:30:00Z", "crude_change_mmbbl": -2.0},
                ]
            },
        )
        row = archive_record_to_context(raw)
        payload = row["value"]["event_payload"]
        self.assertTrue(payload["pre_release_consensus_available"])
        self.assertEqual([item["source"] for item in payload["expectations_pre_release"]], ["before"])
        statuses = {item["source"]: item["pre_release_usable"] for item in payload["expectations"]}
        self.assertEqual(statuses, {"before": True, "at_release": False})

    def test_archive_does_not_infer_direction_or_reaction(self):
        row = archive_record_to_context(
            _archive_record(
                event_id="headline",
                published="2026-08-10T01:00:00Z",
                available="2026-08-10T06:30:00+05:30",
            )
        )
        self.assertEqual(row["value"]["mechanism_stance"], "UNKNOWN")
        self.assertEqual(row["value"]["materiality_status"], "UNASSESSED")
        self.assertFalse(row["value"]["reaction"]["confirmed"])
        self.assertFalse(row["value"]["headline_sentiment_inferred"])

    def test_multiple_same_series_events_are_preserved(self):
        archive = {
            "records": [
                _archive_record(event_id="a", published="2026-08-04T03:00:00Z", available="2026-08-04T08:30:00+05:30"),
                _archive_record(event_id="b", published="2026-08-04T10:00:00Z", available="2026-08-04T15:30:00+05:30"),
            ]
        }
        rows = event_context_records(archive)
        self.assertEqual([row["event_id"] for row in rows], ["a", "b"])
        family = build_event_reaction_family_from_records(rows, "2026-08-04T16:00:00+05:30")
        self.assertEqual(family["detail"]["visible_event_count"], 2)
        self.assertEqual(family["state"], "CONTEXT_ONLY")

    def test_contract_is_shadow_only(self):
        contract = archive_contract()
        self.assertEqual(contract["current_mind_effect"], "NONE")
        self.assertFalse(contract["headline_direction_inference"])
        self.assertFalse(contract["reaction_backfill"])
        self.assertFalse(contract["at_release_consensus_is_pre_release"])


if __name__ == "__main__":
    unittest.main()
