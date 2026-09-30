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

from opensumma.money import Money

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
        # Never a float in the database. SQLite stores whatever it is given, so each
        # value's own type is asked; PostgreSQL's is the column's.
        if connection.dialect.name == "sqlite":
            query = "SELECT DISTINCT typeof(amount) FROM amounts"
            expected = {"integer", "null"}
        else:
            query = "SELECT DISTINCT pg_typeof(amount)::text FROM amounts"
            expected = {"bigint"}
        assert set(connection.scalars(text(query))) == expected

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
