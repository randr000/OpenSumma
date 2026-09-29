import pytest

from opensumma.kernel.enums import (
    AccountType,
    IssueCode,
    JournalEntryStatus,
    NormalBalance,
    PeriodStatus,
)
from opensumma.objects.enums import AccountingObjectType, CounterpartyKind


@pytest.mark.parametrize(
    ("account_type", "expected"),
    [
        (AccountType.ASSET, NormalBalance.DEBIT),
        (AccountType.LIABILITY, NormalBalance.CREDIT),
        (AccountType.EQUITY, NormalBalance.CREDIT),
        (AccountType.REVENUE, NormalBalance.CREDIT),
        (AccountType.EXPENSE, NormalBalance.DEBIT),
    ],
)
def test_each_account_type_has_its_conventional_normal_balance(
    account_type: AccountType, expected: NormalBalance
) -> None:
    assert account_type.normal_balance is expected


def test_the_five_account_types_are_exactly_the_fundamental_classifications() -> None:
    assert [member.value for member in AccountType] == [
        "ASSET",
        "LIABILITY",
        "EQUITY",
        "REVENUE",
        "EXPENSE",
    ]


def test_debit_and_credit_are_the_only_sides() -> None:
    assert [member.value for member in NormalBalance] == ["DEBIT", "CREDIT"]


def test_a_period_is_either_open_or_closed() -> None:
    assert [member.value for member in PeriodStatus] == ["OPEN", "CLOSED"]


@pytest.mark.parametrize(
    ("status", "in_ledger", "is_final"),
    [
        (JournalEntryStatus.DRAFT, False, False),
        (JournalEntryStatus.PROPOSED, False, False),
        (JournalEntryStatus.PENDING_APPROVAL, False, False),
        (JournalEntryStatus.APPROVED, False, False),
        (JournalEntryStatus.POSTED, True, True),
        (JournalEntryStatus.REVERSED, True, True),
        (JournalEntryStatus.VOIDED, False, True),
    ],
)
def test_only_posted_and_reversed_entries_are_in_the_ledger(
    status: JournalEntryStatus, in_ledger: bool, is_final: bool
) -> None:
    assert status.in_ledger is in_ledger
    assert status.is_final is is_final


def test_issue_codes_are_their_own_names() -> None:
    """Codes are compared by callers and benchmarks, so they must be stable text."""
    assert all(code.value == code.name for code in IssueCode)


@pytest.mark.parametrize(
    ("status", "locked"),
    [
        (JournalEntryStatus.DRAFT, False),
        (JournalEntryStatus.PROPOSED, False),
        (JournalEntryStatus.PENDING_APPROVAL, True),
        (JournalEntryStatus.APPROVED, True),
        (JournalEntryStatus.POSTED, True),
        (JournalEntryStatus.REVERSED, True),
        (JournalEntryStatus.VOIDED, True),
    ],
)
def test_an_entrys_content_is_locked_from_submission_onward(
    status: JournalEntryStatus, locked: bool
) -> None:
    assert status.is_locked is locked


@pytest.mark.parametrize(
    ("object_type", "kind"),
    [
        (AccountingObjectType.VENDOR_BILL, CounterpartyKind.VENDOR),
        (AccountingObjectType.VENDOR_PAYMENT, CounterpartyKind.VENDOR),
        (AccountingObjectType.PURCHASE_ORDER, CounterpartyKind.VENDOR),
        (AccountingObjectType.CUSTOMER_INVOICE, CounterpartyKind.CUSTOMER),
        (AccountingObjectType.CUSTOMER_PAYMENT, CounterpartyKind.CUSTOMER),
        (AccountingObjectType.SALES_ORDER, CounterpartyKind.CUSTOMER),
        (AccountingObjectType.EXPENSE, None),
        (AccountingObjectType.BANK_TRANSACTION, None),
        (AccountingObjectType.CONTRACT, None),
        (AccountingObjectType.JOURNAL_ENTRY, None),
        (AccountingObjectType.RECONCILIATION, None),
    ],
)
def test_an_object_type_names_the_counterparty_kind_it_concerns(
    object_type: AccountingObjectType, kind: CounterpartyKind | None
) -> None:
    assert object_type.counterparty_kind is kind
