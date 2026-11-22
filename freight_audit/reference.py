"""Small hand-written reference lists used by the generator.

City coordinates are approximate, publicly known geographic facts typed in by
hand (used only to estimate distances). No external dataset is downloaded.
Carrier names are invented by combining two word lists.
"""
from decimal import Decimal

# (city, state, latitude, longitude)
CITIES = [
    ("Chicago", "IL", 41.88, -87.63), ("Dallas", "TX", 32.78, -96.80),
    ("Atlanta", "GA", 33.75, -84.39), ("Los Angeles", "CA", 34.05, -118.24),
    ("Memphis", "TN", 35.15, -90.05), ("Denver", "CO", 39.74, -104.99),
    ("Seattle", "WA", 47.61, -122.33), ("Phoenix", "AZ", 33.45, -112.07),
    ("Houston", "TX", 29.76, -95.37), ("Columbus", "OH", 39.96, -82.99),
    ("Indianapolis", "IN", 39.77, -86.16), ("Kansas City", "MO", 39.10, -94.58),
    ("Nashville", "TN", 36.16, -86.78), ("Charlotte", "NC", 35.23, -80.84),
    ("Newark", "NJ", 40.74, -74.17), ("Philadelphia", "PA", 39.95, -75.17),
    ("Boston", "MA", 42.36, -71.06), ("Miami", "FL", 25.76, -80.19),
    ("Orlando", "FL", 28.54, -81.38), ("Jacksonville", "FL", 30.33, -81.66),
    ("Minneapolis", "MN", 44.98, -93.27), ("St. Louis", "MO", 38.63, -90.20),
    ("Detroit", "MI", 42.33, -83.05), ("Cleveland", "OH", 41.50, -81.69),
    ("Pittsburgh", "PA", 40.44, -79.99), ("Salt Lake City", "UT", 40.76, -111.89),
    ("Las Vegas", "NV", 36.17, -115.14), ("Portland", "OR", 45.52, -122.68),
    ("Sacramento", "CA", 38.58, -121.49), ("San Antonio", "TX", 29.42, -98.49),
    ("Oklahoma City", "OK", 35.47, -97.52), ("Omaha", "NE", 41.26, -95.93),
    ("Louisville", "KY", 38.25, -85.76), ("Cincinnati", "OH", 39.10, -84.51),
    ("Richmond", "VA", 37.54, -77.44), ("Baltimore", "MD", 39.29, -76.61),
    ("Albuquerque", "NM", 35.08, -106.65), ("El Paso", "TX", 31.76, -106.49),
    ("Milwaukee", "WI", 43.04, -87.91), ("Birmingham", "AL", 33.52, -86.80),
]
HUBS = ["Chicago", "Memphis", "Dallas", "Atlanta", "Columbus", "Kansas City", "Denver",
        "Indianapolis", "Nashville", "Louisville"]

CARRIER_PREFIXES = [
    "Ironwood", "Redfern", "Kestrel", "Bluecrest", "Granite Peak", "Silverline", "Northwind",
    "Copperleaf", "Harborview", "Pinecone", "Stonebridge", "Meadowlark", "Driftwood",
    "Falconridge", "Cedar Hollow", "Lakeshore", "Sunvale", "Brightwater", "Oakmont", "Thistle",
    "Riverbend", "Clearpath", "Highplain", "Westgate", "Amberfield",
]
CARRIER_SUFFIXES = ["Freight Lines", "Logistics", "Transport", "Carriers", "Express",
                    "Haulage", "Cartage", "Motor Freight"]

# code: (description, charge basis, typical price)
ACCESSORIALS = {
    "LIFTGATE": ("Hydraulic liftgate used at delivery", "FLAT", Decimal("65.00")),
    "RESIDENTIAL": ("Delivery to a residential address", "FLAT", Decimal("85.00")),
    "INSIDE_DELIVERY": ("Freight carried inside the building", "FLAT", Decimal("95.00")),
    "APPOINTMENT": ("Delivery at a booked time slot", "FLAT", Decimal("25.00")),
    "LIMITED_ACCESS": ("Hard-to-reach site (school, farm, military base)", "FLAT",
                       Decimal("70.00")),
    "HAZMAT": ("Hazardous materials handling", "FLAT", Decimal("40.00")),
    "DETENTION": ("Driver kept waiting beyond 2 free hours", "PER_HOUR", Decimal("75.00")),
}
# Chance a contracted (non-detention) service is actually performed on a delivery.
PERFORM_PROBABILITY = {"LIFTGATE": 0.25, "RESIDENTIAL": 0.12, "INSIDE_DELIVERY": 0.06,
                       "APPOINTMENT": 0.18, "LIMITED_ACCESS": 0.05, "HAZMAT": 0.04}
DETENTION_FREE_HOURS = Decimal("2")

# Weight tiers: (min lb, max lb or None, price multiplier vs. the lightest tier)
WEIGHT_TIERS = [(0, 499, Decimal("1.00")), (500, 999, Decimal("0.85")),
                (1000, 1999, Decimal("0.72")), (2000, 4999, Decimal("0.60")),
                (5000, None, Decimal("0.50"))]
SERVICE_MULTIPLIER = {"ECONOMY": Decimal("0.85"), "STANDARD": Decimal("1.00"),
                      "EXPEDITED": Decimal("1.45")}
SERVICE_SPEED_MILES_PER_DAY = {"ECONOMY": 300, "STANDARD": 450, "EXPEDITED": 650}
H2_RATE_FACTOR = Decimal("0.96")     # Jul-Dec contracts were renegotiated 4% cheaper
