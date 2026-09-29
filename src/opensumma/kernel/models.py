"""Relational tables for the kernel: master data and journal entries.

These are the persistence model, not the kernel's API. Rules that span rows, such
as non-overlapping periods and the consequences of the account hierarchy, live in
the service modules beside this one. Rules a database can check portably are
declared here too, so that even a direct write cannot produce a nonsensical row.

The end of this module guards recorded journal entries: every session refuses to
flush a change to a posted, reversed, or voided entry.
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    event,
    inspect,
    select,
)
from sqlalchemy.orm import (
    Mapped,
    ORMExecuteState,
    Session,
    UOWTransaction,
    mapped_column,
    relationship,
)

from opensumma.db import Base, TimestampMixin, enum_check, enum_column
from opensumma.kernel.enums import (
    LEDGER_STATUSES,
    AccountType,
    IssueCode,
    JournalEntryStatus,
    NormalBalance,
    PeriodStatus,
)
from opensumma.kernel.errors import (
    ImmutableEntryError,
    JournalEntryError,
    ValidationIssue,
)
from opensumma.money import ZERO, Money
from opensumma.utc import UtcDateTime

CODE_LENGTH = 32
NAME_LENGTH = 200


class Account(TimestampMixin, Base):
    """A node in the chart of accounts.

    Accounts form a hierarchy. Only leaf accounts are postable; a parent exists
    to aggregate the accounts below it. An account is active only if all of its
    ancestors are active too, which the account services maintain.
    """

    __tablename__ = "account"
    __table_args__ = (
        CheckConstraint("length(code) > 0", name="code_not_empty"),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="parent_is_not_self"
        ),
        enum_check("account_type", AccountType),
        enum_check("normal_balance", NormalBalance),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(CODE_LENGTH), unique=True)
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    account_type: Mapped[AccountType] = mapped_column(enum_column(AccountType))
    normal_balance: Mapped[NormalBalance] = mapped_column(enum_column(NormalBalance))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("account.id"))
    is_active: Mapped[bool] = mapped_column(default=True)

    parent: Mapped["Account | None"] = relationship(
        back_populates="children", remote_side="Account.id"
    )
    children: Mapped[list["Account"]] = relationship(
        back_populates="parent", order_by="Account.code"
    )

    @property
    def is_leaf(self) -> bool:
        """True when no other account rolls up into this one."""
        return not self.children

    @property
    def is_postable(self) -> bool:
        """True when a journal line may reference this account."""
        return self.is_active and self.is_leaf

    @property
    def is_contra(self) -> bool:
        """True when the account carries the opposite balance to its type.

        Accumulated depreciation is an asset that normally carries a credit
        balance; sales returns are revenue that normally carries a debit balance.
        """
        return self.normal_balance is not self.account_type.normal_balance

    def __repr__(self) -> str:
        return f"Account(code={self.code!r}, name={self.name!r})"


class AccountingPeriod(TimestampMixin, Base):
    """A date range that owns the postings whose accounting date falls in it.

    Periods never overlap, so every date belongs to at most one period. A closed
    period accepts no further postings.
    """

    __tablename__ = "accounting_period"
    __table_args__ = (
        CheckConstraint("length(code) > 0", name="code_not_empty"),
        CheckConstraint("end_date >= start_date", name="end_not_before_start"),
        enum_check("status", PeriodStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(CODE_LENGTH), unique=True)
    start_date: Mapped[date]
    end_date: Mapped[date]
    status: Mapped[PeriodStatus] = mapped_column(
        enum_column(PeriodStatus), default=PeriodStatus.OPEN
    )

    @property
    def is_open(self) -> bool:
        return self.status is PeriodStatus.OPEN

    def contains(self, on: date) -> bool:
        """True when ``on`` falls inside the period; both ends are inclusive."""
        return self.start_date <= on <= self.end_date

    def __repr__(self) -> str:
        return f"AccountingPeriod(code={self.code!r}, status={self.status.value})"


class Dimension(TimestampMixin, Base):
    """An analytical axis on journal lines, such as department or location.

    Dimensions classify postings for reporting. They are not part of the
    double-entry itself: they never affect whether an entry balances.
    """

    __tablename__ = "dimension"
    __table_args__ = (CheckConstraint("length(code) > 0", name="code_not_empty"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(CODE_LENGTH), unique=True)
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))

    values: Mapped[list["DimensionValue"]] = relationship(
        back_populates="dimension", order_by="DimensionValue.code"
    )

    def __repr__(self) -> str:
        return f"Dimension(code={self.code!r})"


class DimensionValue(TimestampMixin, Base):
    """One allowed value of a dimension, such as the Engineering department.

    Values are deactivated rather than deleted, because postings that already
    reference them stay in the ledger forever.
    """

    __tablename__ = "dimension_value"
    __table_args__ = (
        CheckConstraint("length(code) > 0", name="code_not_empty"),
        UniqueConstraint("dimension_id", "code"),
        # Redundant with the primary key, but it is what lets a journal line's
        # dimension reference name the value *and* its dimension together, so the
        # database itself rejects a value filed under the wrong dimension.
        UniqueConstraint("id", "dimension_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dimension_id: Mapped[int] = mapped_column(ForeignKey("dimension.id"))
    code: Mapped[str] = mapped_column(String(CODE_LENGTH))
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    is_active: Mapped[bool] = mapped_column(default=True)

    dimension: Mapped[Dimension] = relationship(back_populates="values")

    def __repr__(self) -> str:
        return f"DimensionValue(code={self.code!r})"


DESCRIPTION_LENGTH = 500

_LEDGER_STATUSES = ", ".join(f"'{status.value}'" for status in LEDGER_STATUSES)


class JournalEntry(TimestampMixin, Base):
    """Debit and credit lines recorded together on one accounting date.

    Once posted, an entry is part of the ledger and never changes again, except
    that it is marked REVERSED when a reversing entry offsets it.
    """

    __tablename__ = "journal_entry"
    __table_args__ = (
        CheckConstraint("length(description) > 0", name="description_not_empty"),
        CheckConstraint(
            "reversal_of_id IS NULL OR reversal_of_id <> id",
            name="does_not_reverse_itself",
        ),
        CheckConstraint(
            f"(status IN ({_LEDGER_STATUSES}) AND posted_at IS NOT NULL) "
            f"OR (status NOT IN ({_LEDGER_STATUSES}) AND posted_at IS NULL)",
            name="posted_at_matches_status",
        ),
        enum_check("status", JournalEntryStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entry_date: Mapped[date] = mapped_column(index=True)
    description: Mapped[str] = mapped_column(String(DESCRIPTION_LENGTH))
    status: Mapped[JournalEntryStatus] = mapped_column(
        enum_column(JournalEntryStatus), default=JournalEntryStatus.DRAFT
    )
    posted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    reversal_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entry.id"), unique=True
    )

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry",
        order_by="JournalLine.line_number",
        cascade="all, delete-orphan",
    )
    reversal_of: Mapped["JournalEntry | None"] = relationship(
        back_populates="reversed_by", remote_side="JournalEntry.id"
    )
    reversed_by: Mapped["JournalEntry | None"] = relationship(
        back_populates="reversal_of"
    )

    def __init__(self, **kwargs: Any) -> None:
        # Column defaults apply only at flush; the status is needed before that.
        kwargs.setdefault("status", JournalEntryStatus.DRAFT)
        super().__init__(**kwargs)

    @property
    def total_debits(self) -> Decimal:
        return sum((line.debit for line in self.lines), ZERO)

    @property
    def total_credits(self) -> Decimal:
        return sum((line.credit for line in self.lines), ZERO)

    @property
    def is_balanced(self) -> bool:
        return self.total_debits == self.total_credits

    def structure_issues(self) -> list[ValidationIssue]:
        """What makes the entry unfit for the ledger whatever the master data says.

        These two rules are the double-entry itself, so they are checked both when
        an entry is validated and, as a last line of defence, whenever one is
        flushed into the ledger.
        """
        issues = []
        if len(self.lines) < 2:
            issues.append(
                ValidationIssue(
                    IssueCode.TOO_FEW_LINES,
                    "a journal entry needs at least two lines, "
                    f"but this one has {len(self.lines)}",
                )
            )
        if not self.is_balanced:
            debits, credits = self.total_debits, self.total_credits
            issues.append(
                ValidationIssue(
                    IssueCode.UNBALANCED,
                    f"debits total {debits} but credits total {credits}, "
                    f"a difference of {debits - credits}",
                )
            )
        return issues

    def __repr__(self) -> str:
        return f"JournalEntry(id={self.id!r}, status={self.status.value})"


class JournalLine(Base):
    """One debit or one credit to one account, within a journal entry."""

    __tablename__ = "journal_line"
    __table_args__ = (
        UniqueConstraint("journal_entry_id", "line_number"),
        CheckConstraint("line_number >= 1", name="line_number_positive"),
        # Exactly one side carries a strictly positive amount: never both, never
        # neither, never a negative amount.
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name="one_positive_side",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entry.id"))
    line_number: Mapped[int]
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    debit: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    credit: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    memo: Mapped[str | None] = mapped_column(String(DESCRIPTION_LENGTH))

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()
    dimensions: Mapped[list["JournalLineDimension"]] = relationship(
        back_populates="line",
        order_by="JournalLineDimension.dimension_id",
        cascade="all, delete-orphan",
    )

    def __init__(self, **kwargs: Any) -> None:
        # Column defaults apply only at flush; totals are needed before that.
        kwargs.setdefault("debit", ZERO)
        kwargs.setdefault("credit", ZERO)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return (
            f"JournalLine(line_number={self.line_number!r}, "
            f"debit={self.debit}, credit={self.credit})"
        )


class JournalLineDimension(Base):
    """The value a journal line carries for one dimension.

    The value is referenced together with its dimension, so the database rejects a
    value filed under the wrong dimension, and the primary key allows at most one
    value per dimension on each line.
    """

    __tablename__ = "journal_line_dimension"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dimension_value_id", "dimension_id"],
            ["dimension_value.id", "dimension_value.dimension_id"],
        ),
    )

    journal_line_id: Mapped[int] = mapped_column(
        ForeignKey("journal_line.id"), primary_key=True
    )
    dimension_id: Mapped[int] = mapped_column(primary_key=True)
    dimension_value_id: Mapped[int]

    line: Mapped[JournalLine] = relationship(back_populates="dimensions")
    value: Mapped[DimensionValue] = relationship()


# --- Protection of recorded journal entries ------------------------------------
#
# A posted, reversed, or voided entry is final. The kernel's services never change
# one, and these session hooks make sure nothing else does by accident either:
# every flush is checked, and bulk INSERT/UPDATE/DELETE statements, which would
# bypass that check, are refused on journal tables outright. Raw SQL on a
# connection is outside their reach; agents never get SQL access, so that is the
# boundary.

_JOURNAL_TABLES = frozenset({"journal_entry", "journal_line", "journal_line_dimension"})
_JOURNAL_TYPES = (JournalEntry, JournalLine, JournalLineDimension)


def _identity(obj: object) -> int | None:
    """The first primary-key value ``obj`` was loaded with, or None if it is new.

    For a line dimension that is the id of its line.
    """
    key = inspect(obj, raiseerr=True).identity
    return None if key is None else int(key[0])


def _recorded_statuses(
    session: Session, entry_ids: Iterable[int]
) -> dict[int, JournalEntryStatus]:
    """Each entry's status as last written to the database.

    Read from the database rather than from attribute history: once an object has
    been expired, for instance by a commit, SQLAlchemy no longer knows an
    attribute's previous value, and history alone would let a change through.
    """
    ids = set(entry_ids)
    if not ids:
        return {}
    table = JournalEntry.__table__
    rows = session.execute(
        select(table.c.id, table.c.status).where(table.c.id.in_(ids))
    )
    return {row.id: row.status for row in rows}


def _recorded_entry_of_lines(
    session: Session, line_ids: Iterable[int]
) -> dict[int, int]:
    """The entry each line belonged to, as last written to the database."""
    ids = set(line_ids)
    if not ids:
        return {}
    table = JournalLine.__table__
    rows = session.execute(
        select(table.c.id, table.c.journal_entry_id).where(table.c.id.in_(ids))
    )
    return {row.id: row.journal_entry_id for row in rows}


def _touched(obj: JournalLine | JournalLineDimension) -> tuple[set[int], set[int]]:
    """The entries and lines, already in the database, that ``obj`` would alter.

    Both where it was loaded from and where it is attached now count, so moving a
    line out of a posted entry or into one is caught either way.
    """
    entry_ids: set[int] = set()
    line_ids: set[int] = set()
    if (loaded_line_id := _identity(obj)) is not None:
        line_ids.add(loaded_line_id)
    line = obj if isinstance(obj, JournalLine) else obj.line
    if line is not None:
        if (line_id := _identity(line)) is not None:
            line_ids.add(line_id)
        if line.entry is not None and (entry_id := _identity(line.entry)) is not None:
            entry_ids.add(entry_id)
    return entry_ids, line_ids


def _changed_columns(entry: JournalEntry) -> set[str]:
    state = inspect(entry)
    return {
        column.key
        for column in state.mapper.column_attrs
        if state.attrs[column.key].history.has_changes()
    }


@event.listens_for(Session, "before_flush")
def _guard_recorded_entries(
    session: Session, flush_context: UOWTransaction, instances: object
) -> None:
    modified = [
        obj
        for obj in session.dirty
        if isinstance(obj, _JOURNAL_TYPES) and session.is_modified(obj)
    ]
    candidates = (*session.new, *modified, *session.deleted)
    entries = [obj for obj in candidates if isinstance(obj, JournalEntry)]
    parts = [
        obj for obj in candidates if isinstance(obj, JournalLine | JournalLineDimension)
    ]
    if not entries and not parts:
        return

    touched = {id(part): _touched(part) for part in parts}
    entry_of_line = _recorded_entry_of_lines(
        session, (line_id for _, line_ids in touched.values() for line_id in line_ids)
    )
    recorded_entry_ids: set[int] = set()
    for entry in entries:
        if (entry_id := _identity(entry)) is not None:
            recorded_entry_ids.add(entry_id)
    for entry_ids, _ in touched.values():
        recorded_entry_ids |= entry_ids
    statuses = _recorded_statuses(
        session, {*recorded_entry_ids, *entry_of_line.values()}
    )

    for entry in entries:
        _check_entry(session, entry, statuses)

    for part in parts:
        entry_ids, line_ids = touched[id(part)]
        entry_ids |= {entry_of_line[i] for i in line_ids if i in entry_of_line}
        final = sorted(i for i in entry_ids if i in statuses and statuses[i].is_final)
        if final:
            raise ImmutableEntryError(
                f"journal entry {final[0]} is {statuses[final[0]].value}; "
                "its lines and their dimensions cannot change"
            )


def _check_entry(
    session: Session, entry: JournalEntry, statuses: dict[int, JournalEntryStatus]
) -> None:
    entry_id = _identity(entry)
    recorded = statuses.get(entry_id) if entry_id is not None else None

    if recorded is not None and recorded.is_final:
        if entry in session.deleted:
            raise ImmutableEntryError(
                f"journal entry {entry_id} is {recorded.value} and cannot be "
                "deleted; reverse it instead"
            )
        changed = _changed_columns(entry)
        reversing = (
            recorded is JournalEntryStatus.POSTED
            and changed == {"status"}
            and entry.status is JournalEntryStatus.REVERSED
        )
        if changed and not reversing:
            raise ImmutableEntryError(
                f"journal entry {entry_id} is {recorded.value} and cannot be "
                f"changed ({', '.join(sorted(changed))}); reverse it and post a "
                "correction instead"
            )

    already_in_ledger = recorded is not None and recorded.in_ledger
    if (
        entry.status.in_ledger
        and not already_in_ledger
        and entry not in session.deleted
    ):
        issues = entry.structure_issues()
        if issues:
            raise JournalEntryError(issues)


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_journal_changes(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    table = getattr(state.statement, "table", None)
    if table is not None and table.name in _JOURNAL_TABLES:
        raise ImmutableEntryError(
            f"bulk writes to {table.name} are refused; write journal entries "
            "through the session so that recorded entries stay protected"
        )
