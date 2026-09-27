"""Relational tables for the kernel's master data.

These are the persistence model, not the kernel's API. Rules that span rows, such
as non-overlapping periods and the consequences of the account hierarchy, live in
the service modules beside this one. Rules a database can check portably are
declared here too, so that even a direct write cannot produce a nonsensical row.
"""

from datetime import date
from enum import Enum as PyEnum

from sqlalchemy import CheckConstraint, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from opensumma.db import Base, TimestampMixin
from opensumma.kernel.enums import AccountType, NormalBalance, PeriodStatus

CODE_LENGTH = 32
NAME_LENGTH = 200


def _enum_column(enum_type: type[PyEnum]) -> Enum:
    """An enum column stored as portable text.

    PostgreSQL native enum types are avoided: altering one later is awkward and
    SQLite has no equivalent, so the two backends would diverge. The allowed
    values are constrained by ``_enum_check`` rather than by the type itself,
    because a type-generated CHECK is emitted both by the type and by Alembic's
    rendering of it, which produces duplicate constraints with the same name.
    """
    return Enum(
        enum_type,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


def _enum_check(column: str, enum_type: type[PyEnum]) -> CheckConstraint:
    """Restrict ``column`` to the values of ``enum_type`` at the database level."""
    allowed = ", ".join(f"'{member.value}'" for member in enum_type)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"{column}_is_valid")


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
        _enum_check("account_type", AccountType),
        _enum_check("normal_balance", NormalBalance),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(CODE_LENGTH), unique=True)
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    account_type: Mapped[AccountType] = mapped_column(_enum_column(AccountType))
    normal_balance: Mapped[NormalBalance] = mapped_column(_enum_column(NormalBalance))
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
        _enum_check("status", PeriodStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(CODE_LENGTH), unique=True)
    start_date: Mapped[date]
    end_date: Mapped[date]
    status: Mapped[PeriodStatus] = mapped_column(
        _enum_column(PeriodStatus), default=PeriodStatus.OPEN
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
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dimension_id: Mapped[int] = mapped_column(ForeignKey("dimension.id"))
    code: Mapped[str] = mapped_column(String(CODE_LENGTH))
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    is_active: Mapped[bool] = mapped_column(default=True)

    dimension: Mapped[Dimension] = relationship(back_populates="values")

    def __repr__(self) -> str:
        return f"DimensionValue(code={self.code!r})"
