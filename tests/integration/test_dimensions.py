import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opensumma.kernel.dimensions import (
    add_dimension_value,
    create_dimension,
    dimensions,
    get_dimension,
    resolve_dimension_value,
)
from opensumma.kernel.errors import (
    DuplicateCodeError,
    InactiveDimensionValueError,
    UnknownDimensionError,
    UnknownDimensionValueError,
)
from opensumma.kernel.models import Dimension, DimensionValue


@pytest.fixture
def department(session: Session) -> Dimension:
    dimension = create_dimension(session, code="DEPARTMENT", name="Department")
    add_dimension_value(session, dimension, code="ENG", name="Engineering")
    add_dimension_value(session, dimension, code="SALES", name="Sales")
    return dimension


def test_values_belong_to_their_dimension_and_are_ordered_by_code(
    department: Dimension,
) -> None:
    assert [value.code for value in department.values] == ["ENG", "SALES"]
    assert all(value.dimension is department for value in department.values)


def test_a_value_resolves_within_its_dimension(
    session: Session, department: Dimension
) -> None:
    value = resolve_dimension_value(session, "DEPARTMENT", "ENG")

    assert value.name == "Engineering"
    assert value.dimension is department


def test_value_codes_are_unique_within_a_dimension(
    session: Session, department: Dimension
) -> None:
    with pytest.raises(DuplicateCodeError):
        add_dimension_value(session, department, code="ENG", name="Engineering again")


def test_the_same_value_code_may_be_used_by_another_dimension(
    session: Session, department: Dimension
) -> None:
    location = create_dimension(session, code="LOCATION", name="Location")
    add_dimension_value(session, location, code="ENG", name="Engineering Building")

    assert resolve_dimension_value(session, "LOCATION", "ENG").name == (
        "Engineering Building"
    )
    assert resolve_dimension_value(session, "DEPARTMENT", "ENG").name == "Engineering"


def test_dimension_codes_are_unique(session: Session, department: Dimension) -> None:
    with pytest.raises(DuplicateCodeError):
        create_dimension(session, code="DEPARTMENT", name="Another department")


def test_an_unknown_dimension_is_rejected(
    session: Session, department: Dimension
) -> None:
    with pytest.raises(UnknownDimensionError):
        resolve_dimension_value(session, "PROJECT", "ENG")


def test_a_value_unknown_to_its_dimension_is_rejected(
    session: Session, department: Dimension
) -> None:
    with pytest.raises(UnknownDimensionValueError):
        resolve_dimension_value(session, "DEPARTMENT", "LEGAL")


def test_a_retired_value_is_rejected(session: Session, department: Dimension) -> None:
    engineering = resolve_dimension_value(session, "DEPARTMENT", "ENG")
    engineering.is_active = False

    with pytest.raises(InactiveDimensionValueError):
        resolve_dimension_value(session, "DEPARTMENT", "ENG")


def test_unknown_dimension_codes_are_reported(session: Session) -> None:
    with pytest.raises(UnknownDimensionError):
        get_dimension(session, "PROJECT")


def test_dimensions_are_listed_by_code(session: Session, department: Dimension) -> None:
    create_dimension(session, code="CLASS", name="Class")

    assert [dimension.code for dimension in dimensions(session)] == [
        "CLASS",
        "DEPARTMENT",
    ]


def test_the_database_rejects_a_duplicate_value_code_in_one_dimension(
    session: Session, department: Dimension
) -> None:
    session.flush()
    session.add(
        DimensionValue(dimension_id=department.id, code="ENG", name="Engineering again")
    )

    with pytest.raises(IntegrityError):
        session.flush()
