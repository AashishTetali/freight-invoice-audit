-- Freight invoice audit - database schema (ANSI SQL)
-- Generated from freight_audit/schema.py. Load schema.sql, then seed.sql.
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS credit_note_status_history;
DROP TABLE IF EXISTS credit_note;
DROP TABLE IF EXISTS exception_status_history;
DROP TABLE IF EXISTS audit_exception;
DROP TABLE IF EXISTS invoice_audit;
DROP TABLE IF EXISTS invoice_status_history;
DROP TABLE IF EXISTS invoice_line;
DROP TABLE IF EXISTS freight_invoice;
DROP TABLE IF EXISTS shipment_status_history;
DROP TABLE IF EXISTS delivery_accessorial;
DROP TABLE IF EXISTS delivery_record;
DROP TABLE IF EXISTS weight_measurement;
DROP TABLE IF EXISTS shipment_leg;
DROP TABLE IF EXISTS shipment;
DROP TABLE IF EXISTS rate_card_accessorial;
DROP TABLE IF EXISTS accessorial;
DROP TABLE IF EXISTS rate_card_line;
DROP TABLE IF EXISTS rate_card;
DROP TABLE IF EXISTS lane;
DROP TABLE IF EXISTS carrier;

-- A trucking company that moves freight for us.
CREATE TABLE carrier (
  carrier_id TEXT NOT NULL,
  carrier_name TEXT NOT NULL,
  scac_code TEXT NOT NULL,
  hq_state TEXT NOT NULL,
  onboarded_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('ACTIVE', 'INACTIVE')),
  PRIMARY KEY (carrier_id),
  UNIQUE (carrier_name),
  UNIQUE (scac_code)
);

-- A route from an origin city to a destination city.
CREATE TABLE lane (
  lane_id TEXT NOT NULL,
  origin_city TEXT NOT NULL,
  origin_state TEXT NOT NULL,
  dest_city TEXT NOT NULL,
  dest_state TEXT NOT NULL,
  distance_miles INTEGER NOT NULL,
  PRIMARY KEY (lane_id),
  UNIQUE (origin_city, origin_state, dest_city, dest_state)
);

