"""Single source of truth for every table.

This one file drives three things, so they can never disagree:
  * the SQL schema (CREATE TABLE statements),
  * the validation checks (required fields, allowed values, ID formats, keys),
  * the data dictionary document (DATA_DICTIONARY.md).
"""
from dataclasses import dataclass, field

# ---------------------------------------------------------------- enums
SERVICE_LEVELS = ("ECONOMY", "STANDARD", "EXPEDITED")
ACCESSORIAL_CODES = ("LIFTGATE", "RESIDENTIAL", "INSIDE_DELIVERY", "APPOINTMENT",
                     "LIMITED_ACCESS", "HAZMAT", "DETENTION")
CHARGE_TYPES = ("BASE", "FUEL", "ACCESSORIAL")
LINE_UNITS = ("LB", "PCT_OF_BASE", "EACH", "HOUR")
MEASUREMENT_SOURCES = ("SHIPPER_BOL", "CARRIER_REWEIGH")
ROLES = ("SUPPLIER", "PLANNER", "CARRIER", "WAREHOUSE_OPERATOR", "AUDIT_ENGINE",
         "LOGISTICS_ANALYST", "OPERATIONS_MANAGER")

SHIPMENT_STATUSES = ("BOOKED", "PICKED_UP", "IN_TRANSIT", "DELIVERED")
INVOICE_STATUSES = ("RECEIVED", "AUDITED", "APPROVED", "DISPUTED", "REJECTED",
                    "RESOLVED", "ESCALATED", "PAID")
EXCEPTION_STATUSES = ("OPEN", "WAIVED", "DISPUTED", "INVOICE_REJECTED", "ACKNOWLEDGED",
                      "CREDITED", "PARTIALLY_CREDITED", "CARRIER_DECLINED", "CREDIT_ON_HOLD")
CREDIT_STATUSES = ("ISSUED", "APPLIED", "HELD")

# Which status may follow which. None = "record created".
TRANSITIONS = {
    "shipment": {None: {"BOOKED"}, "BOOKED": {"PICKED_UP"}, "PICKED_UP": {"IN_TRANSIT"},
                 "IN_TRANSIT": {"DELIVERED"}},
    "invoice": {None: {"RECEIVED"}, "RECEIVED": {"AUDITED"},
                "AUDITED": {"APPROVED", "DISPUTED", "REJECTED"}, "APPROVED": {"PAID"},
                "DISPUTED": {"RESOLVED", "ESCALATED"}, "RESOLVED": {"PAID"}},
    "exception": {None: {"OPEN"},
                  "OPEN": {"WAIVED", "DISPUTED", "INVOICE_REJECTED", "ACKNOWLEDGED"},
                  "DISPUTED": {"CREDITED", "PARTIALLY_CREDITED", "CARRIER_DECLINED",
                               "CREDIT_ON_HOLD"}},
    "credit_note": {None: {"ISSUED"}, "ISSUED": {"APPLIED", "HELD"}},
}

# Exception types the audit engine can raise.
EXCEPTION_TYPES = (
    "UNKNOWN_SHIPMENT", "CARRIER_MISMATCH", "DUPLICATE_INVOICE", "RATE_CARD_EXPIRED",
    "RATE_CARD_MISMATCH", "DIM_WEIGHT_DISCREPANCY", "WEIGHT_TIER_MISAPPLIED",
    "BASE_RATE_MISMATCH", "BASE_AMOUNT_MISMATCH", "FUEL_SURCHARGE_MISCALCULATED",
    "FUEL_ON_INCORRECT_BASE", "SURCHARGE_DUPLICATED", "INVALID_ACCESSORIAL",
    "ACCESSORIAL_AMOUNT_MISMATCH", "UNBILLED_CHARGE", "CREDIT_EXCEEDS_EXCEPTION",
)
# A knock-on effect of another exception (not an independent error).
DERIVED_EXCEPTION_TYPES = ("FUEL_ON_INCORRECT_BASE",)

