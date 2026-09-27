"""Property-based tests of the ledger's invariants.

Hypothesis generates many journal entries, balanced and not, and for every one:

- an entry posts if and only if it has at least two lines and balances;
- total debits in the ledger equal total credits, and so does every entry in it;
- an entry and its reversal net every account to zero.

Ledger totals are read as raw integer cents with SQL, independently of the
kernel's own arithmetic. Each example runs in a transaction that is rolled back,
so examples never see one another's entries.
"""

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from opensumma.db import Base, create_engine
from opensumma.kernel import (
    DEFAULT_CHART_OF_ACCOUNTS,
    JournalEntry,
    JournalEntryError,
    LineInput,
    create_calendar_year_periods,
    create_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
    seed_chart_of_accounts,
)

MARCH = date(2026, 3, 15)
APRIL = date(2026, 4, 1)

_PARENTS = {spec.parent for spec in DEFAULT_CHART_OF_ACCOUNTS}
LEAVES = sorted(
    spec.code for spec in DEFAULT_CHART_OF_ACCOUNTS if spec.code not in _PARENTS
)

Line = tuple[str, str, int]  # (account code, "debit" or "credit", amount in cents)

accounts = st.sampled_from(LEAVES)
cents = st.integers(min_value=1, max_value=10**9)
lines = st.tuples(accounts, st.sampled_from(["debit", "credit"]), cents)


@st.composite
def balanced_entries(draw: st.DrawFn) -> list[Line]:
    """At least one debit and one credit, topped up on one side to balance."""
    entry: list[Line] = [
        *(
            (account, "debit", amount)
            for account, amount in draw(
                st.lists(st.tuples(accounts, cents), min_size=1, max_size=4)
            )
        ),
        *(
            (account, "credit", amount)
            for account, amount in draw(
                st.lists(st.tuples(accounts, cents), min_size=1, max_size=4)
            )
        ),
    ]
    difference = _net(entry)
    if difference:
        side = "credit" if difference > 0 else "debit"
        entry.append((draw(accounts), side, abs(difference)))
    return draw(st.permutations(entry))


any_entries = st.one_of(balanced_entries(), st.lists(lines, max_size=6))


def _net(entry: list[Line]) -> int:
    return sum(amount if side == "debit" else -amount for _, side, amount in entry)


def _record(session: Session, entry: list[Line]) -> JournalEntry:
    return create_journal_entry(
        session,
        entry_date=MARCH,
        description="Generated",
        lines=[_line_input(*line) for line in entry],
    )


def _line_input(account: str, side: str, amount: int) -> LineInput:
    value = Decimal(amount).scaleb(-2)
    if side == "debit":
        return LineInput(account, debit=value)
    return LineInput(account, credit=value)


def _ledger_totals(session: Session) -> tuple[int, int]:
    debits, credits = session.execute(
        text(
            "SELECT COALESCE(SUM(l.debit), 0), COALESCE(SUM(l.credit), 0) "
            "FROM journal_line l JOIN journal_entry e ON e.id = l.journal_entry_id "
            "WHERE e.status IN ('POSTED', 'REVERSED')"
        )
    ).one()
    return int(debits), int(credits)


def _unbalanced_ledger_entries(session: Session) -> list[int]:
    return list(
        session.scalars(
            text(
                "SELECT e.id FROM journal_entry e "
                "JOIN journal_line l ON l.journal_entry_id = e.id "
                "WHERE e.status IN ('POSTED', 'REVERSED') "
                "GROUP BY e.id HAVING SUM(l.debit) <> SUM(l.credit)"
            )
        )
    )


def _net_by_account(session: Session) -> Counter[str]:
    rows = session.execute(
        text(
            "SELECT a.code, SUM(l.debit) - SUM(l.credit) "
            "FROM journal_line l "
            "JOIN journal_entry e ON e.id = l.journal_entry_id "
            "JOIN account a ON a.id = l.account_id "
            "WHERE e.status IN ('POSTED', 'REVERSED') GROUP BY a.code"
        )
    )
    return Counter({code: int(net) for code, net in rows if net})


@pytest.fixture(scope="module")
def ledger(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    path: Path = tmp_path_factory.mktemp("ledger") / "ledger.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        create_calendar_year_periods(session, 2026)
        session.commit()
    yield engine
    engine.dispose()


@contextmanager
def _scratch_books(engine: Engine) -> Iterator[Session]:
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(connection) as session:
            assert _ledger_totals(session) == (0, 0), "examples must not share state"
            yield session
        transaction.rollback()


@settings(max_examples=75, deadline=None)
@given(entries=st.lists(any_entries, min_size=1, max_size=5))
def test_an_entry_posts_exactly_when_it_balances_and_the_ledger_always_balances(
    ledger: Engine, entries: list[list[Line]]
) -> None:
    with _scratch_books(ledger) as session:
        for entry in entries:
            journal_entry = _record(session, entry)
            if len(entry) >= 2 and _net(entry) == 0:
                post_journal_entry(session, journal_entry)
            else:
                with pytest.raises(JournalEntryError):
                    post_journal_entry(session, journal_entry)
            session.flush()

            debits, credits = _ledger_totals(session)
            assert debits == credits
            assert _unbalanced_ledger_entries(session) == []


@settings(max_examples=50, deadline=None)
@given(entries=st.lists(balanced_entries(), min_size=1, max_size=4), data=st.data())
def test_an_entry_and_its_reversal_net_every_account_to_zero(
    ledger: Engine, entries: list[list[Line]], data: st.DataObject
) -> None:
    with _scratch_books(ledger) as session:
        posted = []
        for entry in entries:
            journal_entry = _record(session, entry)
            post_journal_entry(session, journal_entry)
            posted.append(journal_entry)
        reversed_ = data.draw(st.sets(st.integers(0, len(posted) - 1)))
        for index in sorted(reversed_):
            reverse_journal_entry(session, posted[index], entry_date=APRIL)
        session.flush()

        expected: Counter[str] = Counter()
        for index, entry in enumerate(entries):
            if index not in reversed_:
                for account, side, amount in entry:
                    expected[account] += amount if side == "debit" else -amount
        assert _net_by_account(session) == Counter(
            {account: net for account, net in expected.items() if net}
        )
        debits, credits = _ledger_totals(session)
        assert debits == credits
