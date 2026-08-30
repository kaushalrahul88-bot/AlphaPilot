from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.copper_historical_news_integrity_audit import audit_historical_news_records

EXPECTED_SOURCE_RUN_ID = 33309160252
EXPECTED_SOURCE_ARTIFACT_ID = 9731584724
EXPECTED_SOURCE_RECORD_COUNT = 54
EXPECTED_SOURCE_DATASET_SHA256 = "f37aab4971f3cccd74a8ca6feb7cc391e4a5d8aa8e7038f97b9567dac010bc3a"


def _reconstruct_record(row):
    return {
        "series": "COPPER_NEWS",
        "observed_at": row["available_at"],
        "available_at": row["available_at"],
        "source": row.get("source") or "GDELT",
        "value": {
            "headline": row.get("headline"),
            "url": row.get("url"),
            "domain": row.get("source"),
            "language": row.get("language"),
            "sourcecountry": row.get("sourcecountry"),
            "sentiment": row.get("raw_sentiment"),
            "event_tags": row.get("event_tags") or [],
            "gdelt_seendate": row["available_at"],
        },
        "quality": "GDELT_SEEN_TIMESTAMP_RECONSTRUCTED_FROM_IMMUTABLE_AUDIT_ARTIFACT",
    }


def re_audit(source_path: str, output_path: str):
    source = json.loads(Path(source_path).read_text(encoding="utf-8"))
    meta = source.get("source_metadata") or {}
    if int(source.get("raw_record_count") or 0) != EXPECTED_SOURCE_RECORD_COUNT:
        raise RuntimeError(f"Unexpected source record count: {source.get('raw_record_count')}")
    if meta.get("dataset_sha256") != EXPECTED_SOURCE_DATASET_SHA256:
        raise RuntimeError(f"Unexpected source dataset hash: {meta.get('dataset_sha256')}")
    rows = source.get("records") or []
    if len(rows) != EXPECTED_SOURCE_RECORD_COUNT:
        raise RuntimeError(f"Source artifact rows mismatch: {len(rows)}")
    records = [_reconstruct_record(row) for row in rows]
    result = audit_historical_news_records(records)
    result["source_metadata"] = {
        "provider": meta.get("provider"),
        "query": meta.get("query"),
        "timestamp_semantics": meta.get("timestamp_semantics"),
        "original_retrieved_at": meta.get("retrieved_at"),
        "source_dataset_sha256": EXPECTED_SOURCE_DATASET_SHA256,
        "source_workflow_run_id": EXPECTED_SOURCE_RUN_ID,
        "source_artifact_id": EXPECTED_SOURCE_ARTIFACT_ID,
        "source_raw_record_count": EXPECTED_SOURCE_RECORD_COUNT,
        "acquisition_mode": "FROZEN_IMMUTABLE_GITHUB_ACTIONS_ARTIFACT",
        "network_refetch": False,
    }
    Path(output_path).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        f"Re-audited frozen news: raw={result['raw_record_count']} "
        f"accepted={result['accepted_record_count']} "
        f"sha256={result['accepted_dataset_sha256']}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", default="news-audit-v2.json")
    args = parser.parse_args()
    re_audit(args.source, args.output)
