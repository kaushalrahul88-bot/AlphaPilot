from __future__ import annotations

class RiskConfig:
    def __init__(self, max_position_notional: float | None = None, max_daily_loss: float | None = None):
        self.max_position_notional = max_position_notional
        self.max_daily_loss = max_daily_loss

    def validate_notional(self, notional: float) -> None:
        if notional <= 0:
            raise ValueError("notional must be positive")
        if self.max_position_notional is not None and notional > self.max_position_notional:
            raise ValueError("notional exceeds max_position_notional")
