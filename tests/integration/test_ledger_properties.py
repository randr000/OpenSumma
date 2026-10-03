"""Property-based tests of the ledger's invariants.

Hypothesis generates many journal entries, balanced and not, with signed amounts,
debits positive and credits negative, zero lines among them, and for every one:

- an entry posts if and only if it has at least two lines and balances;
- the amounts in the ledger sum to zero, so total debits equal total credits, and
  so do the amounts of every entry in it;
- an entry and its reversal net every account to zero;
- every report agrees with balances worked out independently from the entries
  that were posted, whatever else was drafted, voided, or reversed.

Ledger totals are read as raw integer cents with SQL, independently of the
kernel's own arithmetic. Each example runs in a transaction that is rolled back,
so examples never see one another's entries.
"""

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from opensumma.kernel import (
    DEFAULT_CHART_OF_ACCOUNTS,
    AccountType,
    JournalEntry,
    JournalEntryError,
    LineInput,
    balance_sheet,
    create_calendar_year_periods,
    create_journal_entry,
    income_statement,
    post_journal_entry,
    reverse_journal_entry,
    seed_chart_of_accounts,
    trial_balance,
    void_journal_entry,
)

MARCH = date(2026, 3, 15)
APRIL = date(2026, 4, 1)

_PARENTS = {spec.parent for spec in DEFAULT_CHART_OF_ACCOUNTS}
LEAVES = sorted(
    spec.code for spec in DEFAULT_CHART_OF_ACCOUNTS if spec.code not in _PARENTS
)

TYPE_OF = {spec.code: spec.account_type for spec in DEFAULT_CHART_OF_ACCOUNTS}

Line = tuple[str, int]  # (account code, signed amount in cents, debits positive)

accounts = st.sampled_from(LEAVES)
cents = st.one_of(st.integers(min_value=-(10**9), max_value=10**9), st.just(0))
lines = st.tuples(accounts, cents)


@st.composite
def balanced_entries(draw: st.DrawFn) -> list[Line]:
    """At least one line, and one more that brings the amounts' sum to zero."""
    entry = draw(st.lists(lines, min_size=1, max_size=7))
    entry.append((draw(accounts), -_net(entry)))
    return draw(st.permutations(entry))


any_entries = st.one_of(balanced_entries(), st.lists(lines, max_size=6))


def _net(entry: list[Line]) -> int:
    return sum(amount for _, amount in entry)


def _record(session: Session, entry: list[Line], on: date = MARCH) -> JournalEntry:
    return create_journal_entry(
        session,
        entry_date=on,
        description="Generated",
        lines=[_line_input(*line) for line in entry],
    )


def _line_input(account: str, amount: int) -> LineInput:
    return LineInput(account, Decimal(amount).scaleb(-2))


def _ledger_totals(session: Session) -> tuple[int, int]:
    """The ledger's debits and its credits, each added up as positive cents."""
    debits, credits = session.execute(
        text(
            "SELECT COALESCE(SUM(CASE WHEN l.amount > 0 THEN l.amount END), 0), "
            "COALESCE(SUM(CASE WHEN l.amount < 0 THEN -l.amount END), 0) "
            "FROM journal_line l JOIN journal_entry e ON e.id = l.journal_entry_id "
            "WHERE e.status IN ('POSTED', 'REVERSED')"
        )
    ).one()
    return int(debits), int(credits)


def _journal_lines(session: Session) -> int:
    return int(session.scalar(text("SELECT COUNT(*) FROM journal_line")) or 0)


def _unbalanced_ledger_entries(session: Session) -> list[int]:
    return list(
        session.scalars(
            text(
                "SELECT e.id FROM journal_entry e "
                "JOIN journal_line l ON l.journal_entry_id = e.id "
                "WHERE e.status IN ('POSTED', 'REVERSED') "
                "GROUP BY e.id HAVING SUM(l.amount) <> 0"
            )
        )
    )


def _net_by_account(session: Session) -> Counter[str]:
    rows = session.execute(
        text(
            "SELECT a.code, SUM(l.amount) "
            "FROM journal_line l "
            "JOIN journal_entry e ON e.id = l.journal_entry_id "
            "JOIN account a ON a.id = l.account_id "
            "WHERE e.status IN ('POSTED', 'REVERSED') GROUP BY a.code"
        )
    )
    return Counter({code: int(net) for code, net in rows if net})


@pytest.fixture(scope="module")
def ledger(module_engine: Engine) -> Engine:
    with Session(module_engine) as session:
        seed_chart_of_accounts(session)
        create_calendar_year_periods(session, 2026)
        session.commit()
    return module_engine


@contextmanager
def _scratch_books(engine: Engine) -> Iterator[Session]:
    with engine.connect() as connection:
        transaction = connection.begin()
        with Session(connection) as session:
            assert _journal_lines(session) == 0, "examples must not share state"
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
                for account, amount in entry:
                    expected[account] += amount
        assert _net_by_account(session) == Counter(
            {account: net for account, net in expected.items() if net}
        )
        debits, credits = _ledger_totals(session)
        assert debits == credits


# Posting is the likeliest fate, so most generated entries reach the ledger.
FATES = ["posted", "posted", "draft", "voided", "reversed"]
dated_entries = st.tuples(
    st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31)),
    st.sampled_from(FATES),
    balanced_entries(),
)


@settings(max_examples=50, deadline=None)
@given(entries=st.lists(dated_entries, max_size=8))
def test_every_report_agrees_with_what_was_posted(
    ledger: Engine, entries: list[tuple[date, str, list[Line]]]
) -> None:
    """Reports are checked against an oracle built from the generated entries alone.

    Only entries left posted count: drafts and voided entries never reach the
    ledger, and a reversed entry is cancelled by its reversal.
    """
    with _scratch_books(ledger) as session:
        net_debit: Counter[str] = Counter()  # per account, in cents
        for on, fate, entry in entries:
            journal_entry = _record(session, entry, on)
            if fate == "voided":
                void_journal_entry(journal_entry)
            if fate in ("posted", "reversed"):
                post_journal_entry(session, journal_entry)
            if fate == "reversed":
                reverse_journal_entry(session, journal_entry, entry_date=on)
            if fate == "posted":
                for account, amount in entry:
                    net_debit[account] += amount

        year_end = date(2026, 12, 31)
        trial = trial_balance(session, as_of=year_end)
        assert trial.is_balanced
        assert {line.account_code: _cents(line.balance) for line in trial.lines} == {
            account: net for account, net in net_debit.items() if net
        }

        revenue = sum(
            -net
            for account, net in net_debit.items()
            if TYPE_OF[account] is AccountType.REVENUE
        )
        expenses = sum(
            net
            for account, net in net_debit.items()
            if TYPE_OF[account] is AccountType.EXPENSE
        )
        year = income_statement(session, start=date(2026, 1, 1), end=year_end)
        assert _cents(year.net_income) == revenue - expenses

        months = [
            income_statement(session, start=start, end=end)
            for start, end in _months_of_2026()
        ]
        assert sum(month.net_income for month in months) == year.net_income

        sheet = balance_sheet(session, as_of=year_end)
        assert sheet.is_balanced
        assert sheet.unclosed_net_income == year.net_income


def _cents(amount: Decimal) -> int:
    return int(amount.scaleb(2))


def _months_of_2026() -> list[tuple[date, date]]:
    starts = [date(2026, month, 1) for month in range(1, 13)]
    ends = [later - timedelta(days=1) for later in starts[1:]] + [date(2026, 12, 31)]
    return list(zip(starts, ends, strict=True))
