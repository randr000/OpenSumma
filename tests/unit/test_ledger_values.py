"""Report values that need no database: sign handling and the balance checks."""

from datetime import date
from decimal import Decimal

import pytest

from opensumma.kernel.enums import AccountType, NormalBalance
from opensumma.kernel.ledger import Activity
from opensumma.kernel.reports import BalanceSheet, StatementSection, TrialBalance


@pytest.mark.parametrize(
    ("debits", "credits", "debit_side", "credit_side"),
    [
        ("100.00", "40.00", "60.00", "-60.00"),
        ("40.00", "100.00", "-60.00", "60.00"),
        ("0.00", "0.00", "0.00", "0.00"),
        ("25.00", "25.00", "0.00", "0.00"),
    ],
)
def test_a_balance_is_stated_on_the_side_asked_for(
    debits: str, credits: str, debit_side: str, credit_side: str
) -> None:
    activity = Activity(Decimal(debits), Decimal(credits))

    assert activity.balance(NormalBalance.DEBIT) == Decimal(debit_side)
    assert activity.balance(NormalBalance.CREDIT) == Decimal(credit_side)


def test_a_zero_balance_never_prints_as_negative_zero() -> None:
    assert str(Activity().balance(NormalBalance.CREDIT)) == "0.00"
    assert str(
        Activity(Decimal("5.00"), Decimal("5.00")).balance(NormalBalance.DEBIT)
    ) == ("0.00")


def test_activity_adds_up_side_by_side() -> None:
    total = Activity(Decimal("1.10"), Decimal("0.00")) + Activity(
        Decimal("0.00"), Decimal("2.20")
    )
    assert total == Activity(Decimal("1.10"), Decimal("2.20"))


def test_a_trial_balance_is_balanced_when_its_columns_agree() -> None:
    def trial(debits: str, credits: str) -> TrialBalance:
        return TrialBalance(date(2026, 1, 31), (), Decimal(debits), Decimal(credits))

    assert trial("10.00", "10.00").is_balanced
    assert not trial("10.00", "9.99").is_balanced


def test_a_balance_sheet_balances_when_assets_equal_liabilities_and_equity() -> None:
    def section(account_type: AccountType, total: str) -> StatementSection:
        return StatementSection(account_type, (), Decimal(total))

    def sheet(assets: str) -> BalanceSheet:
        return BalanceSheet(
            as_of=date(2026, 1, 31),
            assets=section(AccountType.ASSET, assets),
            liabilities=section(AccountType.LIABILITY, "30.00"),
            equity=section(AccountType.EQUITY, "50.00"),
            unclosed_net_income=Decimal("20.00"),
            total_equity=Decimal("70.00"),
            total_liabilities_and_equity=Decimal("100.00"),
        )

    assert sheet("100.00").is_balanced
    assert not sheet("100.01").is_balanced
