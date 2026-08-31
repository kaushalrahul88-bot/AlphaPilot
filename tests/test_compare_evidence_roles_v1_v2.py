import copy,unittest
from scripts.compare_evidence_roles_v1_v2 import compare


class EvidenceRolesShadowComparisonTests(unittest.TestCase):
    def payload(self,outcome):
        return {"decisions":[{
            "click_timestamp":"2026-08-03T13:20:00+05:30",
            "evidence":{
                "lanes":{"STRUCTURE":[{"lane":"STRUCTURE","stance":"BULLISH","source":"structure"}],
                         "MACRO":[{"lane":"MACRO","stance":"BEARISH","source":"macro"}]},
                "independent_bullish_lanes":["STRUCTURE"],
                "independent_bearish_lanes":["MACRO"],
            },
            "decision":{"action":"NO_TRADE"},
            "outcome":outcome,
        }]}

    def test_outcome_changes_cannot_change_shadow_classification(self):
        target=compare(self.payload({"label":"TARGET","r_multiple":1.5}))
        stop=compare(self.payload({"label":"STOP","r_multiple":-1.0}))
        self.assertEqual(target,stop)
        self.assertTrue(target["outcome_blind"])
        self.assertFalse(target["outcomes_read"])

    def test_supporting_lanes_cannot_manufacture_v2_direction(self):
        payload={"decisions":[{
            "click_timestamp":"x",
            "evidence":{"lanes":{"STRUCTURE":[],"MACRO":[{"lane":"MACRO","stance":"BULLISH"}],
                                  "EXPERIENCE":[{"lane":"EXPERIENCE","stance":"BULLISH"}]},
                        "independent_bullish_lanes":["MACRO","EXPERIENCE"],"independent_bearish_lanes":[]},
            "decision":{"action":"BUY_CE"},"outcome":{"label":"TARGET"}}]}
        result=compare(payload)
        self.assertEqual(result["transition_counts"],{"BULLISH->NONE":1})
        self.assertEqual(result["recorded_action_conflicts"],{"BULLISH->NONE":1})


if __name__=="__main__":unittest.main()
