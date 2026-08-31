import unittest

from app.current_mind_copper_replay import _dominant_direction


def _news(stance, status):
    return {
        "lane":"NEWS",
        "stance":stance,
        "source":"news_intelligence+persistence",
        "detail":{
            "visible":1,
            "persistence":[{"status":status,"weight":0.8}],
        },
    }


class CurrentMindNewsGeometryDirectionTests(unittest.TestCase):
    def test_decayed_news_cannot_create_geometry_with_one_price_lane(self):
        items=[
            {"lane":"STRUCTURE","stance":"BULLISH","source":"market_structure"},
            _news("BULLISH","ACTIVE_DECAYED"),
        ]
        self.assertIsNone(_dominant_direction(items))

    def test_unconfirmed_news_cannot_block_two_lane_opposite_geometry(self):
        items=[
            {"lane":"STRUCTURE","stance":"BEARISH","source":"market_structure"},
            {"lane":"MACRO","stance":"BEARISH","source":"macro"},
            _news("BULLISH","ACTIVE"),
        ]
        self.assertEqual(_dominant_direction(items),"BEARISH")

    def test_fresh_price_aligned_news_can_join_structure_for_geometry(self):
        items=[
            {"lane":"STRUCTURE","stance":"BULLISH","source":"market_structure"},
            _news("BULLISH","ACTIVE"),
        ]
        self.assertEqual(_dominant_direction(items),"BULLISH")

    def test_legacy_non_news_lane_counting_is_preserved(self):
        items=[
            {"lane":"STRUCTURE","stance":"BULLISH","source":"market_structure"},
            {"lane":"EXPERIENCE","stance":"BULLISH","source":"walk_forward_memory"},
        ]
        self.assertEqual(_dominant_direction(items),"BULLISH")


if __name__=="__main__":
    unittest.main()
