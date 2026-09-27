import pytest
from sqlalchemy.orm import Session

from opensumma.kernel.accounts import chart_of_accounts, postable_accounts
from opensumma.kernel.dimensions import resolve_dimension_value
from opensumma.kernel.enums import AccountType
from opensumma.kernel.errors import DuplicateCodeError
from opensumma.kernel.seed import (
    DEFAULT_CHART_OF_ACCOUNTS,
    DEFAULT_DIMENSIONS,
    seed_chart_of_accounts,
    seed_dimensions,
)


def test_the_seed_creates_every_account_in_the_template(session: Session) -> None:
    accounts = seed_chart_of_accounts(session)

    assert len(accounts) == len(DEFAULT_CHART_OF_ACCOUNTS)
    assert [account.code for account in chart_of_accounts(session)] == sorted(
        spec.code for spec in DEFAULT_CHART_OF_ACCOUNTS
    )


def test_all_five_account_types_appear_in_the_seeded_chart(session: Session) -> None:
    seed_chart_of_accounts(session)

    present = {account.account_type for account in chart_of_accounts(session)}
    assert present == set(AccountType)


def test_every_seeded_account_shares_its_parents_type(session: Session) -> None:
    seed_chart_of_accounts(session)

    for account in chart_of_accounts(session):
        if account.parent is not None:
            assert account.account_type is account.parent.account_type


def test_the_seeded_chart_has_a_single_root_per_account_type(session: Session) -> None:
    seed_chart_of_accounts(session)

    roots = [
        account for account in chart_of_accounts(session) if account.parent is None
    ]
    assert len(roots) >= len(AccountType)
    assert {root.account_type for root in roots} == set(AccountType)


def test_only_the_seeded_leaves_are_postable(session: Session) -> None:
    seed_chart_of_accounts(session)

    postable = postable_accounts(session)
    assert postable, "a seeded chart must offer somewhere to post"
    for account in chart_of_accounts(session):
        assert account.is_postable == (account in postable)
        assert account.is_postable is account.is_leaf


def test_seeded_contra_accounts_carry_the_opposite_balance(session: Session) -> None:
    accounts = seed_chart_of_accounts(session)

    accumulated_depreciation = accounts["1590"]
    assert accumulated_depreciation.account_type is AccountType.ASSET
    assert accumulated_depreciation.is_contra

    sales_returns = accounts["4900"]
    assert sales_returns.account_type is AccountType.REVENUE
    assert sales_returns.is_contra


def test_seeding_an_already_seeded_chart_is_rejected(session: Session) -> None:
    seed_chart_of_accounts(session)

    with pytest.raises(DuplicateCodeError):
        seed_chart_of_accounts(session)


def test_the_seed_creates_every_dimension_and_value(session: Session) -> None:
    created = seed_dimensions(session)

    assert set(created) == {spec.code for spec in DEFAULT_DIMENSIONS}
    for spec in DEFAULT_DIMENSIONS:
        for code, name in spec.values:
            assert resolve_dimension_value(session, spec.code, code).name == name
