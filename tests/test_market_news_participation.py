import unittest
from app.market_news_participation import assess_news_participation as assess


class NewsParticipationTests(unittest.TestCase):
    def window(self,volume=100,ivolume=150,oi=1000,coi=1020):
        return {"pre_event":{"volume":volume,"open_interest":oi},
                "immediate":{"volume":ivolume,"open_interest":1010},
                "confirmation":{"volume":ivolume,"open_interest":coi}}

    def reaction(self,state):return {"reaction_state":state,"outcome_blind":True}

    def test_accepted_price_with_volume_and_oi_is_broad_participation(self):
        r=assess(self.window(),self.reaction("ACCEPTED_REACTION"))
        self.assertEqual(r["participation_state"],"BROAD_PARTICIPATION")

    def test_accepted_price_without_expansion_is_price_only(self):
        r=assess(self.window(ivolume=110,coi=1005),self.reaction("ACCEPTED_REACTION"))
        self.assertEqual(r["participation_state"],"PRICE_ONLY_ACCEPTANCE")

    def test_missing_data_never_becomes_confirmation(self):
        w={"pre_event":{"price":100},"immediate":{"price":101},"confirmation":{"price":102}}
        r=assess(w,self.reaction("ACCEPTED_REACTION"))
        self.assertEqual(r["volume"]["state"],"UNKNOWN")
        self.assertEqual(r["open_interest"]["state"],"UNKNOWN")
        self.assertEqual(r["participation_state"],"PRICE_ONLY_ACCEPTANCE")

    def test_failed_reaction_with_expansion_preserves_rejection(self):
        r=assess(self.window(),self.reaction("FAILED_REACTION"))
        self.assertEqual(r["participation_state"],"PARTICIPATED_BUT_REJECTED")

    def test_trade_outcome_metadata_is_not_read(self):
        a=self.reaction("ACCEPTED_REACTION");b=dict(a);a["outcome"]="TARGET";b["outcome"]="STOP"
        self.assertEqual(assess(self.window(),a),assess(self.window(),b))


if __name__=="__main__":unittest.main()
