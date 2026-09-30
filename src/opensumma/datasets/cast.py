"""Who a generated company does business with, and what it sells.

The company makes fleet-monitoring hardware and sells services around it. Its
vendors are a fixed cast, the default counterparties and five more, each with the
account its bills are posted to. Its customers are the default five and, for a
larger company, more generated from word lists. Every name is fictional.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from opensumma.datasets.rng import Rng
from opensumma.objects import DEFAULT_COUNTERPARTIES, CounterpartyKind


@dataclass(frozen=True)
class Vendor:
    """A vendor and how the company records its bills."""

    code: str
    name: str
    category: str
    account: str
    invoice_prefix: str
    departments: tuple[str, ...]
    low: int  # the usual range of a one-off bill, in cents
    high: int


def _default_name(code: str) -> str:
    return next(spec.name for spec in DEFAULT_COUNTERPARTIES if spec.code == code)


# Recurring vendors, whose bills come on a schedule.
RENT = Vendor(
    "V-HARBOR", _default_name("V-HARBOR"), "rent", "6200", "HPP", ("GA",), 0, 0
)
HOSTING = Vendor(
    "V-STRATUS",
    _default_name("V-STRATUS"),
    "hosting",
    "5200",
    "STR",
    ("ENG",),
    30_000,
    400_000,
)
SOFTWARE = Vendor(
    "V-BRIGHT",
    _default_name("V-BRIGHT"),
    "software_subscription",
    "6100",
    "BRL",
    ("ENG", "MKT", "SALES"),
    20_000,
    300_000,
)
LEGAL = Vendor(
    "V-KELLER",
    _default_name("V-KELLER"),
    "legal",
    "6400",
    "KM",
    ("GA",),
    150_000,
    900_000,
)
INSURANCE = Vendor(
    "V-SUMMIT", _default_name("V-SUMMIT"), "insurance", "1130", "SMI", ("GA",), 0, 0
)
OFFICE = Vendor(
    "V-PAPER",
    _default_name("V-PAPER"),
    "office_supplies",
    "6700",
    "PTO",
    ("ENG", "GA", "MKT", "SALES"),
    4_000,
    90_000,
)
SUPPLIER = Vendor(
    "V-FORGE", "Forgeline Components", "inventory", "1140", "FLC", (), 0, 0
)
HARDWARE = Vendor(
    "V-CIRCUIT",
    "Circuit Yard Hardware",
    "equipment",
    "1510",
    "CYH",
    (),
    120_000,
    950_000,
)
TRAVEL = Vendor(
    "V-TRAILHEAD",
    "Trailhead Travel Partners",
    "travel",
    "6500",
    "TTP",
    ("MKT", "SALES"),
    40_000,
    450_000,
)
CONSULTING = Vendor(
    "V-NORTHGATE",
    "Northgate Advisory Group",
    "consulting",
    "6400",
    "NAG",
    ("ENG", "GA"),
    200_000,
    1_200_000,
)
DESIGN = Vendor(
    "V-SIGNAL",
    "Signal Street Studio",
    "design",
    "6400",
    "SSS",
    ("MKT",),
    80_000,
    600_000,
)

VENDORS: Sequence[Vendor] = (
    RENT,
    HOSTING,
    SOFTWARE,
    LEGAL,
    INSURANCE,
    OFFICE,
    SUPPLIER,
    HARDWARE,
    TRAVEL,
    CONSULTING,
    DESIGN,
)
# Vendors whose one-off bills arrive at random through the year, and how often.
OCCASIONAL_VENDORS: Sequence[Vendor] = (
    OFFICE,
    SOFTWARE,
    HOSTING,
    LEGAL,
    TRAVEL,
    CONSULTING,
    DESIGN,
)
OCCASIONAL_WEIGHTS: Sequence[int] = (30, 18, 12, 5, 20, 8, 7)

# Vendors the default cast does not include, added to every generated company.
NEW_VENDORS: Sequence[Vendor] = (SUPPLIER, HARDWARE, TRAVEL, CONSULTING, DESIGN)


@dataclass(frozen=True)
class Customer:
    """A customer and how it buys and pays."""

    code: str
    name: str
    location: str
    product_share: float  # how often an invoice is for hardware rather than services
    size: int  # 1 small, 2 medium, 3 large
    days_late: int  # how many days after the due date it usually pays


_FIRST_WORDS = (
    "Alder", "Amber", "Anchor", "Aspen", "Atlas", "Beacon", "Birch", "Bramble",
    "Canyon", "Cascade", "Cobalt", "Copper", "Crescent", "Delta", "Driftwood",
    "Ember", "Falcon", "Fern", "Fjord", "Garnet", "Granite", "Hawthorn", "Heron",
    "Indigo", "Juniper", "Keystone", "Lantern", "Laurel", "Linden", "Magnolia",
    "Maple", "Meridian", "Mesa", "Nimbus", "Northstar", "Oakmont", "Onyx", "Osprey",
    "Pioneer", "Quarry", "Redwood", "Ridgeline", "Riverbend", "Saffron", "Sagebrush",
    "Sequoia", "Sierra", "Silverline", "Spruce", "Tamarack", "Timberline", "Tundra",
    "Upland", "Wavecrest", "Willow", "Wren", "Yarrow", "Zephyr", "Kestrel", "Lumen",
)  # fmt: skip
_SECOND_WORDS = (
    "Logistics", "Health", "Foods", "Energy", "Labs", "Schools", "Manufacturing",
    "Retail", "Hospitality", "Analytics", "Construction", "Dental", "Freight",
    "Media", "Veterinary", "Engineering", "Clinics", "Outfitters", "Architects",
    "Transit",
)  # fmt: skip
_DEFAULT_CUSTOMERS = tuple(
    spec for spec in DEFAULT_COUNTERPARTIES if spec.kind is CounterpartyKind.CUSTOMER
)
MAX_CUSTOMERS = len(_DEFAULT_CUSTOMERS) + len(_FIRST_WORDS)


def customers(rng: Rng, count: int) -> list[Customer]:
    """The default customers and ``count`` minus five more, each with how it buys."""
    if not len(_DEFAULT_CUSTOMERS) <= count <= MAX_CUSTOMERS:
        raise ValueError(
            f"a company has {len(_DEFAULT_CUSTOMERS)} to {MAX_CUSTOMERS} customers"
        )
    names = [(spec.code, spec.name) for spec in _DEFAULT_CUSTOMERS]
    for first in rng.sample(_FIRST_WORDS, count - len(names)):
        names.append((f"C-{first.upper()}", f"{first} {rng.choice(_SECOND_WORDS)}"))
    return [
        Customer(
            code=code,
            name=name,
            location=rng.weighted(("EAST", "WEST", "HQ"), (4, 4, 2)),
            product_share=rng.choice((0.2, 0.4, 0.6, 0.8)),
            size=rng.weighted((1, 2, 3), (5, 4, 1)),
            days_late=rng.weighted((0, 5, 15, 30), (5, 3, 2, 1)),
        )
        for code, name in names
    ]


@dataclass(frozen=True)
class Product:
    name: str
    price: int  # cents per unit
    cost: int


PRODUCTS: Sequence[Product] = (
    Product("Fleet sensor kit", 24_900, 11_850),
    Product("Vehicle gateway unit", 115_000, 54_000),
    Product("Rugged field tablet", 68_900, 35_500),
    Product("Harness and cable bundle", 8_900, 3_100),
)


@dataclass(frozen=True)
class Service:
    name: str
    rate: int  # cents per hour


SERVICES: Sequence[Service] = (
    Service("Implementation services", 17_500),
    Service("Fleet analytics consulting", 21_000),
    Service("Driver training", 12_500),
)


@dataclass(frozen=True)
class Merchant:
    """Where employees spend on the company card."""

    name: str
    category: str
    account: str
    low: int  # cents
    high: int


MERCHANTS: Sequence[Merchant] = (
    Merchant("Skyway Airlines", "airfare", "6500", 18_000, 95_000),
    Merchant("Harborview Hotel", "lodging", "6500", 14_000, 72_000),
    Merchant("Copperleaf Bistro", "meals", "6500", 1_800, 26_000),
    Merchant("Metro Rideshare", "ground_transport", "6500", 900, 8_500),
    Merchant("Quill & Ink Stationers", "office_supplies", "6700", 1_500, 21_000),
    Merchant("Parcel Express", "shipping", "6700", 1_200, 16_000),
)
MERCHANT_WEIGHTS: Sequence[int] = (12, 12, 30, 22, 14, 10)
