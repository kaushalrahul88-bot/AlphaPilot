import unittest
from app.learning_experiment_gate import *
class GateTests(unittest.TestCase):
 def d(self):return {"process_score":.8,"expectancy_r":.2,"false_positive_rate":.1,"abstention_quality":.8,"confidence_calibration":.8}
 def test_no_auto_deploy(self):
  b=self.d();c=dict(b,expectancy_r=.3,process_score=.81)
  e=evaluate_learning_experiment(b,c,holdout_frozen=True,point_in_time_clean=True)
  self.assertFalse(promotion_record({},e)["production_change_allowed"])
 def test_hindsight_rejected(self):
  e=evaluate_learning_experiment(self.d(),self.d(),holdout_frozen=True,point_in_time_clean=False)
  self.assertEqual(e["status"],"REJECT")
if __name__=="__main__":unittest.main()
