"""Dimension services.

Dimensions are analytical axes on journal lines, such as department or location.
``resolve_dimension_value`` is the gate journal lines will use: an unknown or
retired value is rejected rather than stored as free text.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensumma.kernel.errors import (
    DuplicateCodeError,
    InactiveDimensionValueError,
    UnknownDimensionError,
    UnknownDimensionValueError,
)
from opensumma.kernel.models import Dimension, DimensionValue


def create_dimension(session: Session, *, code: str, name: str) -> Dimension:
    """Add an analytical axis."""
    code = code.strip()
    if not code:
        raise ValueError("dimension code must not be empty")
    if find_dimension(session, code) is not None:
        raise DuplicateCodeError(f"dimension code {code!r} already exists")

    dimension = Dimension(code=code, name=name)
    session.add(dimension)
    return dimension


def add_dimension_value(
    session: Session,
    dimension: Dimension,
    *,
    code: str,
    name: str,
    is_active: bool = True,
) -> DimensionValue:
    """Add an allowed value to ``dimension``.

    Codes are unique within a dimension but not across them, so DEPARTMENT and
    LOCATION may both have a value coded ``HQ``.
    """
    code = code.strip()
    if not code:
        raise ValueError("dimension value code must not be empty")
    if any(value.code == code for value in dimension.values):
        raise DuplicateCodeError(
            f"dimension {dimension.code} already has a value coded {code!r}"
        )

    value = DimensionValue(
        dimension=dimension, code=code, name=name, is_active=is_active
    )
    session.add(value)
    return value


def find_dimension(session: Session, code: str) -> Dimension | None:
    """Return the dimension with ``code``, or ``None``."""
    return session.scalars(
        select(Dimension).where(Dimension.code == code)
    ).one_or_none()


def get_dimension(session: Session, code: str) -> Dimension:
    """Return the dimension with ``code``, or raise ``UnknownDimensionError``."""
    dimension = find_dimension(session, code)
    if dimension is None:
        raise UnknownDimensionError(f"no dimension with code {code!r}")
    return dimension


def dimensions(session: Session) -> list[Dimension]:
    """Every dimension, ordered by code."""
    return list(session.scalars(select(Dimension).order_by(Dimension.code)))


def get_dimension_value(
    session: Session, dimension_code: str, value_code: str
) -> DimensionValue:
    """Return value ``value_code`` of dimension ``dimension_code``, active or not.

    Raises if the dimension is unknown or the value is unknown within it.
    """
    dimension = get_dimension(session, dimension_code)
    value = next(
        (candidate for candidate in dimension.values if candidate.code == value_code),
        None,
    )
    if value is None:
        raise UnknownDimensionValueError(
            f"dimension {dimension_code} has no value coded {value_code!r}"
        )
    return value


def resolve_dimension_value(
    session: Session, dimension_code: str, value_code: str
) -> DimensionValue:
    """Return the usable value ``value_code`` of dimension ``dimension_code``.

    Raises if the dimension is unknown, the value is unknown within it, or the
    value has been retired.
    """
    value = get_dimension_value(session, dimension_code, value_code)
    if not value.is_active:
        raise InactiveDimensionValueError(
            f"value {value_code!r} of dimension {dimension_code} is inactive"
        )
    return value
