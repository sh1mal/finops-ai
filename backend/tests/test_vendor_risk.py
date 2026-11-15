import unittest
from datetime import date

from backend.verification import vendor_amount_check


class VendorRiskTests(unittest.TestCase):
    def test_threshold_is_inclusive_and_uses_the_exact_average(self):
        date_of_invoice = date(2026, 11, 12)
        history = {"count": 3, "total_cents": 2500000, "mean_cents": 833333}
        self.assertFalse(vendor_amount_check(1200000, history, date_of_invoice)["passed"])
        # The rounded mean would place this amount at the threshold.
        self.assertTrue(vendor_amount_check(1166666, history, date_of_invoice)["passed"])
        self.assertTrue(vendor_amount_check(900000, history, date_of_invoice)["passed"])
        exact = {"count": 3, "total_cents": 30000, "mean_cents": 10000}
        self.assertFalse(vendor_amount_check(14000, exact, date_of_invoice)["passed"])

    def test_sparse_history_does_not_hold_the_invoice(self):
        history = {"count": 1, "total_cents": 127500, "mean_cents": 127500}
        check = vendor_amount_check(180000, history, date(2026, 11, 12))
        self.assertTrue(check["passed"])
        self.assertTrue(check["skipped"])
