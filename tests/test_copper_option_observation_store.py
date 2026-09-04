from app.copper_option_observation_store import (
    COPPER_SCHEMA_SQL,
    PROVENANCE_ID,
    TABLE_NAME,
    TRIGGER_SQL,
)


def test_copper_store_is_symbol_exact_and_first_seen_only():
    assert TABLE_NAME == "copper_option_observations"
    assert PROVENANCE_ID == "COPPER_FIRST_SEEN_IMMUTABLE_OPTION_OBSERVATIONS_V1"
    assert "CHECK (underlying_symbol = 'COPPER')" in COPPER_SCHEMA_SQL
    assert "PRIMARY KEY (provider, trading_symbol, sample_bucket_at)" in COPPER_SCHEMA_SQL
    assert f"INSERT INTO {TABLE_NAME}" in TRIGGER_SQL
    assert "IF NEW.underlying_symbol = 'COPPER'" in TRIGGER_SQL
    assert "ON CONFLICT (provider, trading_symbol, sample_bucket_at) DO NOTHING" in TRIGGER_SQL
    assert "AFTER INSERT ON commodity_option_snapshots" in TRIGGER_SQL
    assert "AFTER UPDATE" not in TRIGGER_SQL


def test_store_definition_contains_no_historical_backfill_query():
    upper = TRIGGER_SQL.upper()
    assert "SELECT" not in upper
    assert "INSERT INTO COMMODITY_OPTION_SNAPSHOTS" not in upper
    assert "UPDATE COPPER_OPTION_OBSERVATIONS" not in upper


def test_store_does_not_capture_other_underlyings():
    assert "CRUDEOILM" not in COPPER_SCHEMA_SQL
    assert "CRUDEOILM" not in TRIGGER_SQL
    assert "NATURALGAS" not in COPPER_SCHEMA_SQL
    assert "NATURALGAS" not in TRIGGER_SQL
