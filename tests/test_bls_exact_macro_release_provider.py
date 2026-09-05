import unittest
from datetime import datetime, timezone

from app.bls_exact_macro_release_provider import (
    BLS_HOST,
    BLS_RELEASE_PREFIX,
    BlsExactMacroReleasePolicy,
    BlsExactMacroReleaseProvider,
    architecture_contract,
)

CPI_URL = "https://www.bls.gov/news.release/cpi.nr0.htm"
EMP_URL = "https://www.bls.gov/news.release/empsit.nr0.htm"


def _cpi_html(*, embargo="8:30 a.m. (ET) Wednesday, August 12, 2026", headline="increased 0.1 percent", core="rose 0.2 percent"):
    return f"""
    <html><body>
      <p>Transmission of material in this release is embargoed until {embargo}</p>
      <h1>CONSUMER PRICE INDEX - JULY 2026</h1>
      <p>The Consumer Price Index for All Urban Consumers (CPI-U) {headline} on a seasonally adjusted basis in July.</p>
      <p>The index for all items less food and energy {core} over the month.</p>
    </body></html>
    """


def _employment_html(*, embargo="8:30 a.m. (ET) Friday, September 4, 2026", payroll="increased by 162,000", unemployment="was unchanged at 4.1 percent", earnings="rose by 10 cents, or 0.3 percent"):
    return f"""
    <html><body>
      <p>Transmission of material in this release is embargoed until {embargo}</p>
      <h1>THE EMPLOYMENT SITUATION - AUGUST 2026</h1>
      <p>Total nonfarm payroll employment {payroll} in August.</p>
      <p>The unemployment rate {unemployment}.</p>
      <p>In August, average hourly earnings for all employees on private nonfarm payrolls {earnings} to $36.53.</p>
    </body></html>
    """


class _Response:
    def __init__(self, text):
        self.text = text
        self.raise_calls = 0

    def raise_for_status(self):
        self.raise_calls += 1


class _Client:
    def __init__(self, text):
        self.text = text
        self.calls = []
        self.response = _Response(text)

    def get(self, url, *, timeout):
        self.calls.append((url, timeout))
        return self.response


