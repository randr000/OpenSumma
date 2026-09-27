import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opensumma.kernel.accounts import (
    activate_account,
    assert_postable,
    chart_of_accounts,
    create_account,
    deactivate_account,
    descendants,
    get_account,
    postable_accounts,
)
from opensumma.kernel.enums import AccountType, NormalBalance
from opensumma.kernel.errors import (
    AccountTypeMismatchError,
    DuplicateCodeError,
    InactiveAccountError,
    NotPostableError,
    UnknownAccountError,
)
from opensumma.kernel.models import Account


@pytest.fixture
def assets(session: Session) -> Account:
    return create_account(
        session, code="1000", name="Assets", account_type=AccountType.ASSET
    )


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
def test_normal_balance_defaults_to_the_convention_for_the_type(
    session: Session, account_type: AccountType, expected: NormalBalance
) -> None:
    account = create_account(
        session, code="9000", name="Test", account_type=account_type
    )

    assert account.normal_balance is expected
    assert not account.is_contra


def test_a_contra_account_carries_the_opposite_balance_to_its_type(
    session: Session,
) -> None:
    account = create_account(
        session,
        code="1590",
        name="Accumulated Depreciation",
        account_type=AccountType.ASSET,
        normal_balance=NormalBalance.CREDIT,
    )

    assert account.account_type is AccountType.ASSET
    assert account.normal_balance is NormalBalance.CREDIT
    assert account.is_contra


def test_account_codes_are_unique(session: Session, assets: Account) -> None:
    with pytest.raises(DuplicateCodeError):
        create_account(
            session, code="1000", name="Something else", account_type=AccountType.ASSET
        )


def test_an_account_code_may_not_be_blank(session: Session) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        create_account(
            session, code="   ", name="Assets", account_type=AccountType.ASSET
        )


def test_unknown_account_codes_are_reported(session: Session) -> None:
    with pytest.raises(UnknownAccountError):
        get_account(session, "4242")


def test_a_child_account_rolls_up_into_its_parent(
    session: Session, assets: Account
) -> None:
    cash = create_account(
        session,
        code="1110",
        name="Cash",
        account_type=AccountType.ASSET,
        parent=assets,
    )

    assert cash.parent is assets
    assert assets.children == [cash]


def test_children_are_ordered_by_code(session: Session, assets: Account) -> None:
    for code in ("1300", "1100", "1200"):
        create_account(
            session,
            code=code,
            name=f"Account {code}",
            account_type=AccountType.ASSET,
            parent=assets,
        )
    session.flush()
    session.expire(assets, ["children"])

    assert [child.code for child in assets.children] == ["1100", "1200", "1300"]


def test_a_child_must_be_the_same_type_as_its_parent(
    session: Session, assets: Account
) -> None:
    with pytest.raises(AccountTypeMismatchError):
        create_account(
            session,
            code="6100",
            name="Rent Expense",
            account_type=AccountType.EXPENSE,
            parent=assets,
        )


def test_an_account_is_postable_until_it_gains_a_child(
    session: Session, assets: Account
) -> None:
    assert assets.is_postable
    assert_postable(assets)

    create_account(
        session,
        code="1110",
        name="Cash",
        account_type=AccountType.ASSET,
        parent=assets,
    )

    assert not assets.is_postable
    assert not assets.is_leaf
    with pytest.raises(NotPostableError):
        assert_postable(assets)


def test_an_inactive_leaf_account_is_not_postable(
    session: Session, assets: Account
) -> None:
    deactivate_account(assets)

    assert assets.is_leaf
    assert not assets.is_postable
    with pytest.raises(InactiveAccountError):
        assert_postable(assets)


def test_postable_accounts_are_the_active_leaves(
    session: Session, assets: Account
) -> None:
    cash = create_account(
        session, code="1110", name="Cash", account_type=AccountType.ASSET, parent=assets
    )
    retired = create_account(
        session,
        code="1120",
        name="Retired",
        account_type=AccountType.ASSET,
        parent=assets,
    )
    deactivate_account(retired)

    assert postable_accounts(session) == [cash]


def test_deactivating_an_account_deactivates_everything_below_it(
    session: Session, assets: Account
) -> None:
    current = create_account(
        session,
        code="1100",
        name="Current Assets",
        account_type=AccountType.ASSET,
        parent=assets,
    )
    cash = create_account(
        session,
        code="1110",
        name="Cash",
        account_type=AccountType.ASSET,
        parent=current,
    )

    deactivate_account(assets)

    assert not any(account.is_active for account in (assets, current, cash))
    assert postable_accounts(session) == []


def test_an_account_cannot_be_activated_under_an_inactive_parent(
    session: Session, assets: Account
) -> None:
    cash = create_account(
        session, code="1110", name="Cash", account_type=AccountType.ASSET, parent=assets
    )
    deactivate_account(assets)

    with pytest.raises(InactiveAccountError):
        activate_account(cash)


def test_reactivating_a_parent_leaves_its_children_retired(
    session: Session, assets: Account
) -> None:
    cash = create_account(
        session, code="1110", name="Cash", account_type=AccountType.ASSET, parent=assets
    )
    deactivate_account(assets)

    activate_account(assets)

    assert assets.is_active
    assert not cash.is_active


def test_an_active_account_cannot_be_added_under_an_inactive_parent(
    session: Session, assets: Account
) -> None:
    deactivate_account(assets)

    with pytest.raises(InactiveAccountError):
        create_account(
            session,
            code="1110",
            name="Cash",
            account_type=AccountType.ASSET,
            parent=assets,
        )

    inactive_child = create_account(
        session,
        code="1110",
        name="Cash",
        account_type=AccountType.ASSET,
        parent=assets,
        is_active=False,
    )
    assert not inactive_child.is_active


def test_descendants_walks_the_whole_subtree(session: Session, assets: Account) -> None:
    current = create_account(
        session,
        code="1100",
        name="Current Assets",
        account_type=AccountType.ASSET,
        parent=assets,
    )
    create_account(
        session,
        code="1110",
        name="Cash",
        account_type=AccountType.ASSET,
        parent=current,
    )
    create_account(
        session,
        code="1500",
        name="Fixed Assets",
        account_type=AccountType.ASSET,
        parent=assets,
    )

    assert [account.code for account in descendants(assets)] == [
        "1100",
        "1110",
        "1500",
    ]


def test_the_chart_is_listed_by_code(session: Session, assets: Account) -> None:
    create_account(
        session, code="4000", name="Revenue", account_type=AccountType.REVENUE
    )
    create_account(
        session, code="2000", name="Liabilities", account_type=AccountType.LIABILITY
    )

    assert [account.code for account in chart_of_accounts(session)] == [
        "1000",
        "2000",
        "4000",
    ]


def test_the_database_rejects_an_account_that_is_its_own_parent(
    session: Session, assets: Account
) -> None:
    session.flush()
    assets.parent_id = assets.id

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_database_rejects_a_blank_account_code(session: Session) -> None:
    session.add(
        Account(
            code="",
            name="Nameless",
            account_type=AccountType.ASSET,
            normal_balance=NormalBalance.DEBIT,
        )
    )

    with pytest.raises(IntegrityError):
        session.flush()


def test_the_database_rejects_an_account_type_outside_the_five(
    session: Session, assets: Account
) -> None:
    """Raw SQL, because the Python layer would reject the value before the database."""
    session.flush()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE account SET account_type = 'GOODWILL' WHERE id = :id"),
            {"id": assets.id},
        )
