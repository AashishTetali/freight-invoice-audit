"""Unit tests for freight_audit/pricing.py (the contract maths)."""
import unittest
from datetime import date
from decimal import Decimal as D

from freight_audit.pricing import (Expected, Index, base_charge, billable_weight, ceil_int, dim_weight, find_tier,
                                   fuel_charge, money)
from tests.helpers import generated_world

TIERS = [dict(tier_min_lb=0, tier_max_lb=499, rate_per_lb=D("0.25")),
         dict(tier_min_lb=500, tier_max_lb=999, rate_per_lb=D("0.18")),
         dict(tier_min_lb=1000, tier_max_lb=None, rate_per_lb=D("0.12"))]


class TestFormulas(unittest.TestCase):
    def test_worked_example(self):
        """48x40x48 in pallet, 300 lb, DIM factor 139, 500-999 lb band at $0.18, 22% fuel, $50 liftgate."""
        dim = dim_weight(48, 40, 48, 1, 139)
        self.assertEqual(dim, 664)                          # 92,160 / 139 = 663.02, rounded UP
        weight = billable_weight(D("300"), dim)
        self.assertEqual(weight, 664)
        base, min_applied = base_charge(weight, D("0.18"), D("75.00"))
        self.assertEqual((base, min_applied), (D("119.52"), 0))
        fuel = fuel_charge(base, D("22"))
        self.assertEqual(fuel, D("26.29"))
        self.assertEqual(base + fuel + D("50.00"), D("195.81"))

    def test_money_rounds_half_up_to_cents(self):
        self.assertEqual(money(D("1.005")), D("1.01"))
        self.assertEqual(money(D("2.675")), D("2.68"))
        self.assertEqual(money(D("2.674")), D("2.67"))
        self.assertEqual(money(5), D("5.00"))

    def test_ceil_int(self):
        self.assertEqual(ceil_int(D("663.02")), 664)
        self.assertEqual(ceil_int(D("664")), 664)
        self.assertEqual(ceil_int(D("0.1")), 1)

    def test_dim_weight_exact_division_is_not_rounded_up(self):
        self.assertEqual(dim_weight(139, 10, 1, 1, 139), 10)
        self.assertEqual(dim_weight(48, 40, 48, 2, 166), 1111)      # 184,320 / 166 = 1110.36

    def test_actual_weight_wins_when_heavier(self):
        self.assertEqual(billable_weight(D("900.2"), 664), 901)
        self.assertEqual(billable_weight(D("100"), 664), 664)

    def test_find_tier_boundaries(self):
        self.assertEqual(find_tier(TIERS, 0)[0], 0)
        self.assertEqual(find_tier(TIERS, 499)[0], 0)
        self.assertEqual(find_tier(TIERS, 500)[0], 1)
        self.assertEqual(find_tier(TIERS, 999)[0], 1)
        self.assertEqual(find_tier(TIERS, 1_000_000)[0], 2)        # open-ended top tier
        with self.assertRaises(ValueError):
            find_tier(TIERS, -1)

    def test_minimum_charge(self):
        self.assertEqual(base_charge(100, D("0.25"), D("75.00")), (D("75.00"), 1))
        self.assertEqual(base_charge(300, D("0.25"), D("75.00")), (D("75.00"), 0))    # exactly the minimum

    def test_fuel(self):
        self.assertEqual(fuel_charge(D("100.00"), D("21.8")), D("21.80"))
        self.assertEqual(fuel_charge(D("0.00"), D("30")), D("0.00"))

    def test_expected_total(self):
        e = Expected("S", {}, {}, 1, 1, 1, [], 0, D("0.1"), D("100.00"), 0, D("20"), D("20.00"),
                     [("LIFTGATE", D("1"), D("50"), D("50.00"), "EACH")])
        self.assertEqual(e.total, D("170.00"))


class TestIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.t, cls.gt, _ = generated_world()
        cls.idx = Index(cls.t)

    def test_card_for_picks_the_period(self):
        cid = self.t["carrier"][0]["carrier_id"]
        self.assertTrue(self.idx.card_for(cid, date(2026, 3, 1))["rate_card_id"].endswith("2026-01"))
        self.assertTrue(self.idx.card_for(cid, date(2026, 8, 1))["rate_card_id"].endswith("2026-02"))
        self.assertIsNone(self.idx.card_for(cid, date(2025, 12, 31)))
        self.assertIsNone(self.idx.card_for("CAR-999999", date(2026, 3, 1)))

    def test_is_effective_is_inclusive(self):
        card = self.t["rate_card"][0]
        self.assertTrue(Index.is_effective(card, card["effective_from"]))
        self.assertTrue(Index.is_effective(card, card["effective_to"]))

    def test_carrier_reweigh_beats_shipper_declaration(self):
        sid = next(m["shipment_id"] for m in self.t["weight_measurement"] if m["measurement_source"] == "CARRIER_REWEIGH")
        self.assertEqual(self.idx.authoritative_measurement(sid)["measurement_source"], "CARRIER_REWEIGH")
        only_bol = next(s["shipment_id"] for s in self.t["shipment"] if len(self.idx.measurements[s["shipment_id"]]) == 1)
        self.assertEqual(self.idx.authoritative_measurement(only_bol)["measurement_source"], "SHIPPER_BOL")

    def test_expected_is_internally_consistent(self):
        for s in self.t["shipment"][:100]:
            e = self.idx.expected(s["shipment_id"])
            self.assertEqual(e.billable_lb, max(e.actual_lb, e.dim_lb))
            self.assertEqual(e.rate, e.tiers[e.tier_index]["rate_per_lb"])
            self.assertEqual(e.fuel, fuel_charge(e.base, e.fuel_pct))
            self.assertTrue(Index.is_effective(e.card, s["ship_date"]))
            performed = self.idx.performed.get(s["shipment_id"], {})
            contract = self.idx.card_acc[e.card["rate_card_id"]]
            for code, qty, rate, amount, unit in e.accessorials:
                self.assertIn(code, performed)
                self.assertIn(code, contract)
                self.assertEqual(amount, money(rate * qty))

    def test_card_override_prices_with_another_card(self):
        s = next(s for s in self.t["shipment"] if s["ship_date"] >= self.cfg.h2_start)
        e = self.idx.expected(s["shipment_id"])
        old = self.idx.cards[e.card["previous_rate_card_id"]]
        e_old = self.idx.expected(s["shipment_id"], card=old)
        self.assertEqual(e_old.card["rate_card_id"], old["rate_card_id"])
        self.assertGreater(e_old.rate, e.rate)                    # H1 contracts are 4% dearer


if __name__ == "__main__":
    unittest.main()
