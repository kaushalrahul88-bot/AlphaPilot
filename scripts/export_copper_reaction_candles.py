from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.copper_market_brain_direction_audit import PRIMARY_END, PRIMARY_START, REFERENCE_CONTRACT
from app.frozen_market_candle_export import build_frozen_candle_artifact


async def export_from_store(store):
    await store.initialize()
    segments=await store.read_symbol_contract_segments("COPPER",5,PRIMARY_START,PRIMARY_END)
    target=next((s for s in segments if str(s.get("trading_symbol") or "").upper()==REFERENCE_CONTRACT),None)
    if not target:raise RuntimeError(f"Stored contract {REFERENCE_CONTRACT} not found")
    return build_frozen_candle_artifact(target.get("candles") or [],symbol="COPPER",trading_symbol=REFERENCE_CONTRACT,
                                        interval_minutes=5,start=PRIMARY_START,end=PRIMARY_END,
                                        source="persistent_store.read_symbol_contract_segments")


def main():
    p=argparse.ArgumentParser(description="Export the exact stored Copper Current Mind candle segment; no network refetch.")
    p.add_argument("--output",required=True);args=p.parse_args()
    # Import the application's configured persistent store only when this operational command runs.
    from app.main import store
    artifact=asyncio.run(export_from_store(store))
    Path(args.output).write_text(json.dumps(artifact,indent=2,sort_keys=True)+"\n")
    print(json.dumps({k:artifact[k] for k in ("trading_symbol","interval_minutes","candle_count","first_timestamp","last_timestamp","candles_sha256")},sort_keys=True))


if __name__=="__main__":main()
