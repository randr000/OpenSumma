"""Journal entry services: record, validate, post, void, and reverse.

Every line carries one signed amount: a debit is positive, a credit negative, and a
line may be zero. An entry balances when its lines sum to zero.

Two tiers of rules apply, and they are kept apart on purpose.

**Recording** rejects what cannot be stored at all: an amount that is not an exact
``Decimal`` in whole cents; a reference to an account, dimension, or dimension value
that does not exist.

**Posting** rejects what cannot enter the ledger: fewer than two lines, amounts that
do not sum to zero, an aggregate or inactive account, a missing or closed period, or
a retired dimension value. A draft may break these rules, because it may still be
incomplete and master data may change before it is posted, so they are checked at
the moment of posting.

Both tiers report every problem at once, as ``ValidationIssue`` values with stable
codes, so a proposal can be corrected in one pass.

The kernel posts an entry from any status that is not yet final. Which statuses and
which actors may post is the workflow engine's decision (Phase 5), not the kernel's.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from opensumma.kernel.accounts import find_account
from opensumma.kernel.dimensions import get_dimension_value
from opensumma.kernel.enums import IssueCode, JournalEntryStatus
from opensumma.kernel.errors import (
    AlreadyPostedError,
    EntryStatusError,
    JournalEntryError,
    UnknownDimensionError,
    UnknownDimensionValueError,
    UnknownJournalEntryError,
    UnknownPeriodError,
    ValidationIssue,
)
from opensumma.kernel.models import (
    DESCRIPTION_LENGTH,
    DimensionValue,
    JournalEntry,
    JournalLine,
    JournalLineDimension,
)
from opensumma.kernel.periods import hold_period_for, period_for_date
from opensumma.money import ZERO, ensure_money
from opensumma.utc import ensure_date, utcnow


@dataclass(frozen=True)
class LineInput:
    """One proposed journal line, referring to master data by code.

    ``amount`` is signed: positive for a debit, negative for a credit; it may be
    zero. ``dimensions`` maps a dimension code to one of its value codes, so a line
    can carry at most one value per dimension.
    """

    account: str
    amount: Decimal
    memo: str | None = None
    dimensions: Mapping[str, str] = field(default_factory=dict)


def create_journal_entry(
    session: Session,
    *,
    entry_date: date,
    description: str,
    lines: Sequence[LineInput],
) -> JournalEntry:
    """Record a DRAFT journal entry.

    Raises ``JournalEntryError`` listing everything that prevents the entry from
    being stored. An entry that can be stored but not posted, such as an unbalanced
    one, is recorded; ``validate_journal_entry`` reports why it cannot be posted.
    """
    ensure_date(entry_date)
    issues = _description_issues(description)
    built: list[JournalLine] = []
    for number, spec in enumerate(lines, start=1):
        line, line_issues = _build_line(session, number, spec)
        issues.extend(line_issues)
        if line is not None:
            built.append(line)
    if issues:
        raise JournalEntryError(issues)

    entry = JournalEntry(
        entry_date=entry_date, description=description.strip(), lines=built
    )
    session.add(entry)
    return entry


def get_journal_entry(session: Session, entry_id: int) -> JournalEntry:
    """Return the journal entry with ``entry_id``, or raise."""
    entry = session.get(JournalEntry, entry_id)
    if entry is None:
        raise UnknownJournalEntryError(f"no journal entry with id {entry_id}")
    return entry


def validate_journal_entry(
    session: Session, entry: JournalEntry
) -> list[ValidationIssue]:
    """Every reason ``entry`` could not be posted now; empty when it could.

    The entry's status is not considered: this answers whether its content is fit
    for the ledger. Issues come in a stable order, entry-level first, then by line.
    """
    issues = entry.structure_issues()

    try:
        period = period_for_date(session, entry.entry_date)
    except UnknownPeriodError as error:
        issues.append(ValidationIssue(IssueCode.NO_PERIOD, str(error)))
    else:
        if not period.is_open:
            issues.append(
                ValidationIssue(
                    IssueCode.PERIOD_CLOSED,
                    f"accounting period {period.code} is closed",
                )
            )

    for line in entry.lines:
        account = line.account
        if not account.is_leaf:
            issues.append(
                ValidationIssue(
                    IssueCode.ACCOUNT_NOT_POSTABLE,
                    f"account {account.code} aggregates child accounts; "
                    "post to a leaf instead",
                    line.line_number,
                )
            )
        if not account.is_active:
            issues.append(
                ValidationIssue(
                    IssueCode.ACCOUNT_INACTIVE,
                    f"account {account.code} is inactive",
                    line.line_number,
                )
            )
        for tag in line.dimensions:
            if not tag.value.is_active:
                issues.append(
                    ValidationIssue(
                        IssueCode.DIMENSION_VALUE_INACTIVE,
                        f"{_describe(tag.value)} is inactive",
                        line.line_number,
                    )
                )
    return issues


def post_journal_entry(session: Session, entry: JournalEntry) -> None:
    """Validate ``entry`` and, if it is fit for the ledger, post it.

    Raises ``JournalEntryError`` with every issue if it is not, and leaves it
    untouched. Posting is permanent: afterwards the entry can only be reversed.
    """
    if entry.status.in_ledger:
        raise AlreadyPostedError(
            f"{_label(entry)} is already {entry.status.value}; "
            "an entry is posted exactly once",
            entry.status,
        )
    if entry.status is JournalEntryStatus.VOIDED:
        raise EntryStatusError(
            f"{_label(entry)} is VOIDED and can never be posted", entry.status
        )
    hold_period_for(session, entry.entry_date)
    issues = validate_journal_entry(session, entry)
    if issues:
        raise JournalEntryError(issues)
    _mark_posted(entry)


def void_journal_entry(entry: JournalEntry) -> None:
    """Abandon an entry that was never posted. It never affects the ledger."""
    if entry.status.in_ledger:
        raise EntryStatusError(
            f"{_label(entry)} is {entry.status.value}; "
            "a posted entry is reversed, not voided",
            entry.status,
        )
    if entry.status is JournalEntryStatus.VOIDED:
        raise EntryStatusError(f"{_label(entry)} is already VOIDED", entry.status)
    entry.status = JournalEntryStatus.VOIDED


def reverse_journal_entry(
    session: Session,
    entry: JournalEntry,
    *,
    entry_date: date,
    description: str | None = None,
) -> JournalEntry:
    """Post a new entry that offsets ``entry`` line by line, each amount negated,
    and mark it REVERSED.

    Both entries stay in the ledger and together net to nothing. The reversal is
    validated like any other entry, so it needs an open period on ``entry_date``
    and accounts that are still postable; if it is not valid, nothing changes.
    """
    ensure_date(entry_date)
    if entry.status is not JournalEntryStatus.POSTED:
        if entry.status is JournalEntryStatus.REVERSED and entry.reversed_by:
            reason = f"is already reversed by journal entry {entry.reversed_by.id}"
        else:
            reason = f"is {entry.status.value}; only posted entries can be reversed"
        raise EntryStatusError(f"{_label(entry)} {reason}", entry.status)

    if entry.id is None:
        session.flush()  # the default description names the entry by its id
    text = (description or "").strip() or f"Reversal of journal entry {entry.id}"
    if issues := _description_issues(text):
        raise JournalEntryError(issues)

    reversal = JournalEntry(
        entry_date=entry_date,
        description=text,
        lines=[
            JournalLine(
                line_number=line.line_number,
                account=line.account,
                amount=ZERO - line.amount,  # never -0.00
                memo=line.memo,
                dimensions=[
                    JournalLineDimension(value=tag.value) for tag in line.dimensions
                ],
            )
            for line in entry.lines
        ],
    )
    # Validated before it is linked to the original or added to the session, so a
    # rejected reversal leaves no trace behind.
    hold_period_for(session, entry_date)
    if issues := validate_journal_entry(session, reversal):
        raise JournalEntryError(issues)

    reversal.reversal_of = entry
    session.add(reversal)
    _mark_posted(reversal)
    entry.status = JournalEntryStatus.REVERSED
    return reversal


def _build_line(
    session: Session, number: int, spec: LineInput
) -> tuple[JournalLine | None, list[ValidationIssue]]:
    """Build line ``number`` from ``spec``, or report why it cannot be stored."""
    issues: list[ValidationIssue] = []

    def report(code: IssueCode, message: str) -> None:
        issues.append(ValidationIssue(code, message, number))

    amount: Decimal | None = None
    try:
        amount = ensure_money(spec.amount)
    except (TypeError, ValueError) as error:
        report(IssueCode.INVALID_AMOUNT, f"the amount is not exact: {error}")

    account = find_account(session, spec.account)
    if account is None:
        report(IssueCode.UNKNOWN_ACCOUNT, f"no account with code {spec.account!r}")

    values: list[DimensionValue] = []
    for dimension_code, value_code in sorted(spec.dimensions.items()):
        try:
            values.append(get_dimension_value(session, dimension_code, value_code))
        except UnknownDimensionError as error:
            report(IssueCode.UNKNOWN_DIMENSION, str(error))
        except UnknownDimensionValueError as error:
            report(IssueCode.UNKNOWN_DIMENSION_VALUE, str(error))

    if spec.memo is not None and len(spec.memo) > DESCRIPTION_LENGTH:
        report(
            IssueCode.TEXT_TOO_LONG,
            f"a line memo is limited to {DESCRIPTION_LENGTH} characters",
        )

    if issues or amount is None or account is None:
        return None, issues
    line = JournalLine(
        line_number=number,
        account=account,
        amount=amount,
        memo=spec.memo or None,
        dimensions=[JournalLineDimension(value=value) for value in values],
    )
    return line, []


def _description_issues(description: str) -> list[ValidationIssue]:
    text = description.strip()
    if not text:
        return [
            ValidationIssue(
                IssueCode.MISSING_DESCRIPTION,
                "a journal entry needs a description of what it records",
            )
        ]
    if len(text) > DESCRIPTION_LENGTH:
        return [
            ValidationIssue(
                IssueCode.TEXT_TOO_LONG,
                f"a description is limited to {DESCRIPTION_LENGTH} characters",
            )
        ]
    return []


def _mark_posted(entry: JournalEntry) -> None:
    entry.status = JournalEntryStatus.POSTED
    entry.posted_at = utcnow()


def _label(entry: JournalEntry) -> str:
    return "the journal entry" if entry.id is None else f"journal entry {entry.id}"


def _describe(value: DimensionValue) -> str:
    return f"value {value.code!r} of dimension {value.dimension.code}"
