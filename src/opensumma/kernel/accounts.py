"""Chart-of-accounts services.

Accounts are created and changed through these functions rather than by
constructing ``Account`` directly, because the rules that keep a chart coherent
span more than one row:

- codes are unique;
- a child's account type matches its parent's, so a subtree reports as one type;
- an account is active only if all of its ancestors are active;
- only active leaf accounts are postable.
"""

from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from opensumma.kernel.enums import AccountType, NormalBalance
from opensumma.kernel.errors import (
    AccountTypeMismatchError,
    DuplicateCodeError,
    InactiveAccountError,
    NotPostableError,
    UnknownAccountError,
)
from opensumma.kernel.models import Account


def create_account(
    session: Session,
    *,
    code: str,
    name: str,
    account_type: AccountType,
    parent: Account | None = None,
    normal_balance: NormalBalance | None = None,
    is_active: bool = True,
) -> Account:
    """Add an account to the chart.

    ``normal_balance`` defaults to the normal balance of ``account_type``. Contra
    accounts, such as accumulated depreciation, pass the opposite side explicitly.

    Adding a child to a postable account turns that account into an aggregate,
    which is how a chart is built from the top down.
    """
    code = code.strip()
    if not code:
        raise ValueError("account code must not be empty")
    if find_account(session, code) is not None:
        raise DuplicateCodeError(f"account code {code!r} already exists")
    if parent is not None:
        if parent.account_type is not account_type:
            raise AccountTypeMismatchError(
                f"account {code} is {account_type.value}, but its parent "
                f"{parent.code} is {parent.account_type.value}"
            )
        if is_active and not parent.is_active:
            raise InactiveAccountError(
                f"cannot add active account {code} under inactive parent {parent.code}"
            )

    account = Account(
        code=code,
        name=name,
        account_type=account_type,
        normal_balance=normal_balance or account_type.normal_balance,
        parent=parent,
        is_active=is_active,
    )
    session.add(account)
    return account


def find_account(session: Session, code: str) -> Account | None:
    """Return the account with ``code``, or ``None``."""
    return session.scalars(select(Account).where(Account.code == code)).one_or_none()


def get_account(session: Session, code: str) -> Account:
    """Return the account with ``code``, or raise ``UnknownAccountError``."""
    account = find_account(session, code)
    if account is None:
        raise UnknownAccountError(f"no account with code {code!r}")
    return account


def chart_of_accounts(session: Session) -> list[Account]:
    """Every account, ordered by code."""
    return list(session.scalars(select(Account).order_by(Account.code)))


def postable_accounts(session: Session) -> list[Account]:
    """The accounts a journal line may reference: the active leaves."""
    child = aliased(Account)
    has_children = select(child.id).where(child.parent_id == Account.id).exists()
    return list(
        session.scalars(
            select(Account)
            .where(Account.is_active, ~has_children)
            .order_by(Account.code)
        )
    )


def assert_postable(account: Account) -> None:
    """Raise unless ``account`` may appear on a journal line."""
    if not account.is_leaf:
        raise NotPostableError(
            f"account {account.code} aggregates child accounts; post to a leaf instead"
        )
    if not account.is_active:
        raise InactiveAccountError(f"account {account.code} is inactive")


def descendants(account: Account) -> Iterator[Account]:
    """Every account below ``account``, depth first."""
    for child in account.children:
        yield child
        yield from descendants(child)


def deactivate_account(account: Account) -> None:
    """Deactivate ``account`` and everything that rolls up into it.

    Deactivating only the parent would leave its leaves postable, which would
    contradict the intent of retiring a whole section of the chart.
    """
    account.is_active = False
    for descendant in descendants(account):
        descendant.is_active = False


def activate_account(account: Account) -> None:
    """Reactivate ``account`` alone; its parent must already be active.

    Descendants stay inactive, because some of them may have been retired
    individually before the subtree was.
    """
    if account.parent is not None and not account.parent.is_active:
        raise InactiveAccountError(
            f"cannot activate {account.code} while its parent "
            f"{account.parent.code} is inactive"
        )
    account.is_active = True
