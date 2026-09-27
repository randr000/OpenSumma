from decimal import Decimal

import pytest
from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    Table,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.exc import StatementError

from ledgerlab.money import Money

metadata = MetaData()
amounts = Table(
    "amounts",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("amount", Money, nullable=True),
)


@pytest.fixture
def amounts_engine(engine: Engine) -> Engine:
    metadata.create_all(engine)
    return engine


def test_amounts_round_trip_exactly(amounts_engine: Engine) -> None:
    values = [Decimal("0.10"), Decimal("-1234567.89"), Decimal("0.00"), None]
    with amounts_engine.begin() as connection:
        connection.execute(insert(amounts), [{"amount": v} for v in values])
        stored: list[Decimal | None] = list(
            connection.scalars(select(amounts.c.amount).order_by(amounts.c.id))
        )
        raw_types = connection.scalars(
            text("SELECT DISTINCT typeof(amount) FROM amounts")
        )
        assert set(raw_types) == {"integer", "null"}  # never a float in the database

    assert stored == values
    assert all(v.as_tuple().exponent == -2 for v in stored if v is not None)


def test_sql_sum_is_exact(amounts_engine: Engine) -> None:
    with amounts_engine.begin() as connection:
        connection.execute(
            insert(amounts), [{"amount": Decimal("0.10")}, {"amount": Decimal("0.20")}]
        )
        total = connection.scalar(select(func.sum(amounts.c.amount)))

    assert total == Decimal("0.30")


@pytest.mark.parametrize("value", [Decimal("0.125"), Decimal("NaN"), 0.1, 10])
def test_rejects_amounts_that_are_not_two_decimal_decimals(
    amounts_engine: Engine, value: object
) -> None:
    with pytest.raises(StatementError), amounts_engine.begin() as connection:
        connection.execute(insert(amounts).values(amount=value))
