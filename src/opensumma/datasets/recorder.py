"""Recording a plan into the books, through the kernel and the object layer.

The generator is trusted code, like any code that calls these layers directly: it
records objects and posts entries without the workflow's actors and approvals, so
that it can record the very mistakes the workflow's controls exist to catch. Every
entry is still created, validated, and posted by the kernel, and every flush passes
the session hooks that guard the ledger and object history.

Autoflush is suspended while recording, and the session is flushed in batches
instead, because flushing before every lookup would run those hooks thousands of
times. Master data is flushed first, so every lookup finds what it needs.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from opensumma.datasets.business import bank_document
from opensumma.datasets.model import (
    BankLine,
    Document,
    Plan,
    StatementLine,
    Transaction,
)
from opensumma.db import Base
from opensumma.kernel import (
    JournalEntry,
    LineInput,
    create_calendar_year_periods,
    create_journal_entry,
    post_journal_entry,
    reverse_journal_entry,
    seed_chart_of_accounts,
    seed_dimensions,
)
from opensumma.objects import (
    AccountingObject,
    AccountingObjectStatus,
    create_accounting_object,
    create_counterparty,
    create_journal_entry_for_object,
    seed_counterparties,
)

FLUSH_EVERY = 500


@dataclass(frozen=True)
class RecordedIds:
    """Where each planned item was recorded, by plan key: its journal entry, its
    document's accounting object, and its bank statement line's object."""

    entries: Mapping[str, int]
    documents: Mapping[str, int]
    bank: Mapping[str, int]


def record_plan(session: Session, plan: Plan) -> RecordedIds:
    """Record ``plan`` into empty books and return where each item went."""
    seed_chart_of_accounts(session)
    seed_dimensions(session)
    seed_counterparties(session)
    for counterparty in plan.counterparties:
        create_counterparty(
            session,
            code=counterparty.code,
            name=counterparty.name,
            kind=counterparty.kind,
        )
    create_calendar_year_periods(session, plan.year)
    session.flush()

    recorder = _Recorder(session, plan)
    with session.no_autoflush:
        for count, item in enumerate(plan.in_order(), start=1):
            if isinstance(item, StatementLine):
                recorder.statement_line(item.key, item.bank_line)
            else:
                recorder.transaction(item)
            if count % FLUSH_EVERY == 0:
                recorder.flush()
        recorder.flush()
    return RecordedIds(recorder.entries, recorder.documents, recorder.bank)


class _Recorder:
    def __init__(self, session: Session, plan: Plan) -> None:
        self.session = session
        self.entries: dict[str, int] = {}
        self.documents: dict[str, int] = {}
        self.bank: dict[str, int] = {}
        self.pending: list[tuple[dict[str, int], str, Base]] = []
        self.reversed_later = {t.reverses for t in plan.transactions if t.reverses}
        self.reversible: dict[str, JournalEntry] = {}

    def flush(self) -> None:
        """Flush, and note the ids of what the flush wrote. Only ids are kept, so
        recorded rows can leave the session's memory."""
        self.session.flush()
        for ids, key, record in self.pending:
            ids[key] = record.id  # type: ignore[attr-defined]
        self.pending.clear()

    def transaction(self, transaction: Transaction) -> None:
        document = None
        if transaction.document is not None:
            document = self.document(transaction.document)
            self.pending.append((self.documents, transaction.key, document))

        if transaction.reverses is not None:
            entry = reverse_journal_entry(
                self.session,
                self.reversible.pop(transaction.reverses),
                entry_date=transaction.entry_date,
                description=transaction.description,
            )
        else:
            lines = [
                LineInput(line.account, line.amount, dimensions=dict(line.dimensions))
                for line in transaction.lines
            ]
            if document is None:
                entry = create_journal_entry(
                    self.session,
                    entry_date=transaction.entry_date,
                    description=transaction.description,
                    lines=lines,
                )
            else:
                entry = create_journal_entry_for_object(
                    self.session,
                    document,
                    entry_date=transaction.entry_date,
                    description=transaction.description,
                    lines=lines,
                )
            post_journal_entry(self.session, entry)
        if transaction.key in self.reversed_later:
            self.reversible[transaction.key] = entry
        self.pending.append((self.entries, transaction.key, entry))

        if transaction.bank_line is not None:
            self.statement_line(transaction.key, transaction.bank_line)

    def statement_line(self, key: str, line: BankLine) -> None:
        """A line of the bank feed: imported and read, but matched to nothing yet."""
        record = self.document(bank_document(line, AccountingObjectStatus.EXTRACTED))
        self.pending.append((self.bank, key, record))

    def document(self, document: Document) -> AccountingObject:
        record = create_accounting_object(
            self.session,
            object_type=document.object_type,
            occurred_at=document.occurred_at,
            source=document.source,
            counterparty=document.counterparty,
            data=document.data,
        )
        # The documents of past business were processed before they reached these
        # books: read, and their vendor or customer settled.
        record.status = document.status
        return record
