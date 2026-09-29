"""Property-based test: business data comes back from the database exactly as written.

Hypothesis generates arbitrary JSON objects made of what business data allows
(strings, 64-bit integers, booleans, null, arrays, and nested objects), writes each
to an accounting object, and reads it back from the database. Each example runs in
a transaction that is rolled back, so examples never see one another's objects.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from opensumma.db import Base, create_engine
from opensumma.objects import AccountingObject, create_accounting_object
from opensumma.objects.data import MAX_INTEGER, MIN_INTEGER

scalars = (
    st.none()
    | st.booleans()
    | st.integers(min_value=MIN_INTEGER, max_value=MAX_INTEGER)
    | st.text()
)
values = st.recursive(
    scalars,
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=20,
)
business_data = st.dictionaries(st.text(max_size=8), values, max_size=6)


@pytest.fixture(scope="module")
def database() -> Iterator[Engine]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@settings(max_examples=100, deadline=None)
@given(data=business_data)
def test_business_data_round_trips_exactly(
    database: Engine, data: dict[str, Any]
) -> None:
    with database.connect() as connection:
        transaction = connection.begin()
        with Session(connection) as session:
            obj = create_accounting_object(
                session,
                object_type="contract",
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
                source="generated",
                data=data,
            )
            session.flush()
            session.expire_all()

            stored = session.get(AccountingObject, obj.id)
            assert stored is not None
            assert stored.data == data
        transaction.rollback()
