from app.crypto_vortex_v1 import Candle, VortexConfig, backtest, summarize


def test_empty_backtest_is_safe():
    assert summarize(backtest([]))["trades"] == 0


def test_flat_market_produces_no_setup():
    candles = [Candle(i * 60_000, 100, 100.1, 99.9, 100, 1) for i in range(200)]
    assert backtest(candles, VortexConfig()) == []


def test_summary_math():
    # Summary's zero case is explicit and deterministic.
    s = summarize([])
    assert s == {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_r": 0.0, "avg_r": 0.0}
