import unittest

from app.trader_evidence_roles import role_geometry
from app.trader_evidence_synthesis import synthesize_evidence


class TraderEvidenceRolesV2PreviewTests(unittest.TestCase):
    def geometry(self,items):return role_geometry(synthesize_evidence(items))

    def test_supporting_lanes_cannot_manufacture_direction_without_structure(self):
        g=self.geometry([
            {"lane":"MACRO","stance":"BULLISH","source":"macro"},
            {"lane":"EXPERIENCE","stance":"BULLISH","source":"memory"},
            {"lane":"PARTICIPATION","stance":"BULLISH","source":"volume"},
        ])
        self.assertIsNone(g["actionable_direction"])
        self.assertEqual(g["anchor_state"],"MISSING")

    def test_mixed_structure_fails_closed_even_when_other_lanes_agree(self):
        g=self.geometry([
            {"lane":"STRUCTURE","stance":"BULLISH","source":"market_structure"},
            {"lane":"STRUCTURE","stance":"BEARISH","source":"short_term_momentum"},
            {"lane":"MACRO","stance":"BULLISH","source":"macro"},
            {"lane":"EXPERIENCE","stance":"BULLISH","source":"memory"},
        ])
        self.assertIsNone(g["actionable_direction"])
        self.assertEqual(g["anchor_state"],"MIXED")

    def test_context_disagreement_is_preserved_without_overriding_structure(self):
        g=self.geometry([
            {"lane":"STRUCTURE","stance":"BEARISH","source":"market_structure"},
            {"lane":"STRUCTURE","stance":"BEARISH","source":"short_term_momentum"},
            {"lane":"MACRO","stance":"BULLISH","source":"macro"},
        ])
        self.assertEqual(g["actionable_direction"],"BEARISH")
        self.assertEqual(g["contradictions"][0]["lane"],"MACRO")

    def test_options_cannot_decide_underlying_direction(self):
        g=self.geometry([
            {"lane":"OPTIONS","stance":"BULLISH","source":"option_chain"},
            {"lane":"MACRO","stance":"BULLISH","source":"macro"},
        ])
        self.assertIsNone(g["actionable_direction"])
        self.assertEqual(g["execution"][0]["lane"],"OPTIONS")

    def test_memory_cannot_override_observable_structure(self):
        g=self.geometry([
            {"lane":"STRUCTURE","stance":"BEARISH","source":"market_structure"},
            {"lane":"EXPERIENCE","stance":"BULLISH","source":"walk_forward_memory"},
        ])
        self.assertEqual(g["actionable_direction"],"BEARISH")
        self.assertEqual(g["memory"][0]["stance"],"BULLISH")


if __name__=="__main__":unittest.main()
