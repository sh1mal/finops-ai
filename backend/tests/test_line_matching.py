import json
import unittest
from unittest.mock import patch

from backend.line_matching import reconcile


class LineMatchingTests(unittest.TestCase):
    def test_model_cannot_choose_an_unspecified_variant(self):
        item = {"description": "Apple MBP M5 14", "quantity": 1, "unit": "each",
                "unit_price": "2000.00", "amount": "2000.00"}
        orders = [
            {"line_number": number, "description": f"MacBook Pro 14-inch M5, {variant}",
             "variant_family": "MacBook Pro M5 14", "variant": variant,
             "quantity": 1, "unit": "each", "unit_price_cents": 200000}
            for number, variant in enumerate(("silver", "space black"), 1)
        ]
        output = {"pairs": [{"invoice_line": 1, "po_line": 1, "status": "matched"}]}
        with patch("backend.line_matching.chat", return_value=({"content": json.dumps(output)}, {})):
            pairs, problems = reconcile([item], orders, "test-model")
        self.assertIsNone(pairs[0]["po"])
        self.assertIn("Ambiguous description", problems[0])
