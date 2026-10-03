"""The books' content, and a fingerprint of it that shows two datasets are the same.

A dataset is reproducible in what it records, not byte for byte: SQLite files, and
the timestamps of when rows were written or posted, differ from run to run. The
fingerprint is a SHA-256 hash of everything else, in a canonical order: the chart
of accounts, dimensions, counterparties, periods, journal entries and their lines,
and accounting objects with their data, events, and links. The audit log and the
actors are left out, since they record who ran the generator and when.
"""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from opensumma.kernel import (
    Account,
    AccountingPeriod,
    Dimension,
    JournalEntry,
    JournalLine,
    JournalLineDimension,
)
from opensumma.objects import AccountingObject, Counterparty


def export_books(session: Session) -> dict[str, Any]:
    """The books' content as JSON values, each list in a stable order."""
    session.flush()
    accounts = session.scalars(select(Account).order_by(Account.code))
    dimensions = session.scalars(
        select(Dimension)
        .options(selectinload(Dimension.values))
        .order_by(Dimension.code)
    )
    entries = session.scalars(
        select(JournalEntry)
        .options(
            selectinload(JournalEntry.lines).selectinload(JournalLine.account),
            selectinload(JournalEntry.lines)
            .selectinload(JournalLine.dimensions)
            .selectinload(JournalLineDimension.value),
        )
        .order_by(JournalEntry.id)
    )
    objects = session.scalars(
        select(AccountingObject)
        .options(
            selectinload(AccountingObject.counterparty),
            selectinload(AccountingObject.events),
            selectinload(AccountingObject.entry_links),
        )
        .order_by(AccountingObject.id)
    )
    return {
        "accounts": [
            {
                "code": account.code,
                "name": account.name,
                "account_type": account.account_type.value,
                "normal_balance": account.normal_balance.value,
                "parent": None if account.parent is None else account.parent.code,
                "is_active": account.is_active,
            }
            for account in accounts
        ],
        "dimensions": [
            {
                "code": dimension.code,
                "name": dimension.name,
                "values": sorted(
                    (
                        {"code": v.code, "name": v.name, "is_active": v.is_active}
                        for v in dimension.values
                    ),
                    key=lambda value: str(value["code"]),
                ),
            }
            for dimension in dimensions
        ],
        "counterparties": [
            {
                "code": c.code,
                "name": c.name,
                "kind": c.kind.value,
                "is_active": c.is_active,
            }
            for c in session.scalars(select(Counterparty).order_by(Counterparty.code))
        ],
        "periods": [
            {
                "code": p.code,
                "start_date": p.start_date.isoformat(),
                "end_date": p.end_date.isoformat(),
                "status": p.status.value,
            }
            for p in session.scalars(
                select(AccountingPeriod).order_by(AccountingPeriod.start_date)
            )
        ],
        "journal_entries": [
            {
                "id": entry.id,
                "entry_date": entry.entry_date.isoformat(),
                "description": entry.description,
                "status": entry.status.value,
                "reversal_of": entry.reversal_of_id,
                "lines": [
                    {
                        "line_number": line.line_number,
                        "account": line.account.code,
                        "amount": str(line.amount),
                        "memo": line.memo,
                        "dimensions": {
                            tag.value.dimension.code: tag.value.code
                            for tag in line.dimensions
                        },
                    }
                    for line in entry.lines
                ],
            }
            for entry in entries
        ],
        "accounting_objects": [
            {
                "id": obj.id,
                "object_type": obj.object_type.value,
                "status": obj.status.value,
                "occurred_at": obj.occurred_at.isoformat(),
                "source": obj.source,
                "counterparty": None
                if obj.counterparty is None
                else obj.counterparty.code,
                "data": obj.data,
                "events": [
                    {
                        "event_type": event.event_type,
                        "occurred_at": event.occurred_at.isoformat(),
                        "source": event.source,
                        "data": event.data,
                    }
                    for event in obj.events
                ],
                "journal_entry_ids": sorted(
                    link.journal_entry_id for link in obj.entry_links
                ),
            }
            for obj in objects
        ],
    }


def books_fingerprint(session: Session) -> str:
    """``sha256:`` and the hash of the books' content; equal books, equal hashes."""
    canonical = json.dumps(
        export_books(session), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return "sha256:" + hashlib.sha256(canonical.encode("ascii")).hexdigest()
