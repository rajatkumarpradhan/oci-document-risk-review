import unittest
from app import main
from review import decide, normalize


class IntakeTests(unittest.TestCase):
    def test_offline_scenario(self):
        result = main([])
        self.assertEqual(result["processed"], 3)
        self.assertEqual([x["decision"] for x in result["results"]], ["PASS", "REVIEW", "REVIEW"])
        self.assertIn("duplicate_invoice_number", result["results"][1]["flags"])

    def test_missing_and_invalid(self):
        result = decide(normalize({"fields": {"invoice_number": "X", "amount": "broken"}}),
                        {"approved_vendors": [], "allowed_currencies": ["USD"], "high_value_threshold": 10000, "min_confidence": .8})
        self.assertIn("invalid_amount", result["flags"])
        self.assertTrue(any(x.startswith("missing_fields:") for x in result["flags"]))


if __name__ == "__main__":
    unittest.main()
