"""Phase 1 acceptance: chart of accounts, accounting periods, and dimensions.

Everything here goes through the public kernel API against a database created by
the migrations, the way a caller outside the project would use it.
"""

from collections.abc import Iterator
from datetime import date

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from opensumma.db import create_engine, init_db
from opensumma.kernel import (
    Account,
    AccountType,
    ClosedPeriodError,
    InactiveAccountError,
    NormalBalance,
    NotPostableError,
    UnknownDimensionValueError,
    UnknownPeriodError,
    assert_period_open,
    assert_postable,
    chart_of_accounts,
    close_period,
    create_calendar_year_periods,
    deactivate_account,
    get_account,
    get_period,
    period_for_date,
    postable_accounts,
    resolve_dimension_value,
    seed_chart_of_accounts,
    seed_dimensions,
)


def _ancestor_codes(account: Account) -> list[str]:
    """The codes of ``account``'s parents, nearest first, up to the root."""
    codes = []
    current = account.parent
    while current is not None:
        codes.append(current.code)
        current = current.parent
    return codes


@pytest.fixture
def seeded(database_url: str, engine: Engine) -> Iterator[Session]:
    """A migrated database holding the default chart, dimensions, and 2026."""
    init_db(database_url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        seed_dimensions(session)
        create_calendar_year_periods(session, 2026)
        session.commit()
        yield session


def test_the_chart_of_accounts_is_a_typed_hierarchy(seeded: Session) -> None:
    accounts = chart_of_accounts(seeded)
    assert accounts

    cash = get_account(seeded, "1111")
    assert cash.account_type is AccountType.ASSET
    assert cash.normal_balance is NormalBalance.DEBIT
    assert _ancestor_codes(cash) == ["1110", "1100", "1000"]
    assert {account.account_type for account in accounts} == set(AccountType)


def test_normal_balances_follow_the_type_unless_the_account_is_contra(
    seeded: Session,
) -> None:
    for account in chart_of_accounts(seeded):
        expected = account.account_type.normal_balance
        if account.is_contra:
            assert account.normal_balance is not expected
        else:
            assert account.normal_balance is expected

    assert get_account(seeded, "1590").normal_balance is NormalBalance.CREDIT


def test_only_active_leaf_accounts_accept_postings(seeded: Session) -> None:
    leaf = get_account(seeded, "6100")
    assert_postable(leaf)

    with pytest.raises(NotPostableError):
        assert_postable(get_account(seeded, "6000"))

    deactivate_account(leaf)
    with pytest.raises(InactiveAccountError):
        assert_postable(leaf)
    assert leaf not in postable_accounts(seeded)


def test_an_accounting_date_resolves_to_exactly_one_open_period(
    seeded: Session,
) -> None:
    period = period_for_date(seeded, date(2026, 3, 15))

    assert period.code == "2026-03"
    assert_period_open(period)

    with pytest.raises(UnknownPeriodError):
        period_for_date(seeded, date(2027, 1, 1))


def test_a_closed_period_stops_accepting_postings(seeded: Session) -> None:
    january = get_period(seeded, "2026-01")
    close_period(january)

    with pytest.raises(ClosedPeriodError):
        assert_period_open(january)

    assert_period_open(get_period(seeded, "2026-02"))


def test_dimension_values_are_validated_against_their_dimension(
    seeded: Session,
) -> None:
    assert resolve_dimension_value(seeded, "DEPARTMENT", "ENG").name == "Engineering"

    with pytest.raises(UnknownDimensionValueError):
        resolve_dimension_value(seeded, "DEPARTMENT", "NOT_A_DEPARTMENT")


def test_master_data_survives_a_reconnection(
    database_url: str, seeded: Session
) -> None:
    """Whatever the kernel recorded is in the database, not just in the session."""
    seeded.close()

    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            assert get_account(session, "1111").name == "Operating Bank Account"
            assert get_period(session, "2026-12").end_date == date(2026, 12, 31)
            assert resolve_dimension_value(session, "LOCATION", "HQ").name == (
                "Headquarters"
            )
    finally:
        engine.dispose()
