"""The double-entry rules of a journal entry, checked on objects in memory."""

from decimal import Decimal

import pytest

from opensumma.kernel.enums import IssueCode, JournalEntryStatus
from opensumma.kernel.errors import JournalEntryError, ValidationIssue
from opensumma.kernel.models import JournalEntry, JournalLine


def _entry(*amounts: str) -> JournalEntry:
    return JournalEntry(
        description="test",
        lines=[
            JournalLine(line_number=number, amount=Decimal(amount))
            for number, amount in enumerate(amounts, start=1)
        ],
    )


def test_a_new_entry_is_a_draft() -> None:
    assert JournalEntry(description="test").status is JournalEntryStatus.DRAFT


def test_the_total_is_the_exact_decimal_sum_of_signed_amounts() -> None:
    entry = _entry("0.10", "0.20", "-0.30")

    assert entry.total == Decimal("0.00")
    assert isinstance(entry.total, Decimal)
    assert entry.is_balanced
    assert entry.structure_issues() == []


def test_a_zero_line_balances_against_nothing() -> None:
    entry = _entry("5.00", "0.00", "-5.00")

    assert entry.is_balanced
    assert entry.structure_issues() == []


@pytest.mark.parametrize(
    ("amounts", "total", "larger"),
    [
        (("100.00", "-99.99"), "0.01", "debits exceed credits by 0.01"),
        (("99.99", "-100.00"), "-0.01", "credits exceed debits by 0.01"),
    ],
)
def test_an_unbalanced_entry_reports_its_total_and_which_side_is_larger(
    amounts: tuple[str, ...], total: str, larger: str
) -> None:
    entry = _entry(*amounts)

    assert entry.total == Decimal(total)
    (issue,) = entry.structure_issues()
    assert issue.code is IssueCode.UNBALANCED
    assert f"sum to {total} rather than zero" in issue.message
    assert larger in issue.message


def test_an_entry_needs_two_lines_even_when_it_balances_trivially() -> None:
    assert [issue.code for issue in _entry().structure_issues()] == [
        IssueCode.TOO_FEW_LINES
    ]


def test_a_single_line_entry_is_both_too_short_and_unbalanced() -> None:
    assert [issue.code for issue in _entry("1.00").structure_issues()] == [
        IssueCode.TOO_FEW_LINES,
        IssueCode.UNBALANCED,
    ]


def test_issues_name_their_line() -> None:
    assert str(ValidationIssue(IssueCode.UNKNOWN_ACCOUNT, "no account", 3)) == (
        "line 3: no account"
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
