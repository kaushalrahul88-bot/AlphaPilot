from app.copper_candle_observation_store import (
    COPPER_CANDLE_SCHEMA_SQL,
    PROVENANCE_ID,
    TABLE_NAME,
    TIMEFRAME_MINUTES,
    TRIGGER_SQL,
)


def test_copper_candle_store_is_exact_5m_and_first_seen_only():
    assert TABLE_NAME == "copper_candle_observations"
    assert PROVENANCE_ID == "COPPER_5M_FIRST_SEEN_IMMUTABLE_CANDLE_OBSERVATIONS_V1"
    assert TIMEFRAME_MINUTES == 5
    assert "CHECK (symbol = 'COPPER')" in COPPER_CANDLE_SCHEMA_SQL
    assert "CHECK (timeframe_minutes = 5)" in COPPER_CANDLE_SCHEMA_SQL
    assert "PRIMARY KEY (provider, trading_symbol, timeframe_minutes, candle_at)" in COPPER_CANDLE_SCHEMA_SQL
    assert f"INSERT INTO {TABLE_NAME}" in TRIGGER_SQL
    assert "IF NEW.symbol = 'COPPER' AND NEW.timeframe_minutes = 5" in TRIGGER_SQL
    assert "AFTER INSERT ON commodity_candles" in TRIGGER_SQL
    assert "ON CONFLICT (provider, trading_symbol, timeframe_minutes, candle_at)" in TRIGGER_SQL
    assert "DO NOTHING" in TRIGGER_SQL
    assert "AFTER UPDATE" not in TRIGGER_SQL


def test_copper_candle_store_has_no_backfill_or_mutation_path():
    upper = TRIGGER_SQL.upper()
    assert "SELECT" not in upper
    assert "INSERT INTO COMMODITY_CANDLES" not in upper
    assert "UPDATE COPPER_CANDLE_OBSERVATIONS" not in upper
    assert "DELETE FROM COPPER_CANDLE_OBSERVATIONS" not in upper
