"""Accounting objects, their JSON business data, and their business events."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Executable, delete, insert, text, update
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from opensumma.objects import (
    AccountingEvent,
    AccountingObject,
    AccountingObjectEntry,
    AccountingObjectStatus,
    AccountingObjectType,
    ImmutableRecordError,
    UnknownAccountingObjectError,
    create_accounting_object,
    get_accounting_object,
    record_accounting_event,
    search_accounting_objects,
)

MARCH_15 = datetime(2026, 3, 15, 9, 30, tzinfo=UTC)


def _bill(
    session: Session,
    invoice: str = "INV-1001",
    *,
    vendor: str = "V-AWS",
    at: datetime = MARCH_15,
    source: str = "email",
) -> AccountingObject:
    return create_accounting_object(
        session,
        object_type=AccountingObjectType.VENDOR_BILL,
        occurred_at=at,
        source=source,
        entity_id=vendor,
        data={"invoice_number": invoice, "amount": "1200.00"},
    )


def test_an_object_is_recorded_with_every_specified_field(session: Session) -> None:
    data = {
        "invoice_number": "INV-1001",
        "amount": "1200.00",
        "due": "2026-04-14",
        "lines": [{"description": "EC2", "amount": "1200.00"}],
    }
    obj = create_accounting_object(
        session,
        object_type=AccountingObjectType.VENDOR_BILL,
        occurred_at=MARCH_15,
        source="email",
        entity_id="V-AWS",
        data=data,
    )
    session.commit()
    session.expire_all()

    stored = get_accounting_object(session, obj.id)
    assert stored.object_type is AccountingObjectType.VENDOR_BILL
    assert stored.status is AccountingObjectStatus.OBSERVED
    assert stored.occurred_at == MARCH_15
    assert stored.occurred_at.tzinfo is UTC
    assert stored.source == "email"
    assert stored.entity_id == "V-AWS"
    assert stored.data == data
    assert stored.created_at.tzinfo is UTC
    assert stored.updated_at.tzinfo is UTC


def test_every_specified_object_type_exists() -> None:
    assert {member.value for member in AccountingObjectType} == {
        "vendor_bill",
        "customer_invoice",
        "customer_payment",
        "vendor_payment",
        "bank_transaction",
        "expense",
        "purchase_order",
        "sales_order",
        "contract",
        "journal_entry",
        "reconciliation",
    }


def test_an_object_type_may_be_given_by_name(session: Session) -> None:
    obj = create_accounting_object(
        session, object_type="customer_invoice", occurred_at=MARCH_15, source="erp"
    )
    assert obj.object_type is AccountingObjectType.CUSTOMER_INVOICE

    with pytest.raises(ValueError, match="invoice"):
        create_accounting_object(
            session, object_type="invoice", occurred_at=MARCH_15, source="erp"
        )


def test_occurred_at_is_an_aware_timestamp_stored_in_utc(session: Session) -> None:
    eastern = timezone(timedelta(hours=-5))
    obj = create_accounting_object(
        session,
        object_type="bank_transaction",
        occurred_at=datetime(2026, 3, 15, 4, 30, tzinfo=eastern),
        source="bank_feed",
    )
    assert obj.occurred_at == MARCH_15

    with pytest.raises(ValueError, match="naive"):
        create_accounting_object(
            session,
            object_type="bank_transaction",
            occurred_at=datetime(2026, 3, 15, 9, 30),  # noqa: DTZ001
            source="bank_feed",
        )


def test_a_source_is_required_and_a_counterparty_is_optional(session: Session) -> None:
    fee = create_accounting_object(
        session, object_type="bank_transaction", occurred_at=MARCH_15, source="bank"
    )
    assert fee.entity_id is None
    assert fee.data == {}

    for source in ("", "   ", "x" * 101):
        with pytest.raises(ValueError, match="source"):
            create_accounting_object(
                session, object_type="expense", occurred_at=MARCH_15, source=source
            )
    with pytest.raises(ValueError, match="entity_id"):
        create_accounting_object(
            session,
            object_type="expense",
            occurred_at=MARCH_15,
            source="card",
            entity_id=" ",
        )


def test_business_data_is_stored_as_json_without_floats(session: Session) -> None:
    obj = _bill(session)
    session.commit()

    raw: str = session.execute(
        text("SELECT data FROM accounting_object WHERE id = :id"), {"id": obj.id}
    ).scalar_one()
    assert '"amount": "1200.00"' in raw


def test_floats_cannot_reach_business_data_by_any_orm_path(session: Session) -> None:
    obj = _bill(session)
    session.commit()

    obj.data = {"amount": 1200.0}
    with pytest.raises(StatementError, match="write amounts as strings"):
        session.flush()
    session.rollback()

    session.add(
        AccountingObject(
            object_type=AccountingObjectType.EXPENSE,
            occurred_at=MARCH_15,
            source="card",
            data={"amount": 12.5},
        )
    )
    with pytest.raises(StatementError, match="write amounts as strings"):
        session.flush()


def test_data_is_copied_so_later_changes_by_the_caller_do_not_leak(
    session: Session,
) -> None:
    data = {"invoice_number": "INV-1"}
    obj = create_accounting_object(
        session,
        object_type="vendor_bill",
        occurred_at=MARCH_15,
        source="email",
        data=data,
    )
    data["invoice_number"] = "INV-2"
    assert obj.data == {"invoice_number": "INV-1"}


def test_objects_are_found_by_their_fields(session: Session) -> None:
    march_1 = datetime(2026, 3, 1, tzinfo=UTC)
    early = _bill(session, "INV-1", at=march_1)
    late = _bill(session, "INV-2", vendor="V-RENT", source="portal")
    invoice = create_accounting_object(
        session, object_type="customer_invoice", occurred_at=MARCH_15, source="erp"
    )
    session.commit()

    # By occurrence, then id: ``late`` and ``invoice`` share a timestamp.
    assert search_accounting_objects(session) == [early, late, invoice]
    assert search_accounting_objects(session, object_type="vendor_bill") == [
        early,
        late,
    ]
    assert search_accounting_objects(session, entity_id="V-RENT") == [late]
    assert search_accounting_objects(session, source="portal") == [late]
    assert search_accounting_objects(session, status="VOIDED") == []
    assert search_accounting_objects(
        session, occurred_from=march_1, occurred_to=march_1
    ) == [early]
    assert search_accounting_objects(session, occurred_from=MARCH_15) == [
        late,
        invoice,
    ]


def test_objects_are_found_by_business_data_fields(session: Session) -> None:
    first = _bill(session, "INV-1001")
    duplicate = _bill(session, "INV-1001", at=MARCH_15 + timedelta(days=2))
    _bill(session, "INV-1002")
    session.commit()

    assert search_accounting_objects(
        session, entity_id="V-AWS", data={"invoice_number": "INV-1001"}
    ) == [first, duplicate]
    assert search_accounting_objects(session, data={"missing": "x"}) == []
    with pytest.raises(TypeError, match="string fields"):
        search_accounting_objects(session, data={"quantity": 3})  # type: ignore[dict-item]


def test_a_time_range_must_run_forwards(session: Session) -> None:
    with pytest.raises(ValueError, match="cannot start"):
        search_accounting_objects(
            session, occurred_from=MARCH_15, occurred_to=MARCH_15 - timedelta(days=1)
        )


def test_an_unknown_object_id_is_reported(session: Session) -> None:
    with pytest.raises(UnknownAccountingObjectError):
        get_accounting_object(session, 404)


def test_business_events_are_recorded_and_kept_in_business_time_order(
    session: Session,
) -> None:
    order = create_accounting_object(
        session, object_type="purchase_order", occurred_at=MARCH_15, source="erp"
    )
    received = record_accounting_event(
        session,
        order,
        event_type="goods_received",
        occurred_at=MARCH_15 + timedelta(days=10),
        source="warehouse",
        data={"quantity": 40, "receipt": "GR-7"},
    )
    approved = record_accounting_event(
        session,
        order,
        event_type="approved",
        occurred_at=MARCH_15 + timedelta(hours=2),
        source="erp",
    )
    session.commit()
    session.expire_all()

    assert [event.event_type for event in order.events] == [
        "approved",
        "goods_received",
    ]
    assert received.data == {"quantity": 40, "receipt": "GR-7"}
    assert approved.data == {}
    assert received.occurred_at.tzinfo is UTC
    assert received.created_at.tzinfo is UTC
    assert received.accounting_object is order


@pytest.mark.parametrize(
    "event_type", ["", "Goods Received", "goods-received", "1st_payment", "x" * 65]
)
def test_event_types_are_lowercase_snake_case(
    session: Session, event_type: str
) -> None:
    order = create_accounting_object(
        session, object_type="purchase_order", occurred_at=MARCH_15, source="erp"
    )
    with pytest.raises(ValueError, match=r"event_type|snake_case"):
        record_accounting_event(
            session,
            order,
            event_type=event_type,
            occurred_at=MARCH_15,
            source="erp",
        )


def test_business_events_are_never_changed_or_deleted(session: Session) -> None:
    order = create_accounting_object(
        session, object_type="purchase_order", occurred_at=MARCH_15, source="erp"
    )
    recorded = record_accounting_event(
        session, order, event_type="issued", occurred_at=MARCH_15, source="erp"
    )
    session.commit()

    recorded.event_type = "cancelled"
    with pytest.raises(ImmutableRecordError, match="record a later event"):
        session.flush()
    session.rollback()

    session.delete(recorded)
    with pytest.raises(ImmutableRecordError, match="cannot be deleted"):
        session.flush()


def test_objects_are_never_deleted(session: Session) -> None:
    obj = _bill(session)
    session.commit()

    session.delete(obj)
    with pytest.raises(ImmutableRecordError, match="void it instead"):
        session.flush()


def test_an_object_that_still_stands_can_be_updated(session: Session) -> None:
    obj = _bill(session)
    session.commit()
    created, updated = obj.created_at, obj.updated_at

    obj.data = {"invoice_number": "INV-1001", "amount": "1250.00"}
    session.commit()

    assert obj.data["amount"] == "1250.00"
    assert obj.created_at == created
    assert obj.updated_at > updated


BulkWrite = Callable[[int], Executable]

BULK_WRITES: dict[str, BulkWrite] = {
    "insert object": lambda _: insert(AccountingObject).values(
        object_type="expense", occurred_at=MARCH_15, source="card", data={}
    ),
    "update object": lambda _: update(AccountingObject).values(source="forged"),
    "delete object": lambda _: delete(AccountingObject),
    "insert event": lambda object_id: insert(AccountingEvent).values(
        accounting_object_id=object_id,
        event_type="forged",
        occurred_at=MARCH_15,
        source="x",
        data={},
    ),
    "update event": lambda _: update(AccountingEvent).values(event_type="forged"),
    "delete event": lambda _: delete(AccountingEvent),
    "delete link": lambda _: delete(AccountingObjectEntry),
}


@pytest.mark.parametrize("write", BULK_WRITES.values(), ids=BULK_WRITES.keys())
def test_bulk_writes_to_object_tables_are_refused(
    session: Session, write: BulkWrite
) -> None:
    obj = _bill(session)
    session.commit()

    with pytest.raises(ImmutableRecordError, match="bulk writes"):
        session.execute(write(obj.id))
