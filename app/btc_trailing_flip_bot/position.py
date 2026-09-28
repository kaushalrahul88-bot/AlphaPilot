from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Position:
    side: str
    entry_time: datetime
    entry_price: float
    market_entry_price: float
    reference_price: float
    notional_usdt: float
    entry_fee: float
    funding: float = 0.0

    @property
    def quantity(self) -> float:
        return self.notional_usdt / self.entry_price
