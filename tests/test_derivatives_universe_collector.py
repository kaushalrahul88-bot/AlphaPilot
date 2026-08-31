import unittest
from datetime import date

from app.derivatives_universe_collector import _active_derivatives_from_rows, _expiry

class DerivativesUniverseCollectorTests(unittest.TestCase):
 def test_expiry(self):self.assertEqual(str(_expiry("2026-09-24")),"2026-09-24")

 def test_discovers_mcx_option_underlyings_only_with_active_future_anchor(self):
  rows=[
   {"exchange":"MCX","segment":"COMMODITY","underlying_symbol":"GOLD","instrument_type":"FUT","expiry_date":"2026-09-30","trading_symbol":"GOLD30SEP26FUT","buy_allowed":"1"},
   {"exchange":"MCX","segment":"COMMODITY","underlying_symbol":"GOLD","instrument_type":"CE","expiry_date":"2026-09-30","trading_symbol":"GOLD30SEP26100000CE","buy_allowed":"1"},
   {"exchange":"MCX","segment":"COMMODITY","underlying_symbol":"SILVER","instrument_type":"PE","expiry_date":"2026-09-30","trading_symbol":"SILVER30SEP26100000PE","buy_allowed":"1"},
   {"exchange":"NSE","segment":"FNO","underlying_symbol":"RELIANCE","instrument_type":"PE","expiry_date":"2026-10-29","trading_symbol":"RELIANCE29OCT26PE","buy_allowed":"1"},
   {"exchange":"NSE","segment":"FNO","underlying_symbol":"RELIANCE","instrument_type":"CE","expiry_date":"2026-09-24","trading_symbol":"RELIANCE24SEP26CE","buy_allowed":"1"},
  ]
  result=_active_derivatives_from_rows(rows,date(2026,8,31))
  self.assertEqual(result["mcx_option_underlyings"],["GOLD"])
  self.assertEqual(result["fno_option_underlyings"],["RELIANCE"])
  self.assertEqual(result["fno_option_expiries"],{"RELIANCE":"2026-09-24"})
  self.assertEqual(result["counts"]["mcx_option_underlyings"],1)

if __name__=="__main__":unittest.main()
