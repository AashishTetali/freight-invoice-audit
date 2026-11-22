"""Unit tests for freight_audit/generator.py (the world builder)."""
import json
import random
import unittest
from collections import Counter
from decimal import Decimal as D

from freight_audit import generator as g
from freight_audit.export import jsonable
from freight_audit.pricing import Index
from freight_audit.schema import INJECTED_ISSUE_TYPES, TABLE_ORDER
from tests.helpers import generated_world, small_cfg


def draft_for(t, idx, shipment, cfg):
    """The same draft structure gen_invoices() builds for a correct bill."""
    e = idx.expected(shipment["shipment_id"])
    return dict(kind="ORIGINAL", carrier_id=shipment["carrier_id"], ship=shipment, exp=e,
                shipment_ref=shipment["shipment_id"], rate_card_id=e.card["rate_card_id"],
                invoice_date=shipment["ship_date"], billed_weight_lb=e.billable_lb,
                lines=g._clean_lines(e), label=None)


class TestHelpers(unittest.TestCase):
    def test_rng_streams_are_repeatable_and_independent(self):
        cfg = small_cfg()
        a = [g.rng_for(cfg, "x").random() for _ in range(3)]
        b = [g.rng_for(cfg, "x").random() for _ in range(3)]
        self.assertEqual(a, b)
        self.assertNotEqual(g.rng_for(cfg, "x").random(), g.rng_for(cfg, "y").random())

    def test_dec(self):
        self.assertEqual(g.dec(1.23456), D("1.2346"))
        self.assertEqual(g.dec(2.5, "1"), D("3"))

    def test_miles_between(self):
        chi, dal = g.CITY["Chicago"], g.CITY["Dallas"]
        self.assertEqual(g.miles_between(chi, chi), 0)
        self.assertEqual(g.miles_between(chi, dal), g.miles_between(dal, chi))
        self.assertTrue(850 < g.miles_between(chi, dal) < 1050)      # ~800 mi straight line x 1.18

    def test_ship_dates_stay_in_window(self):
        cfg, r = small_cfg(), random.Random(1)
        for _ in range(500):
            d = g._random_ship_date(r, cfg)
            self.assertTrue(cfg.ship_start <= d <= cfg.ship_end)

    def test_routes_start_and_end_at_the_lane_cities(self):
        r = random.Random(2)
        for _ in range(200):
            path = g._route(r, g.CITY["Seattle"], g.CITY["Miami"])
            self.assertEqual((path[0][0], path[-1][0]), ("Seattle", "Miami"))
            self.assertTrue(2 <= len(path) <= 4)


class TestGenerate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg, cls.t, cls.gt, cls.hidden = generated_world()

    def test_every_table_exists_and_sizes_match_config(self):
        self.assertEqual(set(self.t), set(TABLE_ORDER))
        self.assertEqual(len(self.t["carrier"]), self.cfg.carriers)
        self.assertEqual(len(self.t["shipment"]), self.cfg.shipments)
        self.assertEqual(len(self.t["freight_invoice"]), self.cfg.invoices)
        self.assertEqual(len(self.t["delivery_record"]), self.cfg.shipments)
        self.assertEqual(len(self.t["lane"]), self.cfg.lanes)

    def test_two_rate_cards_per_carrier_linked_in_order(self):
        per = Counter(c["carrier_id"] for c in self.t["rate_card"])
        self.assertTrue(all(n == 2 for n in per.values()))
        for c in self.t["rate_card"]:
            if c["version"] == "2026-02":
                self.assertEqual(c["previous_rate_card_id"], c["rate_card_id"].replace("-02", "-01"))

    def test_shipments_sorted_by_date_with_sequential_ids(self):
        s = self.t["shipment"]
        self.assertEqual([x["shipment_id"] for x in s], [f"SHP-2026-{i:06d}" for i in range(1, len(s) + 1)])
        self.assertEqual([x["ship_date"] for x in s], sorted(x["ship_date"] for x in s))

    def test_invoices_sorted_by_date_with_sequential_ids(self):
        inv = self.t["freight_invoice"]
        self.assertEqual([x["invoice_id"] for x in inv], [f"FINV-2026-{i:06d}" for i in range(1, len(inv) + 1)])
        self.assertEqual([x["invoice_date"] for x in inv], sorted(x["invoice_date"] for x in inv))

    def test_every_shipment_has_a_shipper_measurement_and_four_history_events(self):
        bol = {m["shipment_id"] for m in self.t["weight_measurement"] if m["measurement_source"] == "SHIPPER_BOL"}
        self.assertEqual(bol, {s["shipment_id"] for s in self.t["shipment"]})
        per = Counter(e["shipment_id"] for e in self.t["shipment_status_history"])
        self.assertTrue(all(n == 4 for n in per.values()))

    def test_answer_key_covers_every_invoice_level_type_at_least_three_times(self):
        counts = Counter(x["issue_type"] for x in self.gt)
        for issue in set(INJECTED_ISSUE_TYPES) - {"CREDIT_EXCEEDS_EXCEPTION"}:   # credits are planted later
            self.assertGreaterEqual(counts[issue], self.cfg.min_cases_per_type, issue)

    def test_labels_point_to_real_invoices(self):
        ids = {i["invoice_id"] for i in self.t["freight_invoice"]}
        self.assertTrue(all(x["entity_id"] in ids for x in self.gt))

    def test_unknown_and_duplicate_labels_are_true(self):
        ships = {s["shipment_id"] for s in self.t["shipment"]}
        inv = {i["invoice_id"]: i for i in self.t["freight_invoice"]}
        for x in self.gt:
            ref = inv[x["entity_id"]]["shipment_ref"]
            if x["issue_type"] == "UNKNOWN_SHIPMENT":
                self.assertNotIn(ref, ships)
            if x["issue_type"] == "DUPLICATE_INVOICE":
                earlier = [i for i in self.t["freight_invoice"] if i["shipment_ref"] == ref and i["invoice_id"] < x["entity_id"]]
                self.assertTrue(earlier)

    def test_hidden_carrier_traits_are_not_exported(self):
        self.assertEqual(set(self.hidden["propensity"]), {c["carrier_id"] for c in self.t["carrier"]})
        for col in self.t["carrier"][0]:
            self.assertNotIn("propensity", col)

    def test_same_seed_same_world_different_seed_different_world(self):
        dump = lambda t, gt: json.dumps([t, gt], default=jsonable, sort_keys=True)
        _, t2, gt2, _ = generated_world()
        _, t3, gt3, _ = generated_world(seed=1)
        self.assertEqual(dump(self.t, self.gt), dump(t2, gt2))
        self.assertNotEqual(dump(self.t, self.gt), dump(t3, gt3))


