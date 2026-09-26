import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from evaluate import run
from review import Ledger, decide, normalize

ROOT = Path(__file__).resolve().parent

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / "policy.json").read_text())
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "audit.sqlite")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_idempotent_and_duplicate(self):
        records = json.loads((ROOT / "sample_invoices.json").read_text())["invoices"]
        one = self.ledger.process(records[0], self.policy)
        self.assertEqual(self.ledger.process(records[0], self.policy), one)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 1)
        two = self.ledger.process(records[1], self.policy)
        self.assertIn("duplicate_invoice_number", two["flags"])
        self.assertTrue(self.ledger.verify_chain())

    def test_correction_and_provenance(self):
        row = {"source": "s", "fields": {"invoice_number": "A", "vendor": {"value":"Wrong", "confidence": .5},
               "amount":"100", "currency":"USD", "due_date":"2026-10-15"}}
        result = self.ledger.process(row, self.policy,
                                     corrections=[{"field":"vendor", "value":"Bluebird Logistics"}], reviewer="qa@example.test")
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["fields"]["vendor"]["origin"], "human_correction")
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 2)
        self.assertTrue(self.ledger.verify_chain())

    def test_source_collision_rejected(self):
        self.ledger.process({"source":"same", "fields":{}}, self.policy)
        with self.assertRaises(ValueError):
            self.ledger.process({"source":"same", "fields":{"vendor":"Changed"}}, self.policy)

    def test_eval_set(self):
        self.assertEqual(run()["decision_accuracy"], 1.0)

if __name__ == "__main__": unittest.main()