# Issue types that the generator deliberately plants (ground truth labels).
INJECTED_ISSUE_TYPES = (
    "UNKNOWN_SHIPMENT", "DUPLICATE_INVOICE", "RATE_CARD_EXPIRED", "WEIGHT_TIER_MISAPPLIED",
    "DIM_WEIGHT_DISCREPANCY", "FUEL_SURCHARGE_MISCALCULATED", "SURCHARGE_DUPLICATED",
    "INVALID_ACCESSORIAL", "CREDIT_EXCEEDS_EXCEPTION",
)
EXPECTED_DECISIONS = ("REJECT_INVOICE", "DISPUTE", "WAIVE", "HOLD_CREDIT")

YEAR_ID = r"\d{4}-\d{6}"


@dataclass
class Col:
    name: str
    type: str                 # ID, TEXT, INT, MONEY, RATE, PCT, WEIGHT, DATE, FLAG
    desc: str
    required: bool = True
    enum: tuple = None
    unit: str = ""
    unique: bool = False
    fk: str = None            # "table.column"
    pattern: str = None       # regular expression the value must match
    rule: str = ""            # extra validation rule, in words
    deferred_fk: bool = False


@dataclass
class Table:
    name: str
    group: str                # master / bridge / transaction / event / audit
    desc: str
    pk: tuple
    cols: list
    unique_together: list = field(default_factory=list)


SQL_TYPES = {"ID": "TEXT", "TEXT": "TEXT", "INT": "INTEGER", "MONEY": "DECIMAL(12,2)",
             "RATE": "DECIMAL(10,4)", "PCT": "DECIMAL(5,2)", "WEIGHT": "DECIMAL(10,1)",
             "DATE": "DATE", "FLAG": "INTEGER"}

