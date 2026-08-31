from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.copper_market_brain_abstention_audit import evaluate_market_brain_abstention


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candles", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sample-every-bars", type=int, default=3)
    args = parser.parse_args()

    artifact = json.loads(Path(args.candles).read_text())
    candles = artifact.get("candles") if isinstance(artifact, dict) else artifact
    if not isinstance(candles, list) or not candles:
        raise RuntimeError("Frozen candle artifact contains no candles")

    report = evaluate_market_brain_abstention(candles, sample_every_bars=args.sample_every_bars)
    if isinstance(artifact, dict):
        report["frozen_source"] = {
            "mode": artifact.get("mode"),
            "symbol": artifact.get("symbol"),
            "trading_symbol": artifact.get("trading_symbol"),
            "interval_minutes": artifact.get("interval_minutes"),
            "start": artifact.get("start"),
            "end": artifact.get("end"),
            "first_timestamp": artifact.get("first_timestamp"),
            "last_timestamp": artifact.get("last_timestamp"),
            "candles_sha256": artifact.get("candles_sha256"),
            "point_in_time": artifact.get("point_in_time"),
            "network_refetch": artifact.get("network_refetch"),
        }
    Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