class TestInjectors(unittest.TestCase):
    def setUp(self):
        self.cfg, self.t, self.gt, _ = generated_world()
        self.idx = Index(self.t)
        self.r = random.Random(3)

    def find(self, cond):
        for s in self.t["shipment"]:
            d = draft_for(self.t, self.idx, s, self.cfg)
            if cond(d):
                return d
        raise LookupError

    def test_clean_lines_add_up_to_expected_total(self):
        d = self.find(lambda d: d["exp"].accessorials)
        self.assertEqual(g._total(d["lines"]), d["exp"].total)
        self.assertEqual([l["charge_type"] for l in d["lines"][:2]], ["BASE", "FUEL"])

    def test_weight_tier_uses_the_lighter_dearer_band_and_recomputes_fuel(self):
        d = self.find(lambda d: d["exp"].tier_index > 0 and d["exp"].base > d["exp"].card["min_charge"] * 2)
        self.assertTrue(g._try_inject("WEIGHT_TIER_MISAPPLIED", d, self.idx, self.r, self.cfg))
        self.assertEqual(d["lines"][0]["unit_rate"], d["exp"].tiers[d["exp"].tier_index - 1]["rate_per_lb"])
        self.assertGreater(d["lines"][1]["amount"], d["exp"].fuel)
        self.assertEqual(d["label"]["issue_type"], "WEIGHT_TIER_MISAPPLIED")

    def test_failed_injection_leaves_the_draft_untouched(self):
        d = self.find(lambda d: d["exp"].tier_index == 0)
        before = [dict(l) for l in d["lines"]]
        self.assertFalse(g._try_inject("WEIGHT_TIER_MISAPPLIED", d, self.idx, self.r, self.cfg))
        self.assertEqual(d["lines"], before)
        self.assertIsNone(d["label"])

    def test_dim_weight_inflates_billed_weight(self):
        d = self.find(lambda d: d["exp"].base > d["exp"].card["min_charge"] * 2)
        self.assertTrue(g._try_inject("DIM_WEIGHT_DISCREPANCY", d, self.idx, self.r, self.cfg))
        self.assertGreater(d["billed_weight_lb"], d["exp"].billable_lb)

    def test_expired_card_only_for_second_half_shipments(self):
        h1 = self.find(lambda d: d["ship"]["ship_date"] < self.cfg.h2_start)
        self.assertFalse(g._try_inject("RATE_CARD_EXPIRED", h1, self.idx, self.r, self.cfg))
        h2 = self.find(lambda d: d["ship"]["ship_date"] >= self.cfg.h2_start and d["exp"].base > d["exp"].card["min_charge"])
        self.assertTrue(g._try_inject("RATE_CARD_EXPIRED", h2, self.idx, self.r, self.cfg))
        self.assertTrue(h2["rate_card_id"].endswith("2026-01"))

    def test_fuel_percent_raised(self):
        d = self.find(lambda d: d["exp"].base > 100)
        self.assertTrue(g._try_inject("FUEL_SURCHARGE_MISCALCULATED", d, self.idx, self.r, self.cfg))
        self.assertGreater(d["lines"][1]["unit_rate"], d["exp"].fuel_pct)

    def test_duplicate_surcharge_appends_a_copy(self):
        d = self.find(lambda d: True)
        n = len(d["lines"])
        self.assertTrue(g._try_inject("SURCHARGE_DUPLICATED", d, self.idx, self.r, self.cfg))
        self.assertEqual(len(d["lines"]), n + 1)
        self.assertEqual(d["label"]["line_no"], n + 1)

    def test_invalid_accessorial_was_not_performed_or_not_contracted(self):
        d = self.find(lambda d: True)
        self.assertTrue(g._try_inject("INVALID_ACCESSORIAL", d, self.idx, self.r, self.cfg))
        code = d["lines"][-1]["accessorial_code"]
        performed = self.idx.performed.get(d["ship"]["shipment_id"], {})
        contract = self.idx.card_acc[d["exp"].card["rate_card_id"]]
        self.assertTrue(code not in performed or code not in contract)


if __name__ == "__main__":
    unittest.main()