C = Col
TABLES = [
    # ------------------------------------------------------------ master data
    Table("carrier", "master", "A trucking company that moves freight for us.", ("carrier_id",), [
        C("carrier_id", "ID", "Carrier identifier", pattern=r"^CAR-\d{6}$", unique=True),
        C("carrier_name", "TEXT", "Trading name (fictional)", unique=True),
        C("scac_code", "TEXT", "4-letter Standard Carrier Alpha Code (fictional)", unique=True,
          pattern=r"^[A-Z]{4}$"),
        C("hq_state", "TEXT", "US state of head office", pattern=r"^[A-Z]{2}$"),
        C("onboarded_date", "DATE", "Date the carrier was approved to haul for us",
          rule="before the first rate card starts"),
        C("status", "TEXT", "Carrier status", enum=("ACTIVE", "INACTIVE")),
    ]),
    Table("lane", "master", "A route from an origin city to a destination city.", ("lane_id",), [
        C("lane_id", "ID", "Lane identifier", pattern=r"^LANE-\d{6}$", unique=True),
        C("origin_city", "TEXT", "Origin city"),
        C("origin_state", "TEXT", "Origin state", pattern=r"^[A-Z]{2}$"),
        C("dest_city", "TEXT", "Destination city", rule="differs from origin_city"),
        C("dest_state", "TEXT", "Destination state", pattern=r"^[A-Z]{2}$"),
        C("distance_miles", "INT", "Road distance estimate", unit="miles", rule="> 0"),
    ], unique_together=[("origin_city", "origin_state", "dest_city", "dest_state")]),
    Table("rate_card", "master",
          "A carrier's contracted price list for one period. Version 01 = Jan-Jun, 02 = Jul-Dec.",
          ("rate_card_id",), [
        C("rate_card_id", "ID", "Rate card identifier: RATE-<carrier-id>-<year>-<version>",
          pattern=r"^RATE-CAR-\d{6}-\d{4}-\d{2}$", unique=True),
        C("carrier_id", "ID", "Carrier that owns this contract", fk="carrier.carrier_id"),
        C("version", "TEXT", "Period version within the year", enum=("2026-01", "2026-02")),
        C("effective_from", "DATE", "First day the card is valid"),
        C("effective_to", "DATE", "Last day the card is valid", rule=">= effective_from"),
        C("previous_rate_card_id", "ID", "The card this one replaced", required=False,
          fk="rate_card.rate_card_id"),
        C("currency", "TEXT", "Currency of all amounts", enum=("USD",)),
        C("min_charge", "MONEY", "Lowest base charge for any shipment", unit="USD", rule="> 0"),
        C("fuel_surcharge_pct", "PCT", "Fuel surcharge as % of base freight", unit="%",
          rule="0 < pct < 50"),
        C("dim_factor", "INT", "Divisor for dimensional weight", unit="cubic inches per lb",
          enum=(139, 166)),
        C("status", "TEXT", "Status on the as-of date (derived from effective dates)",
          enum=("ACTIVE", "EXPIRED")),
    ], unique_together=[("carrier_id", "version")]),
    Table("rate_card_line", "master",
          "One price on a rate card: lane + service level + weight tier -> $ per lb.",
          ("rate_line_id",), [
        C("rate_line_id", "ID", "Rate line identifier", pattern=r"^RLN-\d{6}$", unique=True),
        C("rate_card_id", "ID", "Parent rate card", fk="rate_card.rate_card_id"),
        C("lane_id", "ID", "Lane this price applies to", fk="lane.lane_id"),
        C("service_level", "TEXT", "Service level", enum=SERVICE_LEVELS),
        C("tier_min_lb", "INT", "Lowest billable weight in this tier (inclusive)", unit="lb"),
        C("tier_max_lb", "INT", "Highest billable weight in this tier (inclusive); empty = no limit",
          required=False, unit="lb", rule="> tier_min_lb"),
        C("rate_per_lb", "RATE", "Price per billable pound", unit="USD/lb",
          rule="decreases as tiers get heavier"),
    ], unique_together=[("rate_card_id", "lane_id", "service_level", "tier_min_lb")]),
    Table("accessorial", "master", "Catalogue of extra services a carrier can charge for.",
          ("accessorial_code",), [
        C("accessorial_code", "ID", "Accessorial code", enum=ACCESSORIAL_CODES, unique=True),
        C("description", "TEXT", "What the service is"),
        C("charge_basis", "TEXT", "How it is charged", enum=("FLAT", "PER_HOUR")),
        C("reference_amount", "MONEY", "Typical market price (contracts may differ)", unit="USD"),
    ]),
    Table("rate_card_accessorial", "bridge",
          "Bridge (N:M): which accessorials each rate card allows, at what price.",
          ("rate_card_id", "accessorial_code"), [
        C("rate_card_id", "ID", "Rate card", fk="rate_card.rate_card_id"),
        C("accessorial_code", "ID", "Accessorial allowed by the contract",
          fk="accessorial.accessorial_code"),
        C("contracted_amount", "MONEY", "Contract price per unit (each or per hour)", unit="USD"),
    ]),
    # ------------------------------------------------------------ shipments
    Table("shipment", "transaction", "One booking to move goods from origin to destination.",
          ("shipment_id",), [
        C("shipment_id", "ID", "Shipment identifier", pattern=rf"^SHP-{YEAR_ID}$", unique=True),
        C("carrier_id", "ID", "Carrier hauling it", fk="carrier.carrier_id"),
        C("lane_id", "ID", "Lane", fk="lane.lane_id",
          rule="carrier has a rate on this lane+service"),
        C("service_level", "TEXT", "Service level booked", enum=SERVICE_LEVELS),
        C("ship_date", "DATE", "Pick-up date"),
        C("planned_delivery_date", "DATE", "Promised delivery date", rule=">= ship_date"),
        C("pieces", "INT", "Number of handling units (pallets/crates)", rule="1..6"),
        C("current_status", "TEXT", "Latest status (derived from shipment_status_history)",
          enum=SHIPMENT_STATUSES),
    ]),
    Table("shipment_leg", "transaction",
          "One segment of a shipment's journey (direct = 1 leg, via a hub = 2-3 legs).",
          ("leg_id",), [
        C("leg_id", "ID", "Leg identifier: <shipment-id>-L<n>", pattern=rf"^SHP-{YEAR_ID}-L\d$",
          unique=True),
        C("shipment_id", "ID", "Parent shipment", fk="shipment.shipment_id"),
        C("leg_seq", "INT", "Order of the leg, starting at 1"),
        C("origin_city", "TEXT", "Leg start city", rule="leg 1 starts at the lane origin"),
        C("origin_state", "TEXT", "Leg start state"),
        C("dest_city", "TEXT", "Leg end city", rule="last leg ends at the lane destination"),
        C("dest_state", "TEXT", "Leg end state"),
        C("leg_miles", "INT", "Leg distance", unit="miles"),
        C("depart_date", "DATE", "Leg departure", rule=">= previous leg arrival"),
        C("arrive_date", "DATE", "Leg arrival", rule=">= depart_date"),
    ], unique_together=[("shipment_id", "leg_seq")]),
    Table("weight_measurement", "transaction",
          "A weighing/measuring of a shipment. Shipper declares it; carriers sometimes re-weigh. "
          "Rule: the latest CARRIER_REWEIGH wins, otherwise the SHIPPER_BOL.",
          ("measurement_id",), [
        C("measurement_id", "ID", "Measurement identifier", pattern=rf"^WM-{YEAR_ID}$",
          unique=True),
        C("shipment_id", "ID", "Shipment measured", fk="shipment.shipment_id"),
        C("measurement_source", "TEXT", "Who produced it", enum=MEASUREMENT_SOURCES),
        C("measured_by_role", "TEXT", "Actor role", enum=ROLES),
        C("measured_date", "DATE", "Date measured", rule=">= ship_date"),
        C("pieces", "INT", "Handling units counted"),
        C("length_in", "INT", "Length of one piece", unit="inches"),
        C("width_in", "INT", "Width of one piece", unit="inches"),
        C("height_in", "INT", "Height of one piece", unit="inches"),
        C("actual_weight_lb", "WEIGHT", "Scale weight of the whole shipment", unit="lb",
          rule="> 0"),
    ]),
    Table("delivery_record", "transaction", "Proof of delivery: what actually happened at drop-off.",
          ("delivery_id",), [
        C("delivery_id", "ID", "Delivery identifier", pattern=rf"^DLV-{YEAR_ID}$", unique=True),
        C("shipment_id", "ID", "Shipment delivered (1:1)", fk="shipment.shipment_id", unique=True),
        C("delivered_date", "DATE", "Actual delivery date", rule="= last leg arrival"),
        C("delivery_outcome", "TEXT", "ON_TIME if delivered_date <= planned_delivery_date",
          enum=("ON_TIME", "LATE")),
        C("wait_hours", "PCT", "Hours the driver waited at delivery", unit="hours",
          rule="detention billable above 2 free hours"),
        C("recorded_by_role", "TEXT", "Actor role", enum=ROLES),
    ]),
    Table("delivery_accessorial", "bridge",
          "Bridge (N:M): extra services actually performed at delivery.",
          ("delivery_id", "accessorial_code"), [
        C("delivery_id", "ID", "Delivery record", fk="delivery_record.delivery_id"),
        C("accessorial_code", "ID", "Service performed", fk="accessorial.accessorial_code",
          rule="must be allowed by the shipment's rate card"),
        C("quantity", "PCT", "Units performed (1 for flat services, hours for detention)"),
    ]),
    Table("shipment_status_history", "event", "Every status change of a shipment (never overwritten).",
          ("event_id",), [
        C("event_id", "ID", "Event identifier", pattern=r"^SEV-\d{7}$", unique=True),
        C("shipment_id", "ID", "Shipment", fk="shipment.shipment_id"),
        C("from_status", "TEXT", "Previous status (empty for the first event)", required=False,
          enum=SHIPMENT_STATUSES),
        C("to_status", "TEXT", "New status", enum=SHIPMENT_STATUSES),
        C("event_date", "DATE", "When it changed", rule="non-decreasing per shipment"),
        C("actor_role", "TEXT", "Who changed it", enum=ROLES),
    ]),
    # ------------------------------------------------------------ invoices
    Table("freight_invoice", "transaction",
          "A carrier's bill. Values are exactly what the carrier submitted (untrusted input).",
          ("invoice_id",), [
        C("invoice_id", "ID", "Invoice identifier", pattern=rf"^FINV-{YEAR_ID}$", unique=True),
        C("carrier_id", "ID", "Carrier that sent the bill", fk="carrier.carrier_id"),
        C("shipment_ref", "TEXT",
          "Shipment number written on the bill. Deliberately NOT a foreign key: it is untrusted "
          "carrier input and the audit checks it", pattern=rf"^SHP-{YEAR_ID}$"),
        C("rate_card_id", "ID", "Rate card the carrier says it used", fk="rate_card.rate_card_id"),
        C("invoice_date", "DATE", "Bill date", rule=">= delivered_date"),
        C("billed_weight_lb", "INT", "Weight the carrier charged for", unit="lb"),
        C("total_billed", "MONEY", "Invoice total", unit="USD", rule="= sum of its lines"),
        C("currency", "TEXT", "Currency", enum=("USD",)),
        C("current_status", "TEXT", "Latest status (derived from invoice_status_history)",
          enum=INVOICE_STATUSES),
    ]),
    Table("invoice_line", "transaction", "One charge row on a carrier invoice.", ("line_id",), [
        C("line_id", "ID", "Line identifier: FLINE-<invoice-id>-<nn>",
          pattern=rf"^FLINE-FINV-{YEAR_ID}-\d{{2}}$", unique=True),
        C("invoice_id", "ID", "Parent invoice", fk="freight_invoice.invoice_id"),
        C("line_no", "INT", "Line number, starting at 1"),
        C("charge_type", "TEXT", "Kind of charge", enum=CHARGE_TYPES),
        C("accessorial_code", "ID", "Accessorial billed (ACCESSORIAL lines only)", required=False,
          fk="accessorial.accessorial_code"),
        C("quantity", "MONEY", "lb for BASE, base $ for FUEL, units/hours for ACCESSORIAL"),
        C("unit", "TEXT", "Unit of quantity", enum=LINE_UNITS),
        C("unit_rate", "RATE", "$/lb for BASE, % for FUEL, $/unit for ACCESSORIAL"),
        C("min_charge_applied", "FLAG", "1 if the minimum charge replaced weight x rate",
          enum=(0, 1)),
        C("amount", "MONEY", "Line amount", unit="USD",
          rule="= quantity x unit_rate (or min charge)"),
    ], unique_together=[("invoice_id", "line_no")]),
    Table("invoice_status_history", "event", "Every status change of an invoice.", ("event_id",), [
        C("event_id", "ID", "Event identifier", pattern=r"^IEV-\d{7}$", unique=True),
        C("invoice_id", "ID", "Invoice", fk="freight_invoice.invoice_id"),
        C("from_status", "TEXT", "Previous status", required=False, enum=INVOICE_STATUSES),
        C("to_status", "TEXT", "New status", enum=INVOICE_STATUSES),
        C("event_date", "DATE", "When it changed"),
        C("actor_role", "TEXT", "Who changed it", enum=ROLES),
        C("note", "TEXT", "Reason", required=False),
    ]),
    # ------------------------------------------------------------ audit outputs
    Table("invoice_audit", "audit",
          "Audit engine result per invoice: what it SHOULD have cost (1:1 with invoice).",
          ("invoice_id",), [
        C("invoice_id", "ID", "Invoice audited", fk="freight_invoice.invoice_id", unique=True),
        C("shipment_id", "ID", "Shipment matched (empty if unknown)", required=False,
          fk="shipment.shipment_id"),
        C("expected_rate_card_id", "ID", "Rate card in force on the ship date", required=False,
          fk="rate_card.rate_card_id"),
        C("expected_billable_weight_lb", "INT", "max(actual, DIM) weight", required=False,
          unit="lb"),
        C("audit_date", "DATE", "Date audited", rule="invoice_date + 1 day"),
        C("billed_total", "MONEY", "What the carrier billed", unit="USD"),
        C("expected_total", "MONEY", "What it should cost (0 for rejected bills)", unit="USD"),
        C("variance_total", "MONEY", "billed - expected", unit="USD",
          rule="= sum of the invoice's exception variances"),
        C("exception_count", "INT", "Exceptions raised by the audit"),
        C("audit_result", "TEXT", "Outcome", enum=("CLEAN", "EXCEPTIONS", "REJECTED")),
    ]),
    Table("audit_exception", "audit",
          "A problem found by the audit, tied to the exact invoice line that is wrong.",
          ("exception_id",), [
        C("exception_id", "ID", "Exception identifier", pattern=rf"^FEX-{YEAR_ID}$", unique=True),
        C("invoice_id", "ID", "Invoice", fk="freight_invoice.invoice_id"),
        C("line_id", "ID", "Invoice line at fault (empty for invoice-level problems)",
          required=False, fk="invoice_line.line_id"),
        C("shipment_id", "ID", "Shipment (empty if unknown)", required=False,
          fk="shipment.shipment_id"),
        C("rate_card_id", "ID", "Rate card used to compute the expected amount", required=False,
          fk="rate_card.rate_card_id"),
        C("root_exception_id", "ID", "Exception that caused this knock-on one", required=False,
          fk="audit_exception.exception_id"),
        C("credit_note_id", "ID", "Credit note at fault (CREDIT_EXCEEDS_EXCEPTION only)",
          required=False, fk="credit_note.credit_note_id", deferred_fk=True),
        C("exception_type", "TEXT", "Reason code", enum=EXCEPTION_TYPES),
        C("expected_amount", "MONEY", "What the line should be", unit="USD"),
        C("billed_amount", "MONEY", "What was billed", unit="USD"),
        C("variance_amount", "MONEY", "billed - expected", unit="USD"),
        C("approved_amount", "MONEY", "Amount the analyst approved to dispute", unit="USD",
          rule="= variance if disputed, else 0"),
        C("detail", "TEXT", "Plain-English explanation"),
        C("detected_date", "DATE", "Date raised"),
        C("current_status", "TEXT", "Latest status (derived from exception_status_history)",
          enum=EXCEPTION_STATUSES),
    ]),
    Table("exception_status_history", "event", "Every status change of an exception.",
          ("event_id",), [
        C("event_id", "ID", "Event identifier", pattern=r"^EEV-\d{7}$", unique=True),
        C("exception_id", "ID", "Exception", fk="audit_exception.exception_id"),
        C("from_status", "TEXT", "Previous status", required=False, enum=EXCEPTION_STATUSES),
        C("to_status", "TEXT", "New status", enum=EXCEPTION_STATUSES),
        C("event_date", "DATE", "When it changed"),
        C("actor_role", "TEXT", "Who changed it", enum=ROLES),
        C("note", "TEXT", "Reason", required=False),
    ]),
    Table("credit_note", "transaction",
          "Money the carrier gives back for a disputed exception (0..N per exception).",
          ("credit_note_id",), [
        C("credit_note_id", "ID", "Credit note identifier", pattern=rf"^CN-{YEAR_ID}$",
          unique=True),
        C("exception_id", "ID", "Exception being credited", fk="audit_exception.exception_id"),
        C("invoice_id", "ID", "Invoice being credited", fk="freight_invoice.invoice_id"),
        C("carrier_id", "ID", "Carrier issuing it", fk="carrier.carrier_id"),
        C("issued_date", "DATE", "Date issued", rule="after the dispute"),
        C("credit_amount", "MONEY", "Amount credited", unit="USD",
          rule="sum per exception <= approved_amount, else HELD"),
        C("current_status", "TEXT", "Latest status", enum=CREDIT_STATUSES),
    ]),
    Table("credit_note_status_history", "event", "Every status change of a credit note.",
          ("event_id",), [
        C("event_id", "ID", "Event identifier", pattern=r"^CEV-\d{7}$", unique=True),
        C("credit_note_id", "ID", "Credit note", fk="credit_note.credit_note_id"),
        C("from_status", "TEXT", "Previous status", required=False, enum=CREDIT_STATUSES),
        C("to_status", "TEXT", "New status", enum=CREDIT_STATUSES),
        C("event_date", "DATE", "When it changed"),
        C("actor_role", "TEXT", "Who changed it", enum=ROLES),
        C("note", "TEXT", "Reason", required=False),
    ]),
]

