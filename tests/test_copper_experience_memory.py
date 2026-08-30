import unittest
from app.copper_experience_memory import query_memory

def exp(i,direction="BULLISH",outcome="TARGET_FIRST"):
    return {"timestamp":f"2026-08-{3+i//20:02d}T{9+(i%10):02d}:00:00+05:30","direction":direction,
            "vector":{"return_15m_pct":i/1000},"structure":"UPTREND","opening_range_break":"ABOVE",
            "price_oi_state":"UNKNOWN","outcome":outcome,"minutes_to_event":30,"mfe_pct":.2,"mae_pct":.1}

class MemoryTests(unittest.TestCase):
    def test_memory_refuses_small_prior_sample(self):
        xs=[exp(i) for i in range(10)]
        q={"timestamp":"2026-08-20T10:00:00+05:30","vector":{"return_15m_pct":.01},
           "structure":"UPTREND","opening_range_break":"ABOVE","price_oi_state":"UNKNOWN"}
        self.assertEqual(query_memory(xs,q)["status"],"INSUFFICIENT_MEMORY")

    def test_memory_never_uses_future_experience(self):
        xs=[exp(i,"BULLISH" if i%2==0 else "BEARISH") for i in range(40)]
        q={"timestamp":"2026-08-20T10:00:00+05:30","vector":{"return_15m_pct":.02},
           "structure":"UPTREND","opening_range_break":"ABOVE","price_oi_state":"UNKNOWN"}
        r=query_memory(xs,q,k=20)
        self.assertEqual(r["status"],"READY")
        self.assertLessEqual(r["analogues_used"],20)

if __name__=="__main__":unittest.main()
