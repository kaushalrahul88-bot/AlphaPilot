from app.btc_trailing_flip_bot.indicators import EMA
from app.btc_trailing_flip_bot.strategy import TrailState, initial_side

def test_ema_waits_for_period():
    ema = EMA(3)
    assert not ema.ready
    ema.update(100)
    ema.update(101)
    assert not ema.ready
    ema.update(102)
    assert ema.ready

def test_initial_side_requires_non_equal_emas():
    assert initial_side(101, 100) == "LONG"
    assert initial_side(99, 100) == "SHORT"
    assert initial_side(100, 100) is None

def test_long_trail_only_moves_up():
    state = TrailState("LONG", 100.0, 0.01)
    stop, hit = state.update(105.0)
    assert state.reference == 105.0
    assert stop == 103.95
    assert not hit
    stop, hit = state.update(103.94)
    assert state.reference == 105.0
    assert hit

def test_short_trail_only_moves_down():
    state = TrailState("SHORT", 100.0, 0.01)
    stop, hit = state.update(95.0)
    assert state.reference == 95.0
    assert stop == 95.95
    assert not hit
    stop, hit = state.update(95.96)
    assert state.reference == 95.0
    assert hit
