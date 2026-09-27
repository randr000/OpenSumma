"""A default chart of accounts and dimension set.

This is reference data, not accounting logic: it is created through the same
services as any other account, so the seeded chart obeys exactly the same rules.
The dataset generator and the benchmark will build on it, which is why the specs
are public.

Parents precede their children so that a single pass can resolve them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from opensumma.kernel.accounts import create_account
from opensumma.kernel.dimensions import add_dimension_value, create_dimension
from opensumma.kernel.enums import AccountType, NormalBalance
from opensumma.kernel.models import Account, Dimension


@dataclass(frozen=True)
class AccountSpec:
    """One account in a chart-of-accounts template."""

    code: str
    name: str
    account_type: AccountType
    parent: str | None = None
    normal_balance: NormalBalance | None = None


@dataclass(frozen=True)
class DimensionSpec:
    """One analytical axis and its allowed values, as ``(code, name)`` pairs."""

    code: str
    name: str
    values: Sequence[tuple[str, str]] = field(default_factory=tuple)


DEFAULT_CHART_OF_ACCOUNTS: Sequence[AccountSpec] = (
    AccountSpec("1000", "Assets", AccountType.ASSET),
    AccountSpec("1100", "Current Assets", AccountType.ASSET, "1000"),
    AccountSpec("1110", "Cash and Cash Equivalents", AccountType.ASSET, "1100"),
    AccountSpec("1111", "Operating Bank Account", AccountType.ASSET, "1110"),
    AccountSpec("1112", "Payroll Bank Account", AccountType.ASSET, "1110"),
    AccountSpec("1120", "Accounts Receivable", AccountType.ASSET, "1100"),
    AccountSpec("1130", "Prepaid Expenses", AccountType.ASSET, "1100"),
    AccountSpec("1140", "Inventory", AccountType.ASSET, "1100"),
    AccountSpec("1500", "Fixed Assets", AccountType.ASSET, "1000"),
    AccountSpec("1510", "Equipment", AccountType.ASSET, "1500"),
    AccountSpec(
        "1590",
        "Accumulated Depreciation on Equipment",
        AccountType.ASSET,
        "1500",
        NormalBalance.CREDIT,
    ),
    AccountSpec("2000", "Liabilities", AccountType.LIABILITY),
    AccountSpec("2100", "Current Liabilities", AccountType.LIABILITY, "2000"),
    AccountSpec("2110", "Accounts Payable", AccountType.LIABILITY, "2100"),
    AccountSpec("2120", "Accrued Liabilities", AccountType.LIABILITY, "2100"),
    AccountSpec("2130", "Payroll Liabilities", AccountType.LIABILITY, "2100"),
    AccountSpec("2140", "Sales Tax Payable", AccountType.LIABILITY, "2100"),
    AccountSpec("2500", "Long-Term Liabilities", AccountType.LIABILITY, "2000"),
    AccountSpec("2510", "Notes Payable", AccountType.LIABILITY, "2500"),
    AccountSpec("3000", "Equity", AccountType.EQUITY),
    AccountSpec("3100", "Common Stock", AccountType.EQUITY, "3000"),
    AccountSpec("3200", "Additional Paid-In Capital", AccountType.EQUITY, "3000"),
    AccountSpec("3900", "Retained Earnings", AccountType.EQUITY, "3000"),
    AccountSpec("4000", "Revenue", AccountType.REVENUE),
    AccountSpec("4100", "Product Revenue", AccountType.REVENUE, "4000"),
    AccountSpec("4200", "Service Revenue", AccountType.REVENUE, "4000"),
    AccountSpec(
        "4900",
        "Sales Returns and Allowances",
        AccountType.REVENUE,
        "4000",
        NormalBalance.DEBIT,
    ),
    AccountSpec("5000", "Cost of Revenue", AccountType.EXPENSE),
    AccountSpec("5100", "Cost of Goods Sold", AccountType.EXPENSE, "5000"),
    AccountSpec("5200", "Hosting and Infrastructure", AccountType.EXPENSE, "5000"),
    AccountSpec("6000", "Operating Expenses", AccountType.EXPENSE),
    AccountSpec("6100", "Software Subscriptions", AccountType.EXPENSE, "6000"),
    AccountSpec("6200", "Rent Expense", AccountType.EXPENSE, "6000"),
    AccountSpec("6300", "Salaries and Wages", AccountType.EXPENSE, "6000"),
    AccountSpec("6310", "Payroll Taxes", AccountType.EXPENSE, "6000"),
    AccountSpec("6400", "Professional Fees", AccountType.EXPENSE, "6000"),
    AccountSpec("6500", "Travel and Entertainment", AccountType.EXPENSE, "6000"),
    AccountSpec("6600", "Depreciation Expense", AccountType.EXPENSE, "6000"),
    AccountSpec("6700", "Office Supplies", AccountType.EXPENSE, "6000"),
    AccountSpec("6800", "Insurance Expense", AccountType.EXPENSE, "6000"),
    AccountSpec("6900", "Bank Fees", AccountType.EXPENSE, "6000"),
)

DEFAULT_DIMENSIONS: Sequence[DimensionSpec] = (
    DimensionSpec(
        "DEPARTMENT",
        "Department",
        (
            ("ENG", "Engineering"),
            ("GA", "General and Administrative"),
            ("MKT", "Marketing"),
            ("SALES", "Sales"),
        ),
    ),
    DimensionSpec(
        "LOCATION",
        "Location",
        (
            ("EAST", "East Region"),
            ("HQ", "Headquarters"),
            ("WEST", "West Region"),
        ),
    ),
    DimensionSpec(
        "CLASS",
        "Class",
        (
            ("PRODUCT", "Product"),
            ("SERVICES", "Services"),
        ),
    ),
)


def seed_chart_of_accounts(session: Session) -> dict[str, Account]:
    """Create the default chart of accounts, keyed by account code."""
    accounts: dict[str, Account] = {}
    for spec in DEFAULT_CHART_OF_ACCOUNTS:
        accounts[spec.code] = create_account(
            session,
            code=spec.code,
            name=spec.name,
            account_type=spec.account_type,
            parent=accounts[spec.parent] if spec.parent is not None else None,
            normal_balance=spec.normal_balance,
        )
    return accounts


def seed_dimensions(session: Session) -> dict[str, Dimension]:
    """Create the default dimensions and their values, keyed by dimension code."""
    created: dict[str, Dimension] = {}
    for spec in DEFAULT_DIMENSIONS:
        dimension = create_dimension(session, code=spec.code, name=spec.name)
        for code, name in spec.values:
            add_dimension_value(session, dimension, code=code, name=name)
        created[spec.code] = dimension
    return created
