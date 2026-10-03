"""Report values that need no database: sign handling and the balance checks."""

from datetime import date
from decimal import Decimal

import pytest

from opensumma.kernel.enums import AccountType, NormalBalance
from opensumma.kernel.reports import (
    BalanceSheet,
    StatementSection,
    TrialBalance,
    _in_direction,
)


@pytest.mark.parametrize(
    ("amount", "debit_side", "credit_side"),
    [
        ("60.00", "60.00", "-60.00"),
        ("-60.00", "-60.00", "60.00"),
        ("0.00", "0.00", "0.00"),
    ],
)
def test_a_statement_states_a_signed_amount_on_its_sections_side(
    amount: str, debit_side: str, credit_side: str
) -> None:
    assert _in_direction(Decimal(amount), NormalBalance.DEBIT) == Decimal(debit_side)
    assert _in_direction(Decimal(amount), NormalBalance.CREDIT) == Decimal(credit_side)


def test_a_zero_amount_never_prints_as_negative_zero() -> None:
    assert str(_in_direction(Decimal("0.00"), NormalBalance.CREDIT)) == "0.00"


def test_a_trial_balance_is_balanced_when_its_balances_sum_to_zero() -> None:
    def trial(total: str) -> TrialBalance:
        return TrialBalance(date(2026, 1, 31), (), Decimal(total))

    assert trial("0.00").is_balanced
    assert not trial("0.01").is_balanced
    assert not trial("-0.01").is_balanced


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
