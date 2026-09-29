"""Business data: the flexible JSON context an Accounting Object carries.

Accounting truth stays relational: accounts, journal lines, periods, and the ledger.
Business data is context, such as an invoice number, a vendor's reference, or the
amounts printed on a bill. It is stored as JSON so each kind of object can carry
what it needs, and it never reaches the ledger except through a journal entry.

Only values that JSON stores and returns exactly are accepted: objects with string
keys, arrays, strings, 64-bit integers, booleans, and null. Floats are refused,
because a JSON number with a fraction comes back as a binary float, which must
never stand in for an accounting amount. Amounts are written as strings, such as
``"1200.00"``, and read with ``Decimal`` where they are used. ``Decimal`` itself is
refused too: JSON cannot hold it, and converting it silently would change its type.
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Dialect
from sqlalchemy.types import TypeDecorator

# The range a signed 64-bit integer covers, which every backend's JSON handles
# exactly. Larger integers may come back from database JSON functions as floats.
MIN_INTEGER = -(2**63)
MAX_INTEGER = 2**63 - 1


def ensure_business_data(value: object) -> dict[str, Any]:
    """Return a copy of ``value`` if it is valid business data, or raise.

    Business data is a JSON object. The copy is detached from ``value``, so later
    changes to the caller's dictionary cannot alter what was validated.
    """
    if not isinstance(value, Mapping):
        raise TypeError(
            f"business data must be a JSON object (a dict), not {type(value).__name__}"
        )
    copied: dict[str, Any] = _copy(value, "data")
    return copied


def _copy(value: object, path: str) -> Any:
    # bool before int: a bool is an int in Python, but JSON keeps them apart.
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        if not MIN_INTEGER <= value <= MAX_INTEGER:
            raise ValueError(f"{path} is {value}, which does not fit in 64 bits")
        return value
    if isinstance(value, float | Decimal):
        raise TypeError(
            f"{path} is a {type(value).__name__}; write amounts as strings, "
            "such as '1200.00'"
        )
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} has a key {key!r}; JSON keys are strings")
            copied[key] = _copy(item, f"{path}.{key}")
        return copied
    if isinstance(value, list):
        return [_copy(item, f"{path}[{index}]") for index, item in enumerate(value)]
    raise TypeError(
        f"{path} is a {type(value).__name__}, which JSON cannot store exactly"
    )


class BusinessData(TypeDecorator[dict[str, Any]]):
    """A JSON object column that refuses anything JSON cannot store exactly.

    The check runs on every write that goes through SQLAlchemy, bulk statements
    included, so no path can put a float into business data.
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(
        self, value: dict[str, Any] | None, dialect: Dialect
    ) -> dict[str, Any]:
        return ensure_business_data(value)
