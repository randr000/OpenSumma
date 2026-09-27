import pytest

from opensumma.kernel.enums import AccountType, NormalBalance, PeriodStatus


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
