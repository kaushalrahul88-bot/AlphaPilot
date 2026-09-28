from dataclasses import dataclass

TRAILS=(0.0005,0.001,0.0015,0.002,0.003,0.005,0.0075,0.01)
@dataclass(frozen=True)
class Config:
    symbol:str="BTCUSDT"
    notional_usdt:float=1000.0
    entry_fee_rate:float=0.0004
    exit_fee_rate:float=0.0004
    slippage_rate:float=0.0001
    trail_percentages:tuple=TRAILS
    def validate(self):
        if self.notional_usdt<=0: raise ValueError("notional_usdt must be positive")
        if any(not 0<x<1 for x in self.trail_percentages): raise ValueError("invalid trail percentage")
        if min(self.entry_fee_rate,self.exit_fee_rate,self.slippage_rate)<0: raise ValueError("cost rates cannot be negative")
