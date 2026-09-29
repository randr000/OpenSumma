"""Counterparties: the vendors and customers accounting objects name."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from opensumma.kernel import DuplicateCodeError
from opensumma.objects import (
    DEFAULT_COUNTERPARTIES,
    CounterpartyKind,
    CounterpartyKindError,
    InactiveCounterpartyError,
    UnknownCounterpartyError,
    activate_counterparty,
    counterparties,
    create_accounting_object,
    create_counterparty,
    deactivate_counterparty,
    get_counterparty,
    search_accounting_objects,
)

RECEIVED = datetime(2026, 3, 15, 9, 30, tzinfo=UTC)


def test_the_default_cast_is_six_vendors_and_five_customers(books: Session) -> None:
    vendors = counterparties(books, kind=CounterpartyKind.VENDOR)
    customers = counterparties(books, kind="CUSTOMER")

    assert [v.code for v in vendors] == [
        "V-BRIGHT",
        "V-HARBOR",
        "V-KELLER",
        "V-PAPER",
        "V-STRATUS",
        "V-SUMMIT",
    ]
    assert [c.code for c in customers] == [
        "C-BLUEFIN",
        "C-CEDAR",
        "C-HELIO",
        "C-ORCHARD",
        "C-VERTEX",
    ]
    assert len(counterparties(books)) == len(DEFAULT_COUNTERPARTIES) == 11
    assert get_counterparty(books, "V-STRATUS").name == "Stratus Cloud Hosting"


def test_codes_are_unique_and_required(books: Session) -> None:
    with pytest.raises(DuplicateCodeError):
        create_counterparty(books, code="V-STRATUS", name="Again", kind="VENDOR")
    with pytest.raises(ValueError, match="must not be empty"):
        create_counterparty(books, code=" ", name="Nobody", kind="VENDOR")
    with pytest.raises(UnknownCounterpartyError):
        get_counterparty(books, "V-NOBODY")


@pytest.mark.parametrize(
    ("object_type", "code"),
    [
        ("vendor_bill", "C-HELIO"),
        ("vendor_payment", "C-HELIO"),
        ("purchase_order", "C-HELIO"),
        ("customer_invoice", "V-PAPER"),
        ("customer_payment", "V-PAPER"),
        ("sales_order", "V-PAPER"),
    ],
)
def test_an_object_names_the_kind_of_counterparty_its_type_calls_for(
    books: Session, object_type: str, code: str
) -> None:
    with pytest.raises(CounterpartyKindError):
        create_accounting_object(
            books,
            object_type=object_type,
            occurred_at=RECEIVED,
            source="email",
            counterparty=code,
        )


@pytest.mark.parametrize("code", ["V-PAPER", "C-HELIO"])
def test_an_expense_or_a_contract_may_name_either_kind(
    books: Session, code: str
) -> None:
    for object_type in ("expense", "contract", "bank_transaction"):
        obj = create_accounting_object(
            books,
            object_type=object_type,
            occurred_at=RECEIVED,
            source="card",
            counterparty=code,
        )
        assert obj.counterparty is not None and obj.counterparty.code == code


def test_a_retired_counterparty_cannot_be_named_again(books: Session) -> None:
    earlier = create_accounting_object(
        books,
        object_type="vendor_bill",
        occurred_at=RECEIVED,
        source="email",
        counterparty="V-SUMMIT",
    )
    summit = get_counterparty(books, "V-SUMMIT")
    deactivate_counterparty(summit)
    books.commit()

    with pytest.raises(InactiveCounterpartyError):
        create_accounting_object(
            books,
            object_type="vendor_bill",
            occurred_at=RECEIVED,
            source="email",
            counterparty="V-SUMMIT",
        )
    assert earlier.counterparty is summit  # objects that name it keep it

    activate_counterparty(summit)
    create_accounting_object(
        books,
        object_type="vendor_bill",
        occurred_at=RECEIVED,
        source="email",
        counterparty="V-SUMMIT",
    )
    assert len(search_accounting_objects(books, counterparty="V-SUMMIT")) == 2