-- A carrier's contracted price list for one period. Version 01 = Jan-Jun, 02 = Jul-Dec.
CREATE TABLE rate_card (
  rate_card_id TEXT NOT NULL,
  carrier_id TEXT NOT NULL,
  version TEXT NOT NULL CHECK (version IS NULL OR version IN ('2026-01', '2026-02')),
  effective_from DATE NOT NULL,
  effective_to DATE NOT NULL,
  previous_rate_card_id TEXT,
  currency TEXT NOT NULL CHECK (currency IS NULL OR currency IN ('USD')),
  min_charge DECIMAL(12,2) NOT NULL,
  fuel_surcharge_pct DECIMAL(5,2) NOT NULL,
  dim_factor INTEGER NOT NULL CHECK (dim_factor IS NULL OR dim_factor IN (139, 166)),
  status TEXT NOT NULL CHECK (status IS NULL OR status IN ('ACTIVE', 'EXPIRED')),
  PRIMARY KEY (rate_card_id),
  UNIQUE (carrier_id, version),
  FOREIGN KEY (carrier_id) REFERENCES carrier(carrier_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (previous_rate_card_id) REFERENCES rate_card(rate_card_id) DEFERRABLE INITIALLY DEFERRED
);

-- One price on a rate card: lane + service level + weight tier -> $ per lb.
CREATE TABLE rate_card_line (
  rate_line_id TEXT NOT NULL,
  rate_card_id TEXT NOT NULL,
  lane_id TEXT NOT NULL,
  service_level TEXT NOT NULL CHECK (service_level IS NULL OR service_level IN ('ECONOMY', 'STANDARD', 'EXPEDITED')),
  tier_min_lb INTEGER NOT NULL,
  tier_max_lb INTEGER,
  rate_per_lb DECIMAL(10,4) NOT NULL,
  PRIMARY KEY (rate_line_id),
  UNIQUE (rate_card_id, lane_id, service_level, tier_min_lb),
  FOREIGN KEY (rate_card_id) REFERENCES rate_card(rate_card_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (lane_id) REFERENCES lane(lane_id) DEFERRABLE INITIALLY DEFERRED
);

-- Catalogue of extra services a carrier can charge for.
CREATE TABLE accessorial (
  accessorial_code TEXT NOT NULL CHECK (accessorial_code IS NULL OR accessorial_code IN ('LIFTGATE', 'RESIDENTIAL', 'INSIDE_DELIVERY', 'APPOINTMENT', 'LIMITED_ACCESS', 'HAZMAT', 'DETENTION')),
  description TEXT NOT NULL,
  charge_basis TEXT NOT NULL CHECK (charge_basis IS NULL OR charge_basis IN ('FLAT', 'PER_HOUR')),
  reference_amount DECIMAL(12,2) NOT NULL,
  PRIMARY KEY (accessorial_code)
);

-- Bridge (N:M): which accessorials each rate card allows, at what price.
CREATE TABLE rate_card_accessorial (
  rate_card_id TEXT NOT NULL,
  accessorial_code TEXT NOT NULL,
  contracted_amount DECIMAL(12,2) NOT NULL,
  PRIMARY KEY (rate_card_id, accessorial_code),
  FOREIGN KEY (rate_card_id) REFERENCES rate_card(rate_card_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (accessorial_code) REFERENCES accessorial(accessorial_code) DEFERRABLE INITIALLY DEFERRED
);

-- One booking to move goods from origin to destination.
CREATE TABLE shipment (
  shipment_id TEXT NOT NULL,
  carrier_id TEXT NOT NULL,
  lane_id TEXT NOT NULL,
  service_level TEXT NOT NULL CHECK (service_level IS NULL OR service_level IN ('ECONOMY', 'STANDARD', 'EXPEDITED')),
  ship_date DATE NOT NULL,
  planned_delivery_date DATE NOT NULL,
  pieces INTEGER NOT NULL,
  current_status TEXT NOT NULL CHECK (current_status IS NULL OR current_status IN ('BOOKED', 'PICKED_UP', 'IN_TRANSIT', 'DELIVERED')),
  PRIMARY KEY (shipment_id),
  FOREIGN KEY (carrier_id) REFERENCES carrier(carrier_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (lane_id) REFERENCES lane(lane_id) DEFERRABLE INITIALLY DEFERRED
);

-- One segment of a shipment's journey (direct = 1 leg, via a hub = 2-3 legs).
CREATE TABLE shipment_leg (
  leg_id TEXT NOT NULL,
  shipment_id TEXT NOT NULL,
  leg_seq INTEGER NOT NULL,
  origin_city TEXT NOT NULL,
  origin_state TEXT NOT NULL,
  dest_city TEXT NOT NULL,
  dest_state TEXT NOT NULL,
  leg_miles INTEGER NOT NULL,
  depart_date DATE NOT NULL,
  arrive_date DATE NOT NULL,
  PRIMARY KEY (leg_id),
  UNIQUE (shipment_id, leg_seq),
  FOREIGN KEY (shipment_id) REFERENCES shipment(shipment_id) DEFERRABLE INITIALLY DEFERRED
);

-- A weighing/measuring of a shipment. Shipper declares it; carriers sometimes re-weigh. Rule: the latest CARRIER_REWEIGH wins, otherwise the SHIPPER_BOL.
CREATE TABLE weight_measurement (
  measurement_id TEXT NOT NULL,
  shipment_id TEXT NOT NULL,
  measurement_source TEXT NOT NULL CHECK (measurement_source IS NULL OR measurement_source IN ('SHIPPER_BOL', 'CARRIER_REWEIGH')),
  measured_by_role TEXT NOT NULL CHECK (measured_by_role IS NULL OR measured_by_role IN ('SUPPLIER', 'PLANNER', 'CARRIER', 'WAREHOUSE_OPERATOR', 'AUDIT_ENGINE', 'LOGISTICS_ANALYST', 'OPERATIONS_MANAGER')),
  measured_date DATE NOT NULL,
  pieces INTEGER NOT NULL,
  length_in INTEGER NOT NULL,
  width_in INTEGER NOT NULL,
  height_in INTEGER NOT NULL,
  actual_weight_lb DECIMAL(10,1) NOT NULL,
  PRIMARY KEY (measurement_id),
  FOREIGN KEY (shipment_id) REFERENCES shipment(shipment_id) DEFERRABLE INITIALLY DEFERRED
);

-- Proof of delivery: what actually happened at drop-off.
CREATE TABLE delivery_record (
  delivery_id TEXT NOT NULL,
  shipment_id TEXT NOT NULL,
  delivered_date DATE NOT NULL,
  delivery_outcome TEXT NOT NULL CHECK (delivery_outcome IS NULL OR delivery_outcome IN ('ON_TIME', 'LATE')),
  wait_hours DECIMAL(5,2) NOT NULL,
  recorded_by_role TEXT NOT NULL CHECK (recorded_by_role IS NULL OR recorded_by_role IN ('SUPPLIER', 'PLANNER', 'CARRIER', 'WAREHOUSE_OPERATOR', 'AUDIT_ENGINE', 'LOGISTICS_ANALYST', 'OPERATIONS_MANAGER')),
  PRIMARY KEY (delivery_id),
  UNIQUE (shipment_id),
  FOREIGN KEY (shipment_id) REFERENCES shipment(shipment_id) DEFERRABLE INITIALLY DEFERRED
);

-- Bridge (N:M): extra services actually performed at delivery.
CREATE TABLE delivery_accessorial (
  delivery_id TEXT NOT NULL,
  accessorial_code TEXT NOT NULL,
  quantity DECIMAL(5,2) NOT NULL,
  PRIMARY KEY (delivery_id, accessorial_code),
  FOREIGN KEY (delivery_id) REFERENCES delivery_record(delivery_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (accessorial_code) REFERENCES accessorial(accessorial_code) DEFERRABLE INITIALLY DEFERRED
);

-- Every status change of a shipment (never overwritten).
CREATE TABLE shipment_status_history (
  event_id TEXT NOT NULL,
  shipment_id TEXT NOT NULL,
  from_status TEXT CHECK (from_status IS NULL OR from_status IN ('BOOKED', 'PICKED_UP', 'IN_TRANSIT', 'DELIVERED')),
  to_status TEXT NOT NULL CHECK (to_status IS NULL OR to_status IN ('BOOKED', 'PICKED_UP', 'IN_TRANSIT', 'DELIVERED')),
  event_date DATE NOT NULL,
  actor_role TEXT NOT NULL CHECK (actor_role IS NULL OR actor_role IN ('SUPPLIER', 'PLANNER', 'CARRIER', 'WAREHOUSE_OPERATOR', 'AUDIT_ENGINE', 'LOGISTICS_ANALYST', 'OPERATIONS_MANAGER')),
  PRIMARY KEY (event_id),
  FOREIGN KEY (shipment_id) REFERENCES shipment(shipment_id) DEFERRABLE INITIALLY DEFERRED
);

-- A carrier's bill. Values are exactly what the carrier submitted (untrusted input).
CREATE TABLE freight_invoice (
  invoice_id TEXT NOT NULL,
  carrier_id TEXT NOT NULL,
  shipment_ref TEXT NOT NULL,
  rate_card_id TEXT NOT NULL,
  invoice_date DATE NOT NULL,
  billed_weight_lb INTEGER NOT NULL,
  total_billed DECIMAL(12,2) NOT NULL,
  currency TEXT NOT NULL CHECK (currency IS NULL OR currency IN ('USD')),
  current_status TEXT NOT NULL CHECK (current_status IS NULL OR current_status IN ('RECEIVED', 'AUDITED', 'APPROVED', 'DISPUTED', 'REJECTED', 'RESOLVED', 'ESCALATED', 'PAID')),
  PRIMARY KEY (invoice_id),
  FOREIGN KEY (carrier_id) REFERENCES carrier(carrier_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (rate_card_id) REFERENCES rate_card(rate_card_id) DEFERRABLE INITIALLY DEFERRED
);

-- One charge row on a carrier invoice.
CREATE TABLE invoice_line (
  line_id TEXT NOT NULL,
  invoice_id TEXT NOT NULL,
  line_no INTEGER NOT NULL,
  charge_type TEXT NOT NULL CHECK (charge_type IS NULL OR charge_type IN ('BASE', 'FUEL', 'ACCESSORIAL')),
  accessorial_code TEXT,
  quantity DECIMAL(12,2) NOT NULL,
  unit TEXT NOT NULL CHECK (unit IS NULL OR unit IN ('LB', 'PCT_OF_BASE', 'EACH', 'HOUR')),
  unit_rate DECIMAL(10,4) NOT NULL,
  min_charge_applied INTEGER NOT NULL CHECK (min_charge_applied IS NULL OR min_charge_applied IN (0, 1)),
  amount DECIMAL(12,2) NOT NULL,
  PRIMARY KEY (line_id),
  UNIQUE (invoice_id, line_no),
  FOREIGN KEY (invoice_id) REFERENCES freight_invoice(invoice_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (accessorial_code) REFERENCES accessorial(accessorial_code) DEFERRABLE INITIALLY DEFERRED
);

-- Every status change of an invoice.
CREATE TABLE invoice_status_history (
  event_id TEXT NOT NULL,
  invoice_id TEXT NOT NULL,
  from_status TEXT CHECK (from_status IS NULL OR from_status IN ('RECEIVED', 'AUDITED', 'APPROVED', 'DISPUTED', 'REJECTED', 'RESOLVED', 'ESCALATED', 'PAID')),
  to_status TEXT NOT NULL CHECK (to_status IS NULL OR to_status IN ('RECEIVED', 'AUDITED', 'APPROVED', 'DISPUTED', 'REJECTED', 'RESOLVED', 'ESCALATED', 'PAID')),
  event_date DATE NOT NULL,
  actor_role TEXT NOT NULL CHECK (actor_role IS NULL OR actor_role IN ('SUPPLIER', 'PLANNER', 'CARRIER', 'WAREHOUSE_OPERATOR', 'AUDIT_ENGINE', 'LOGISTICS_ANALYST', 'OPERATIONS_MANAGER')),
  note TEXT,
  PRIMARY KEY (event_id),
  FOREIGN KEY (invoice_id) REFERENCES freight_invoice(invoice_id) DEFERRABLE INITIALLY DEFERRED
);

-- Audit engine result per invoice: what it SHOULD have cost (1:1 with invoice).
CREATE TABLE invoice_audit (
  invoice_id TEXT NOT NULL,
  shipment_id TEXT,
  expected_rate_card_id TEXT,
  expected_billable_weight_lb INTEGER,
  audit_date DATE NOT NULL,
  billed_total DECIMAL(12,2) NOT NULL,
  expected_total DECIMAL(12,2) NOT NULL,
  variance_total DECIMAL(12,2) NOT NULL,
  exception_count INTEGER NOT NULL,
  audit_result TEXT NOT NULL CHECK (audit_result IS NULL OR audit_result IN ('CLEAN', 'EXCEPTIONS', 'REJECTED')),
  PRIMARY KEY (invoice_id),
  FOREIGN KEY (invoice_id) REFERENCES freight_invoice(invoice_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (shipment_id) REFERENCES shipment(shipment_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (expected_rate_card_id) REFERENCES rate_card(rate_card_id) DEFERRABLE INITIALLY DEFERRED
);

-- A problem found by the audit, tied to the exact invoice line that is wrong.
CREATE TABLE audit_exception (
  exception_id TEXT NOT NULL,
  invoice_id TEXT NOT NULL,
  line_id TEXT,
  shipment_id TEXT,
  rate_card_id TEXT,
  root_exception_id TEXT,
  credit_note_id TEXT,
  exception_type TEXT NOT NULL CHECK (exception_type IS NULL OR exception_type IN ('UNKNOWN_SHIPMENT', 'CARRIER_MISMATCH', 'DUPLICATE_INVOICE', 'RATE_CARD_EXPIRED', 'RATE_CARD_MISMATCH', 'DIM_WEIGHT_DISCREPANCY', 'WEIGHT_TIER_MISAPPLIED', 'BASE_RATE_MISMATCH', 'BASE_AMOUNT_MISMATCH', 'FUEL_SURCHARGE_MISCALCULATED', 'FUEL_ON_INCORRECT_BASE', 'SURCHARGE_DUPLICATED', 'INVALID_ACCESSORIAL', 'ACCESSORIAL_AMOUNT_MISMATCH', 'UNBILLED_CHARGE', 'CREDIT_EXCEEDS_EXCEPTION')),
  expected_amount DECIMAL(12,2) NOT NULL,
  billed_amount DECIMAL(12,2) NOT NULL,
  variance_amount DECIMAL(12,2) NOT NULL,
  approved_amount DECIMAL(12,2) NOT NULL,
  detail TEXT NOT NULL,
  detected_date DATE NOT NULL,
  current_status TEXT NOT NULL CHECK (current_status IS NULL OR current_status IN ('OPEN', 'WAIVED', 'DISPUTED', 'INVOICE_REJECTED', 'ACKNOWLEDGED', 'CREDITED', 'PARTIALLY_CREDITED', 'CARRIER_DECLINED', 'CREDIT_ON_HOLD')),
  PRIMARY KEY (exception_id),
  FOREIGN KEY (invoice_id) REFERENCES freight_invoice(invoice_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (line_id) REFERENCES invoice_line(line_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (shipment_id) REFERENCES shipment(shipment_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (rate_card_id) REFERENCES rate_card(rate_card_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (root_exception_id) REFERENCES audit_exception(exception_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (credit_note_id) REFERENCES credit_note(credit_note_id) DEFERRABLE INITIALLY DEFERRED
);

-- Every status change of an exception.
CREATE TABLE exception_status_history (
  event_id TEXT NOT NULL,
  exception_id TEXT NOT NULL,
  from_status TEXT CHECK (from_status IS NULL OR from_status IN ('OPEN', 'WAIVED', 'DISPUTED', 'INVOICE_REJECTED', 'ACKNOWLEDGED', 'CREDITED', 'PARTIALLY_CREDITED', 'CARRIER_DECLINED', 'CREDIT_ON_HOLD')),
  to_status TEXT NOT NULL CHECK (to_status IS NULL OR to_status IN ('OPEN', 'WAIVED', 'DISPUTED', 'INVOICE_REJECTED', 'ACKNOWLEDGED', 'CREDITED', 'PARTIALLY_CREDITED', 'CARRIER_DECLINED', 'CREDIT_ON_HOLD')),
  event_date DATE NOT NULL,
  actor_role TEXT NOT NULL CHECK (actor_role IS NULL OR actor_role IN ('SUPPLIER', 'PLANNER', 'CARRIER', 'WAREHOUSE_OPERATOR', 'AUDIT_ENGINE', 'LOGISTICS_ANALYST', 'OPERATIONS_MANAGER')),
  note TEXT,
  PRIMARY KEY (event_id),
  FOREIGN KEY (exception_id) REFERENCES audit_exception(exception_id) DEFERRABLE INITIALLY DEFERRED
);

-- Money the carrier gives back for a disputed exception (0..N per exception).
CREATE TABLE credit_note (
  credit_note_id TEXT NOT NULL,
  exception_id TEXT NOT NULL,
  invoice_id TEXT NOT NULL,
  carrier_id TEXT NOT NULL,
  issued_date DATE NOT NULL,
  credit_amount DECIMAL(12,2) NOT NULL,
  current_status TEXT NOT NULL CHECK (current_status IS NULL OR current_status IN ('ISSUED', 'APPLIED', 'HELD')),
  PRIMARY KEY (credit_note_id),
  FOREIGN KEY (exception_id) REFERENCES audit_exception(exception_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (invoice_id) REFERENCES freight_invoice(invoice_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (carrier_id) REFERENCES carrier(carrier_id) DEFERRABLE INITIALLY DEFERRED
);

-- Every status change of a credit note.
CREATE TABLE credit_note_status_history (
  event_id TEXT NOT NULL,
  credit_note_id TEXT NOT NULL,
  from_status TEXT CHECK (from_status IS NULL OR from_status IN ('ISSUED', 'APPLIED', 'HELD')),
  to_status TEXT NOT NULL CHECK (to_status IS NULL OR to_status IN ('ISSUED', 'APPLIED', 'HELD')),
  event_date DATE NOT NULL,
  actor_role TEXT NOT NULL CHECK (actor_role IS NULL OR actor_role IN ('SUPPLIER', 'PLANNER', 'CARRIER', 'WAREHOUSE_OPERATOR', 'AUDIT_ENGINE', 'LOGISTICS_ANALYST', 'OPERATIONS_MANAGER')),
  note TEXT,
  PRIMARY KEY (event_id),
  FOREIGN KEY (credit_note_id) REFERENCES credit_note(credit_note_id) DEFERRABLE INITIALLY DEFERRED
);
