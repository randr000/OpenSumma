"""Monetary amounts: ``Decimal`` in Python, exact integer cents in the database.

SQLite has no decimal type; a NUMERIC column stores 0.10 as a binary float, and
SUM(0.10, 0.20) returns 0.30000000000000004. Money is therefore stored as an
integer number of cents, which is exact on SQLite and PostgreSQL alike and
keeps SQL aggregates such as SUM() exact.

Rounding happens once, explicitly, where amounts enter the system
(``round_money``). The ``Money`` column never rounds: it rejects amounts with
more than two decimal places, so a value validated as balanced is exactly the
value recorded.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import BigInteger, Dialect
from sqlalchemy.types import TypeDecorator

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# The largest magnitude a signed 64-bit BIGINT column can hold, in cents.
MAX_CENTS = 2**63 - 1


def round_money(value: Decimal | int | str) -> Decimal:
    """Round an amount to two decimal places, halves away from zero."""
    if isinstance(value, float):
        raise TypeError("monetary amounts must not be floats; use Decimal or str")
    amount = Decimal(value)
    if not amount.is_finite():
        raise ValueError(f"monetary amount must be finite, got {amount}")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def ensure_money(value: object) -> Decimal:
    """Return ``value`` as an exact two-decimal amount, or raise.

    Never rounds. An amount with a fraction of a cent is rejected rather than
    adjusted, so the amount validated is exactly the amount recorded; callers that
    intend to round call ``round_money`` first.
    """
    if not isinstance(value, Decimal):
        raise TypeError(f"monetary amounts must be Decimal, got {type(value).__name__}")
    try:
        exact = value.quantize(CENT)
    except InvalidOperation:
        raise ValueError(f"{value} is not a representable monetary amount") from None
    if not value.is_finite() or value != exact:
        raise ValueError(
            f"{value} is not a two-decimal amount; round it with round_money() "
            "before it is validated and recorded"
        )
    if abs(exact.scaleb(2)) > MAX_CENTS:
        raise ValueError(f"{value} is too large to record")
    return exact


class Money(TypeDecorator[Decimal]):
    """A two-decimal monetary amount, stored as integer cents."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        return int(ensure_money(value).scaleb(2))

    def process_result_value(
        self, value: int | None, dialect: Dialect
    ) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value).scaleb(-2)
