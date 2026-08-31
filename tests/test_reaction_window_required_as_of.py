import unittest
from app.market_news_reaction_windows import build_reaction_window


class ReactionWindowRequiredAsOfTests(unittest.TestCase):
    def test_as_of_is_required_by_api(self):
        with self.assertRaises(TypeError):
            build_reaction_window({"available_at":"2026-08-07T10:00:00+05:30"},[])


if __name__=="__main__":unittest.main()
