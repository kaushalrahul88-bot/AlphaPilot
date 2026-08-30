import json
import tempfile
import unittest
from pathlib import Path

from scripts.reaudit_frozen_copper_news import (
    EXPECTED_SOURCE_DATASET_SHA256,
    re_audit,
)


class FrozenNewsReauditTests(unittest.TestCase):
    def test_reaudit_rejects_wrong_source_hash(self):
        source={
            "raw_record_count":54,
            "source_metadata":{"dataset_sha256":"wrong"},
            "records":[{} for _ in range(54)],
        }
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"source.json"; p.write_text(json.dumps(source))
            with self.assertRaises(RuntimeError):
                re_audit(str(p),str(Path(td)/"out.json"))

    def test_expected_source_hash_is_frozen(self):
        self.assertEqual(
            EXPECTED_SOURCE_DATASET_SHA256,
            "f37aab4971f3cccd74a8ca6feb7cc391e4a5d8aa8e7038f97b9567dac010bc3a",
        )


if __name__=="__main__":
    unittest.main()
