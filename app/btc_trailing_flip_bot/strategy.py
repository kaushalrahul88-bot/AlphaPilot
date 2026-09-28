from dataclasses import dataclass
@dataclass
class TrailState:
    side:str
    reference:float
    trail:float
    def update(self, price:float):
        if self.side=="LONG":
            self.reference=max(self.reference,price); stop=self.reference*(1-self.trail); hit=price<=stop
        else:
            self.reference=min(self.reference,price); stop=self.reference*(1+self.trail); hit=price>=stop
        return stop,hit

def initial_side(ema9,ema21):
    if ema9>ema21:return "LONG"
    if ema9<ema21:return "SHORT"
    return None