class BlsExactMacroReleaseProviderTests(unittest.TestCase):
    def test_disabled_fetch_fails_before_network(self):
        client = _Client(_cpi_html())
        provider = BlsExactMacroReleaseProvider(client=client)
        with self.assertRaises(RuntimeError):
            provider.fetch_release(url=CPI_URL, event_type="CPI")
        self.assertEqual(client.calls, [])

    def test_url_allowlist_rejects_non_bls_http_and_non_release_paths(self):
        provider = BlsExactMacroReleaseProvider()
        first_seen = datetime(2026, 8, 12, 12, 30, 3, tzinfo=timezone.utc)
        bad_urls = [
            "https://example.com/news.release/cpi.nr0.htm",
            "http://www.bls.gov/news.release/cpi.nr0.htm",
            "https://www.bls.gov/cpi/",
            "https://bls.gov/news.release/cpi.nr0.htm",
        ]
        for url in bad_urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    provider.parse_release(_cpi_html(), url=url, event_type="CPI", first_seen_at=first_seen)

    def test_cpi_parses_dst_aware_release_time_reference_period_and_required_metrics(self):
        provider = BlsExactMacroReleaseProvider()
        release = provider.parse_release(
            _cpi_html(),
            url=CPI_URL,
            event_type="CPI",
            first_seen_at=datetime(2026, 8, 12, 12, 30, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(release.event_key, "BLS:CPI:2026-07")
        self.assertEqual(release.reference_period, "2026-07")
        self.assertEqual(release.release_at, datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(release.first_seen_at, datetime(2026, 8, 12, 12, 30, 3, tzinfo=timezone.utc))
        self.assertAlmostEqual(release.values["headline_mom_pct"], 0.1)
        self.assertAlmostEqual(release.values["core_mom_pct"], 0.2)
        self.assertEqual(release.units, {"headline_mom_pct": "PERCENT", "core_mom_pct": "PERCENT"})
        self.assertEqual(release.release_stage, "FIRST_RELEASE")
        self.assertEqual(release.revision_number, 0)

    def test_cpi_supports_decline_and_unchanged_wording(self):
        provider = BlsExactMacroReleaseProvider()
        release = provider.parse_release(
            _cpi_html(headline="fell 0.2 percent", core="was unchanged"),
            url=CPI_URL,
            event_type="CPI",
            first_seen_at=datetime(2026, 8, 12, 12, 31, tzinfo=timezone.utc),
        )
        self.assertAlmostEqual(release.values["headline_mom_pct"], -0.2)
        self.assertAlmostEqual(release.values["core_mom_pct"], 0.0)

    def test_winter_eastern_release_time_converts_to_1330_utc(self):
        provider = BlsExactMacroReleaseProvider()
        html = _cpi_html(
            embargo="8:30 a.m. (ET) Wednesday, January 14, 2026",
        ).replace("JULY 2026", "DECEMBER 2025").replace("in July", "in December")
        release = provider.parse_release(
            html,
            url=CPI_URL,
            event_type="CPI",
            first_seen_at=datetime(2026, 1, 14, 13, 30, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(release.reference_period, "2025-12")
        self.assertEqual(release.release_at, datetime(2026, 1, 14, 13, 30, tzinfo=timezone.utc))

    def test_employment_parses_payroll_unemployment_and_earnings(self):
        provider = BlsExactMacroReleaseProvider()
        release = provider.parse_release(
            _employment_html(),
            url=EMP_URL,
            event_type="EMPLOYMENT_SITUATION",
            first_seen_at=datetime(2026, 9, 4, 12, 30, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(release.event_key, "BLS:EMPLOYMENT_SITUATION:2026-08")
        self.assertEqual(release.release_at, datetime(2026, 9, 4, 12, 30, tzinfo=timezone.utc))
        self.assertAlmostEqual(release.values["payroll_change_k"], 162.0)
        self.assertAlmostEqual(release.values["unemployment_rate_pct"], 4.1)
        self.assertAlmostEqual(release.values["avg_hourly_earnings_mom_pct"], 0.3)
        self.assertEqual(release.units["payroll_change_k"], "THOUSAND_PERSONS")

    def test_employment_supports_negative_parenthetical_payroll_and_falling_earnings(self):
        html = _employment_html(
            payroll="(-23,000)",
            unemployment="rose to 4.3 percent",
            earnings="fell by 8 cents, or 0.2 percent",
        )
        provider = BlsExactMacroReleaseProvider()
        release = provider.parse_release(
            html,
            url=EMP_URL,
            event_type="EMPLOYMENT_SITUATION",
            first_seen_at=datetime(2026, 9, 4, 12, 31, tzinfo=timezone.utc),
        )
        self.assertAlmostEqual(release.values["payroll_change_k"], -23.0)
        self.assertAlmostEqual(release.values["unemployment_rate_pct"], 4.3)
        self.assertAlmostEqual(release.values["avg_hourly_earnings_mom_pct"], -0.2)

    def test_missing_embargo_heading_or_required_metric_fails_closed(self):
        provider = BlsExactMacroReleaseProvider()
        first_seen = datetime(2026, 8, 12, 12, 31, tzinfo=timezone.utc)
        cases = [
            _cpi_html().replace("embargoed until", "available after"),
            _cpi_html().replace("CONSUMER PRICE INDEX - JULY 2026", "CPI RELEASE"),
            _cpi_html().replace("The index for all items less food and energy rose 0.2 percent over the month.", "Core detail unavailable."),
        ]
        for html in cases:
            with self.assertRaises(ValueError):
                provider.parse_release(html, url=CPI_URL, event_type="CPI", first_seen_at=first_seen)

    def test_first_seen_before_official_release_is_rejected(self):
        provider = BlsExactMacroReleaseProvider()
        with self.assertRaises(ValueError):
            provider.parse_release(
                _cpi_html(),
                url=CPI_URL,
                event_type="CPI",
                first_seen_at=datetime(2026, 8, 12, 12, 29, 59, tzinfo=timezone.utc),
            )

    def test_fetch_uses_actual_clock_after_response_and_does_not_backdate_historical_page(self):
        client = _Client(_cpi_html())
        actual_fetch_time = datetime(2026, 9, 5, 8, 15, tzinfo=timezone.utc)
        provider = BlsExactMacroReleaseProvider(
            BlsExactMacroReleasePolicy(enabled=True, timeout_seconds=7.5),
            client=client,
            clock=lambda: actual_fetch_time,
        )
        release = provider.fetch_release(url=CPI_URL, event_type="CPI")
        self.assertEqual(client.calls, [(CPI_URL, 7.5)])
        self.assertEqual(client.response.raise_calls, 1)
        self.assertEqual(release.release_at, datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc))
        self.assertEqual(release.first_seen_at, actual_fetch_time)
        self.assertGreater(release.first_seen_at, release.release_at)

    def test_unsupported_event_type_fails_closed(self):
        provider = BlsExactMacroReleaseProvider()
        with self.assertRaises(ValueError):
            provider.parse_release(
                _cpi_html(),
                url=CPI_URL,
                event_type="PPI",
                first_seen_at=datetime(2026, 8, 12, 12, 31, tzinfo=timezone.utc),
            )

    def test_architecture_keeps_bls_provider_off_and_non_directional(self):
        contract = architecture_contract()
        self.assertFalse(contract["enabled_by_default"])
        self.assertEqual(contract["allowed_host"], BLS_HOST)
        self.assertEqual(contract["allowed_path_prefix"], BLS_RELEASE_PREFIX)
        self.assertTrue(contract["official_release_timestamp_from_bls_page"])
        self.assertTrue(contract["eastern_timezone_dst_aware"])
        self.assertFalse(contract["historical_page_fetch_may_be_backdated"])
        self.assertEqual(contract["cpi_metrics"], ["headline_mom_pct", "core_mom_pct"])
        self.assertEqual(contract["employment_metrics"], ["payroll_change_k", "unemployment_rate_pct", "avg_hourly_earnings_mom_pct"])
        self.assertFalse(contract["consensus_provided"])
        self.assertFalse(contract["surprise_direction_generated"])
        self.assertFalse(contract["options_trade_generated"])
        self.assertFalse(contract["futures_trade_generated"])
        self.assertFalse(contract["network_request_at_import"])


if __name__ == "__main__":
    unittest.main()
