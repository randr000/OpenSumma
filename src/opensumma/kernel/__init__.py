"""The accounting kernel: deterministic accounting master data and rules.

Phase 1 covers the chart of accounts, accounting periods, and dimensions.
Journal entries, posting, and the immutable ledger arrive in Phases 2 and 3.

The kernel is usable directly from Python and depends on nothing above it. Every
name a caller needs is re-exported here, so importing this package is also what
registers the persistence models on ``Base.metadata``.
"""

from opensumma.kernel.accounts import (
    activate_account,
    assert_postable,
    chart_of_accounts,
    create_account,
    deactivate_account,
    descendants,
    find_account,
    get_account,
    postable_accounts,
)
from opensumma.kernel.dimensions import (
    add_dimension_value,
    create_dimension,
    dimensions,
    find_dimension,
    get_dimension,
    resolve_dimension_value,
)
from opensumma.kernel.enums import AccountType, NormalBalance, PeriodStatus
from opensumma.kernel.errors import (
    AccountTypeMismatchError,
    ClosedPeriodError,
    DuplicateCodeError,
    InactiveAccountError,
    InactiveDimensionValueError,
    InvalidPeriodRangeError,
    KernelError,
    NotPostableError,
    OverlappingPeriodError,
    UnknownAccountError,
    UnknownDimensionError,
    UnknownDimensionValueError,
    UnknownPeriodError,
)
from opensumma.kernel.models import Account, AccountingPeriod, Dimension, DimensionValue
from opensumma.kernel.periods import (
    assert_period_open,
    close_period,
    create_calendar_year_periods,
    create_period,
    find_period,
    get_period,
    period_for_date,
    periods,
    reopen_period,
)
from opensumma.kernel.seed import (
    DEFAULT_CHART_OF_ACCOUNTS,
    DEFAULT_DIMENSIONS,
    AccountSpec,
    DimensionSpec,
    seed_chart_of_accounts,
    seed_dimensions,
)

__all__ = [
    "DEFAULT_CHART_OF_ACCOUNTS",
    "DEFAULT_DIMENSIONS",
    "Account",
    "AccountSpec",
    "AccountType",
    "AccountTypeMismatchError",
    "AccountingPeriod",
    "ClosedPeriodError",
    "Dimension",
    "DimensionSpec",
    "DimensionValue",
    "DuplicateCodeError",
    "InactiveAccountError",
    "InactiveDimensionValueError",
    "InvalidPeriodRangeError",
    "KernelError",
    "NormalBalance",
    "NotPostableError",
    "OverlappingPeriodError",
    "PeriodStatus",
    "UnknownAccountError",
    "UnknownDimensionError",
    "UnknownDimensionValueError",
    "UnknownPeriodError",
    "activate_account",
    "add_dimension_value",
    "assert_period_open",
    "assert_postable",
    "chart_of_accounts",
    "close_period",
    "create_account",
    "create_calendar_year_periods",
    "create_dimension",
    "create_period",
    "deactivate_account",
    "descendants",
    "dimensions",
    "find_account",
    "find_dimension",
    "find_period",
    "get_account",
    "get_dimension",
    "get_period",
    "period_for_date",
    "periods",
    "postable_accounts",
    "reopen_period",
    "resolve_dimension_value",
    "seed_chart_of_accounts",
    "seed_dimensions",
]
