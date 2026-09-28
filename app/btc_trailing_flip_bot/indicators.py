from __future__ import annotations

class EMA:
    def __init__(self, period: int):
        if period <= 0:
            raise ValueError("period must be positive")
        self.period = period
        self.value: float | None = None
        self.alpha = 2.0 / (period + 1.0)

    def update(self, price: float) -> float:
        self.value = price if self.value is None else self.alpha * price + (1.0 - self.alpha) * self.value
        return self.value
