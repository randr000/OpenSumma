"""The double-entry rules of a journal entry, checked on objects in memory."""

from decimal import Decimal

from opensumma.kernel.enums import IssueCode, JournalEntryStatus
from opensumma.kernel.errors import JournalEntryError, ValidationIssue
from opensumma.kernel.models import JournalEntry, JournalLine


def _entry(*sides: tuple[str, str]) -> JournalEntry:
    return JournalEntry(
        description="test",
        lines=[
            JournalLine(line_number=number, **{side: Decimal(amount)})
            for number, (side, amount) in enumerate(sides, start=1)
        ],
    )


def test_a_new_entry_is_a_draft() -> None:
    assert JournalEntry(description="test").status is JournalEntryStatus.DRAFT


def test_a_line_defaults_the_unused_side_to_zero() -> None:
    line = JournalLine(line_number=1, debit=Decimal("5.00"))
    assert line.credit == Decimal("0.00")


def test_totals_are_exact_decimal_sums() -> None:
    entry = _entry(("debit", "0.10"), ("debit", "0.20"), ("credit", "0.30"))

    assert entry.total_debits == Decimal("0.30")
    assert entry.total_credits == Decimal("0.30")
    assert isinstance(entry.total_debits, Decimal)
    assert entry.is_balanced
    assert entry.structure_issues() == []


def test_an_unbalanced_entry_reports_both_totals_and_the_difference() -> None:
    entry = _entry(("debit", "100.00"), ("credit", "99.99"))

    (issue,) = entry.structure_issues()
    assert issue.code is IssueCode.UNBALANCED
    assert "100.00" in issue.message
    assert "99.99" in issue.message
    assert "0.01" in issue.message


def test_an_entry_needs_two_lines_even_when_it_balances_trivially() -> None:
    assert [issue.code for issue in _entry().structure_issues()] == [
        IssueCode.TOO_FEW_LINES
    ]


def test_a_single_line_entry_is_both_too_short_and_unbalanced() -> None:
    assert [issue.code for issue in _entry(("debit", "1.00")).structure_issues()] == [
        IssueCode.TOO_FEW_LINES,
        IssueCode.UNBALANCED,
    ]


def test_issues_name_their_line() -> None:
    assert str(ValidationIssue(IssueCode.ZERO_AMOUNT, "no amount", 3)) == (
        "line 3: no amount"
    )
    assert str(ValidationIssue(IssueCode.UNBALANCED, "off by 1")) == "off by 1"


def test_a_journal_entry_error_carries_every_issue() -> None:
    issues = [
        ValidationIssue(IssueCode.UNBALANCED, "off by 1"),
        ValidationIssue(IssueCode.ACCOUNT_INACTIVE, "account 6100 is inactive", 2),
    ]

    error = JournalEntryError(issues)

    assert error.issues == tuple(issues)
    assert str(error) == "off by 1; line 2: account 6100 is inactive"
