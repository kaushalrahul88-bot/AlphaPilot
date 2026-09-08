from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
SHARED_GROUP = "group: alphapilot-production-heavy-render-job"
MANUAL_IF = "if: github.event_name == 'workflow_dispatch'"

# These workflows do no lightweight validation of their own; every run can place
# material load on the single production Render web process. They therefore stay
# dispatch-only.
MANUAL_ONLY = (
    "crypto-btc-first24h-15m-backtest.yml",
    "fno-15m-full-window-backtest.yml",
    "fno-candle-only-four-stock-20d-backtest.yml",
    "fno-current-expiry-history-probe.yml",
    "fno-underlying-random-edge-backtest.yml",
)

# These workflows retain their lightweight PR/push tests, but their production
# replay/dataset/audit job must require an explicit dispatch and participate in
# the same cross-workflow admission queue.
MIXED_VALIDATION_AND_PRODUCTION = (
    "crude-oil-mini-research-framework-parity.yml",
    "fno-four-stock-context-enrichment-v1.yml",
    "fno-four-stock-context-enrichment-v2.yml",
    "fno-v1-current-expiry-comparator.yml",
    "fno-v3-current-expiry-backtest.yml",
    "fno-v3-current-expiry-new-stocks.yml",
    "fno-v5-historical-holdout-a-dataset.yml",
    "fno-v6-historical-holdout-b-dataset.yml",
    "fno-v7-historical-holdout-c-dataset.yml",
)


class HeavyRenderWorkflowAdmissionTests(unittest.TestCase):
    def _read(self, name: str) -> str:
        path = WORKFLOWS / name
        self.assertTrue(path.exists(), f"missing guarded workflow: {name}")
        return path.read_text(encoding="utf-8")

    def test_production_only_workflows_are_dispatch_only_and_serialized(self) -> None:
        for name in MANUAL_ONLY:
            with self.subTest(workflow=name):
                text = self._read(name)
                self.assertIn("workflow_dispatch:", text)
                self.assertNotIn("\n  push:\n", text)
                self.assertNotIn("\n  pull_request:\n", text)
                self.assertIn(SHARED_GROUP, text)
                self.assertIn("cancel-in-progress: false", text)

    def test_mixed_workflows_never_run_production_job_on_push(self) -> None:
        for name in MIXED_VALIDATION_AND_PRODUCTION:
            with self.subTest(workflow=name):
                text = self._read(name)
                self.assertIn("workflow_dispatch:", text)
                self.assertIn(MANUAL_IF, text)
                self.assertNotIn(
                    "if: github.event_name == 'push' || github.event_name == 'workflow_dispatch'",
                    text,
                )
                self.assertNotIn("if: github.event_name != 'pull_request'", text)
                self.assertIn(SHARED_GROUP, text)
                self.assertIn("cancel-in-progress: false", text)

    def test_guarded_set_has_no_accidental_duplicate_entries(self) -> None:
        guarded = MANUAL_ONLY + MIXED_VALIDATION_AND_PRODUCTION
        self.assertEqual(len(guarded), len(set(guarded)))


if __name__ == "__main__":
    unittest.main()
