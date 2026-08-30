from __future__ import annotations
from .slow_context_releases import periodic_record

# Official NBS releases known during the Aug-2026 Copper replay period.
# available_at is the publication/release availability in IST, not the observation period.
CHINA_COPPER_MACRO=[
 periodic_record("MACRO_RELEASE","2026-07-31T23:59:59+08:00","2026-08-01T07:00:00+05:30",
  {"event":"CHINA_MANUFACTURING_PMI","period":"2026-07","actual":49.2,"unit":"index",
   "interpretation":"BELOW_50_CONTRACTION"},
  "National Bureau of Statistics of China",metadata={"copper_channel":"industrial-demand regime","official":True}),
 periodic_record("MACRO_RELEASE","2026-07-31T23:59:59+08:00","2026-08-17T07:30:00+05:30",
  {"event":"CHINA_INDUSTRIAL_VALUE_ADDED","period":"2026-07","yoy_pct":4.5,
   "jan_jul_yoy_pct":5.3},
  "National Bureau of Statistics of China",metadata={"copper_channel":"industrial activity","official":True}),
 periodic_record("MACRO_RELEASE","2026-07-31T23:59:59+08:00","2026-08-17T07:30:00+05:30",
  {"event":"CHINA_FIXED_ASSET_INVESTMENT","period":"2026-01_to_07","yoy_pct":-6.7,
   "infrastructure_yoy_pct":-3.6,"manufacturing_yoy_pct":-1.7,"real_estate_yoy_pct":-19.2},
  "National Bureau of Statistics of China",metadata={"copper_channel":"construction/infrastructure demand","official":True}),
 periodic_record("MACRO_RELEASE","2026-07-31T23:59:59+08:00","2026-08-17T07:30:00+05:30",
  {"event":"CHINA_RETAIL_SALES","period":"2026-07","yoy_pct":0.6,"jan_jul_yoy_pct":1.2},
  "National Bureau of Statistics of China",metadata={"copper_channel":"broad demand context","official":True}),
]

def china_copper_macro_records():
 return list(CHINA_COPPER_MACRO)
