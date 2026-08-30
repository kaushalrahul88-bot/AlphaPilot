import unittest
from app.copper_news_reaction_context import *
class NewsReactionTests(unittest.TestCase):
 def test_bullish_headline_can_be_contradicted(self):
  r=assess_news_reaction({"sentiment":"BULLISH"},{"price":100},{"price":99})
  self.assertEqual(r["confirmation"],"CONTRADICTED")
 def test_future_news_hidden(self):
  xs=[{"published_at":"2026-08-25T14:30:00+05:30"}]
  self.assertEqual(news_visible_as_of(xs,"2026-08-25T14:00:00+05:30"),[])
if __name__=="__main__":unittest.main()
