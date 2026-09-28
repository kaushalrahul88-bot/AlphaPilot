def entry_fill(side,market,slip): return market*(1+slip) if side=="LONG" else market*(1-slip)
def exit_fill(side,market,slip): return market*(1-slip) if side=="LONG" else market*(1+slip)
def fee(price,qty,rate): return price*qty*rate
