from __future__ import annotations

class EMA:
    def __init__(self, period: int):
        if period <= 0:
            raise ValueError("period must be positive")
        self.period = period
        self.value: float | None = None
        self.count = 0
        self.alpha = 2.0 / (period + 1.0)

    @property
    def ready(self) -> bool:
        return self.count >= self.period

    def update(self, price: float) -> float:
        self.count += 1
        self.value = price if self.value is None else self.alpha * price + (1.0 - self.alpha) * self.value
        return self.value
