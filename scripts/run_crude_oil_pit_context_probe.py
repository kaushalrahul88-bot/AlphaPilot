from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.crude_oil_pit_context_probe import probe_crude_oil_pit_context_sync


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-06-01T00:00:00+05:30")
    parser.add_argument("--end", default="2026-09-01T00:00:00+05:30")
    parser.add_argument("--output", default="crude_oil_pit_context_probe.json")
    args = parser.parse_args()

    report = probe_crude_oil_pit_context_sync(args.start, args.end)
    output = Path(args.output)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    summary = {
        "mode": report["mode"],
        "requested_start": report["requested_start"],
        "requested_end_exclusive": report["requested_end_exclusive"],
        "full_window_hourly_candidates": report["full_window_hourly_candidates"],
        "feeds": {
            key: {
                "status": row.get("status"),
                "ticker": row.get("ticker"),
                "rows": row.get("rows"),
                "first_bar_start": row.get("first_bar_start"),
                "last_bar_start": row.get("last_bar_start"),
                "covers_requested_start_date": row.get("covers_requested_start_date"),
                "covers_requested_end_date": row.get("covers_requested_end_date"),
                "error": row.get("error"),
            }
            for key, row in report["feeds"].items()
        },
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
