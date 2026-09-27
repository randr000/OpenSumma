import pytest
from sqlalchemy import (
    Column,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    insert,
    text,
)
from sqlalchemy.exc import IntegrityError


def test_sqlite_connection_works(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


def test_sqlite_enforces_foreign_keys(engine: Engine) -> None:
    metadata = MetaData()
    Table("parent", metadata, Column("id", Integer, primary_key=True))
    child = Table(
        "child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parent.id"), nullable=False),
    )
    metadata.create_all(engine)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(insert(child).values(id=1, parent_id=999))