# The answer key. Kept OUT of the data folder and never read by the audit engine.
GROUND_TRUTH = Table("ground_truth", "answer-key",
                     "Deliberately planted problems and the decision the rules require.",
                     ("gt_id",), [
    C("gt_id", "ID", "Label identifier", pattern=r"^GT-\d{6}$", unique=True),
    C("entity_type", "TEXT", "What was broken", enum=("INVOICE", "CREDIT_NOTE")),
    C("entity_id", "ID", "invoice_id or credit_note_id"),
    C("issue_type", "TEXT", "Planted problem", enum=INJECTED_ISSUE_TYPES),
    C("line_no", "INT", "Invoice line affected (empty = whole invoice)", required=False),
    C("expected_decision", "TEXT", "Decision the documented rules require",
      enum=EXPECTED_DECISIONS),
    C("expected_variance_amount", "MONEY", "Overbilling (or over-credit) the rules imply",
      unit="USD"),
    C("injection_detail", "TEXT", "What exactly was changed"),
])

TABLE_ORDER = [t.name for t in TABLES]          # parents always before children

# Cardinality notes for the data dictionary (parent, child, text).
RELATIONSHIPS = [
    ("carrier", "rate_card", "1:N - a carrier has one card per period (2 in 2026)"),
    ("rate_card", "rate_card", "1:0..1 - a card may replace a previous card"),
    ("rate_card", "rate_card_line", "1:N - many lane/service/tier prices per card"),
    ("lane", "rate_card_line", "1:N"),
    ("rate_card", "accessorial", "N:M via rate_card_accessorial"),
    ("carrier", "shipment", "1:N"),
    ("lane", "shipment", "1:N"),
    ("shipment", "shipment_leg", "1:N (1-3 legs)"),
    ("shipment", "weight_measurement", "1:N (shipper BOL + optional carrier re-weigh)"),
    ("shipment", "delivery_record", "1:1"),
    ("delivery_record", "accessorial", "N:M via delivery_accessorial"),
    ("shipment", "shipment_status_history", "1:N"),
    ("shipment", "freight_invoice", "1:N by shipment_ref (normally 1; 2+ = duplicate billing). "
                                    "Soft link, validated by the audit"),
    ("rate_card", "freight_invoice", "1:N"),
    ("freight_invoice", "invoice_line", "1:N"),
    ("freight_invoice", "invoice_status_history", "1:N"),
    ("freight_invoice", "invoice_audit", "1:1"),
    ("freight_invoice", "audit_exception", "1:0..N"),
    ("invoice_line", "audit_exception", "1:0..N (optional: invoice-level exceptions have no line)"),
    ("audit_exception", "audit_exception", "1:0..N knock-on exceptions via root_exception_id"),
    ("audit_exception", "credit_note", "1:0..N (optional: only disputed exceptions get credits)"),
    ("credit_note", "audit_exception", "1:0..1 a bad credit raises its own exception"),
    ("audit_exception", "exception_status_history", "1:N"),
    ("credit_note", "credit_note_status_history", "1:N"),
]
