"""Deliberate accounting errors, and the ground truth that describes each.

``inject_errors`` takes a clean plan and returns the plan with errors in it, and a
record of every error: the journal entries as the books hold them (``actual``) and
as they should (``expected``), and details such as the account posted and the one
that was correct. A clean plan and its errored copy differ by exactly those
entries, so the ground truth can be checked against both.

Errors draw from their own random stream, so the business beneath them is the same
for a seed whether or not errors are injected. Each transaction takes part in at
most one error, together with the documents it settles or is settled by, so every
error can be read, and corrected, on its own.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from opensumma.datasets import cast
from opensumma.datasets.business import (
    key,
    money,
    month_end,
    next_business_day,
    period_code,
    previous_business_day,
    reference,
    text,
)
from opensumma.datasets.model import (
    OPERATING_ACCOUNT,
    BankLine,
    Document,
    Line,
    Plan,
    StatementLine,
    Transaction,
    dims,
    net_by_account,
)
from opensumma.datasets.rng import Rng
from opensumma.money import ZERO
from opensumma.objects import AccountingObjectStatus, AccountingObjectType

ERROR_TYPES: tuple[str, ...] = (
    "duplicate_invoice",
    "wrong_gl_account",
    "wrong_department",
    "wrong_location",
    "wrong_accounting_period",
    "missing_accrual",
    "duplicate_payment",
    "unreconciled_transaction",
    "unusual_transaction",
    "incorrect_amount",
    "missing_vendor",
)

# Accounts easily confused for one another, each with the one it is mistaken for.
WRONG_ACCOUNTS = {"5200": "6100", "6100": "5200", "6500": "6700", "6700": "6500"}
_DEPARTMENTS = ("ENG", "GA", "MKT", "SALES")
_LOCATIONS = ("EAST", "HQ", "WEST")
_SOURCES = ("email", "vendor_portal", "mail")
# Keys of transactions an error adds are numbered from here, apart from the plan's.
_ADDED_FROM = 900_001


@dataclass(frozen=True)
class Ref:
    """A record in the books, named by the plan key it was recorded from: the
    journal ``entry``, its ``document``, or its ``bank`` statement line."""

    kind: str
    key: str


@dataclass(frozen=True)
class InjectedError:
    """One deliberate error and what the books should hold instead."""

    error_type: str
    summary: str
    actual: tuple[Transaction, ...]
    expected: tuple[Transaction, ...]
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def as_of(self) -> date:
        """The end of the first period the error misstates."""
        first = min(t.entry_date for t in (*self.actual, *self.expected))
        return month_end(first.year, first.month)

    def misstatement(self) -> dict[str, Decimal]:
        """How far each account is off at ``as_of``, debits positive."""
        actual = net_by_account(self.actual, through=self.as_of)
        expected = net_by_account(self.expected, through=self.as_of)
        return {
            account: difference
            for account in sorted({*actual, *expected})
            if (difference := actual.get(account, ZERO) - expected.get(account, ZERO))
            != ZERO
        }


def default_error_count(transactions: int) -> int:
    """One error per hundred transactions, and at least one of each type."""
    return max(len(ERROR_TYPES), transactions // 100)


def inject_errors(
    plan: Plan, *, seed: int, count: int
) -> tuple[Plan, list[InjectedError]]:
    """``plan`` with ``count`` errors in it, spread over every type in turn, and
    the record of each, ordered by the period it misstates."""
    if count < 0:
        raise ValueError(f"cannot inject {count} errors")
    injector = _Injector(plan, seed)
    for error_type in injector.allocate(count):
        injector.inject(error_type)
    return injector.result()


class _Injector:
    def __init__(self, plan: Plan, seed: int) -> None:
        self.plan = plan
        self.rng = Rng(seed, "errors")
        self.by_key = {t.key: t for t in plan.transactions}
        self.replaced: dict[str, Transaction] = {}
        self.removed: set[str] = set()
        self.added: list[Transaction] = []
        self.statement_lines = list(plan.statement_lines)
        self.used: set[str] = set()
        self.errors: list[InjectedError] = []
        self.by_invoice: dict[str, list[str]] = {}
        for t in plan.transactions:
            number = t.document.data.get("invoice_number") if t.document else None
            if isinstance(number, str):
                self.by_invoice.setdefault(number, []).append(t.key)
        refunded = {
            t.document.data["invoice_number"]
            for t in plan.transactions
            if t.kind == "refund" and t.document is not None
        }
        bills = [t for t in plan.transactions if t.kind == "vendor_bill"]
        costs = [
            t for t in plan.transactions if t.kind in ("vendor_bill", "card_expense")
        ]
        self.candidates: dict[str, list[str]] = {
            "duplicate_invoice": [
                t.key for t in bills if t.entry_date <= date(plan.year, 12, 15)
            ],
            "wrong_gl_account": [
                t.key for t in costs if t.lines[0].account in WRONG_ACCOUNTS
            ],
            "wrong_department": [
                t.key for t in costs if t.lines[0].dimension("DEPARTMENT")
            ],
            "wrong_location": [
                t.key for t in costs if t.lines[0].dimension("LOCATION")
            ],
            "wrong_accounting_period": [
                t.key for t in bills if _shiftable(t.entry_date)
            ],
            "missing_accrual": [
                t.key for t in plan.transactions if t.kind == "accrual"
            ],
            "duplicate_payment": [
                t.key
                for t in plan.transactions
                if t.kind == "vendor_payment"
                and t.entry_date <= date(plan.year, 12, 10)
            ],
            "unreconciled_transaction": [
                t.key
                for t in plan.transactions
                if t.kind == "customer_payment"
                and t.document is not None
                and t.document.data["invoice_number"] not in refunded
            ],
            "unusual_transaction": [
                day.isoformat()
                for day in (date(plan.year, 1, 1) + timedelta(n) for n in range(365))
                if day.year == plan.year and day.weekday() >= 5
            ],
            "incorrect_amount": [t.key for t in bills if _transpositions(t.amount)],
            "missing_vendor": [t.key for t in bills],
        }

    # --- Choosing -----------------------------------------------------------------

    def allocate(self, count: int) -> list[str]:
        """The type of each error, taking the types in turn. No type takes more
        than a quarter of its candidates, so errors stay the exception."""
        capacity = {t: max(1, len(self.candidates[t]) // 4) for t in ERROR_TYPES}
        capacity = {t: c if self.candidates[t] else 0 for t, c in capacity.items()}
        total = sum(capacity.values())
        if count > total:
            raise ValueError(
                f"at most {total} errors fit in a dataset of "
                f"{len(self.plan.transactions)} transactions, not {count}"
            )
        allocated: list[str] = []
        while len(allocated) < count:
            for error_type in ERROR_TYPES:
                if len(allocated) < count and capacity[error_type] > 0:
                    capacity[error_type] -= 1
                    allocated.append(error_type)
        return allocated

    def pick(self, error_type: str) -> str:
        available = [k for k in self.candidates[error_type] if k not in self.used]
        if not available:
            raise ValueError(
                f"no transaction is left for another {error_type} error; "
                "ask for fewer errors or more transactions"
            )
        chosen = self.rng.choice(available)
        self.used.add(chosen)
        return chosen

    def claim(self, transaction_key: str) -> Transaction:
        """Take a transaction for an error, with the documents it is settled by or
        settles, so no other error touches them."""
        transaction = self.by_key[transaction_key]
        number = (
            transaction.document.data.get("invoice_number")
            if transaction.document
            else None
        )
        if isinstance(number, str):
            self.used.update(self.by_invoice.get(number, ()))
        self.used.add(transaction_key)
        return transaction

    def inject(self, error_type: str) -> None:
        handlers: dict[str, Callable[[], None]] = {
            "duplicate_invoice": self._duplicate_invoice,
            "wrong_gl_account": self._wrong_gl_account,
            "wrong_department": lambda: self._wrong_dimension(
                "DEPARTMENT", _DEPARTMENTS
            ),
            "wrong_location": lambda: self._wrong_dimension("LOCATION", _LOCATIONS),
            "wrong_accounting_period": self._wrong_accounting_period,
            "missing_accrual": self._missing_accrual,
            "duplicate_payment": self._duplicate_payment,
            "unreconciled_transaction": self._unreconciled_transaction,
            "unusual_transaction": self._unusual_transaction,
            "incorrect_amount": self._incorrect_amount,
            "missing_vendor": self._missing_vendor,
        }
        handlers[error_type]()

    def result(self) -> tuple[Plan, list[InjectedError]]:
        transactions = [
            self.replaced.get(t.key, t)
            for t in self.plan.transactions
            if t.key not in self.removed
        ]
        plan = replace(
            self.plan,
            transactions=tuple(
                sorted(
                    [*transactions, *self.added], key=lambda t: (t.entry_date, t.key)
                )
            ),
            statement_lines=tuple(
                sorted(
                    self.statement_lines, key=lambda s: (s.bank_line.posted_on, s.key)
                )
            ),
        )
        errors = sorted(
            self.errors,
            key=lambda e: (e.as_of, ERROR_TYPES.index(e.error_type), _first_key(e)),
        )
        return plan, errors

    def _add(self, transaction: Transaction) -> None:
        self.added.append(transaction)

    def _next_number(self) -> int:
        return _ADDED_FROM + len(self.added)

    def _record(
        self,
        error_type: str,
        summary: str,
        actual: Sequence[Transaction],
        expected: Sequence[Transaction],
        **details: Any,
    ) -> None:
        self.errors.append(
            InjectedError(error_type, summary, tuple(actual), tuple(expected), details)
        )

    # --- The errors -----------------------------------------------------------------

    def _duplicate_invoice(self) -> None:
        bill = self.claim(self.pick("duplicate_invoice"))
        document = _document(bill)
        received = document.occurred_at + timedelta(days=self.rng.integer(2, 9))
        duplicate = replace(
            bill,
            key=key(self._next_number()),
            document=replace(
                document,
                occurred_at=received,
                source=self.rng.choice([s for s in _SOURCES if s != document.source]),
            ),
        )
        self._add(duplicate)
        data = document.data
        self._record(
            "duplicate_invoice",
            f"Bill {data['invoice_number']} from {data['vendor_name']} was received "
            "twice and recorded both times.",
            [duplicate],
            [],
            invoice_number=data["invoice_number"],
            vendor=document.counterparty,
            original_journal_entry_id=Ref("entry", bill.key),
            original_accounting_object_id=Ref("document", bill.key),
            duplicate_journal_entry_id=Ref("entry", duplicate.key),
            duplicate_accounting_object_id=Ref("document", duplicate.key),
        )

    def _wrong_gl_account(self) -> None:
        original = self.claim(self.pick("wrong_gl_account"))
        correct = original.lines[0].account
        wrong = WRONG_ACCOUNTS[correct]
        posted = replace(
            original,
            lines=(replace(original.lines[0], account=wrong), *original.lines[1:]),
        )
        self.replaced[original.key] = posted
        self._record(
            "wrong_gl_account",
            f"{original.description} was posted to account {wrong} instead of "
            f"{correct}.",
            [posted],
            [original],
            journal_entry_id=Ref("entry", original.key),
            accounting_object_id=Ref("document", original.key),
            line_number=1,
            posted_account=wrong,
            correct_account=correct,
        )

    def _wrong_dimension(self, dimension: str, values: Sequence[str]) -> None:
        error_type = f"wrong_{dimension.lower()}"
        original = self.claim(self.pick(error_type))
        line = original.lines[0]
        correct = line.dimension(dimension)
        assert correct is not None
        wrong = self.rng.choice([v for v in values if v != correct])
        tags = {**dict(line.dimensions), dimension: wrong}
        posted = replace(
            original,
            lines=(replace(line, dimensions=dims(**tags)), *original.lines[1:]),
        )
        self.replaced[original.key] = posted
        name = dimension.lower()
        self._record(
            error_type,
            f"{original.description} was tagged with {name} {wrong} instead of "
            f"{correct}.",
            [posted],
            [original],
            journal_entry_id=Ref("entry", original.key),
            accounting_object_id=Ref("document", original.key),
            line_number=1,
            **{f"posted_{name}": wrong, f"correct_{name}": correct},
        )

    def _wrong_accounting_period(self) -> None:
        original = self.claim(self.pick("wrong_accounting_period"))
        day = original.entry_date
        shift = self.rng.integer(0, 3)
        if day.day >= 21:  # recorded in the next period
            first = date(day.year, day.month + 1, 1)
            posted_on = next_business_day(first + timedelta(days=shift))
        else:  # recorded in the previous period
            posted_on = previous_business_day(
                date(day.year, day.month, 1) - timedelta(days=1 + shift)
            )
        posted = replace(original, entry_date=posted_on)
        self.replaced[original.key] = posted
        self._record(
            "wrong_accounting_period",
            f"{original.description}, dated {day.isoformat()}, was recorded on "
            f"{posted_on.isoformat()}, in period {period_code(posted_on)} instead of "
            f"{period_code(day)}.",
            [posted],
            [original],
            journal_entry_id=Ref("entry", original.key),
            accounting_object_id=Ref("document", original.key),
            posted_date=posted_on.isoformat(),
            correct_date=day.isoformat(),
            posted_period=period_code(posted_on),
            correct_period=period_code(day),
        )

    def _missing_accrual(self) -> None:
        accrual = self.claim(self.pick("missing_accrual"))
        reversals = [t for t in self.plan.transactions if t.reverses == accrual.key]
        omitted = [accrual, *reversals]
        self.removed.update(t.key for t in omitted)
        self.used.update(t.key for t in omitted)
        data = _document(accrual).data
        self._record(
            "missing_accrual",
            f"{data['vendor_name']}'s fees for {data['service_period']} were not "
            f"accrued at the end of the period.",
            [],
            omitted,
            period=data["service_period"],
            vendor=cast.LEGAL.code,
            vendor_name=data["vendor_name"],
            amount=data["estimate"],
            accrual_date=accrual.entry_date.isoformat(),
            expense_account=accrual.lines[0].account,
            accrual_account=accrual.lines[1].account,
        )

    def _duplicate_payment(self) -> None:
        payment = self.claim(self.pick("duplicate_payment"))
        document = _document(payment)
        number = self._next_number()
        day = next_business_day(
            payment.entry_date + timedelta(days=self.rng.integer(7, 14))
        )
        assert payment.bank_line is not None
        duplicate = replace(
            payment,
            key=key(number),
            entry_date=day,
            document=replace(
                document,
                occurred_at=_moved(document.occurred_at, day),
                data={
                    **document.data,
                    "payment_number": f"PAY-{reference('PAY', number)}",
                    "paid_on": day.isoformat(),
                },
            ),
            bank_line=replace(
                payment.bank_line,
                posted_on=min(
                    next_business_day(day + timedelta(days=self.rng.integer(0, 2))),
                    date(day.year, 12, 31),
                ),
                reference=reference("BNK", number),
            ),
        )
        self._add(duplicate)
        data = document.data
        self._record(
            "duplicate_payment",
            f"Bill {data['invoice_number']} from {data['vendor_name']} was paid twice.",
            [duplicate],
            [],
            invoice_number=data["invoice_number"],
            vendor=document.counterparty,
            amount=data["amount"],
            original_journal_entry_id=Ref("entry", payment.key),
            original_accounting_object_id=Ref("document", payment.key),
            duplicate_journal_entry_id=Ref("entry", duplicate.key),
            duplicate_accounting_object_id=Ref("document", duplicate.key),
            duplicate_bank_transaction_object_id=Ref("bank", duplicate.key),
        )

    def _unreconciled_transaction(self) -> None:
        payment = self.claim(self.pick("unreconciled_transaction"))
        assert payment.bank_line is not None
        self.removed.add(payment.key)
        self.statement_lines.append(StatementLine(payment.key, payment.bank_line))
        data = _document(payment).data
        self._record(
            "unreconciled_transaction",
            f"The bank received {data['amount']} from {data['customer_name']} on "
            f"{payment.bank_line.posted_on.isoformat()}, but the payment was never "
            "recorded.",
            [],
            [payment],
            bank_transaction_object_id=Ref("bank", payment.key),
            posted_on=payment.bank_line.posted_on.isoformat(),
            amount=str(payment.bank_line.amount),
            invoice_number=data["invoice_number"],
            customer=_document(payment).counterparty,
        )

    def _unusual_transaction(self) -> None:
        day = date.fromisoformat(self.pick("unusual_transaction"))
        merchant = self.rng.choice([m for m in cast.MERCHANTS if m.account == "6500"])
        usual = [
            t.amount
            for t in self.plan.transactions
            if t.kind == "card_expense"
            and t.document is not None
            and t.document.data["merchant"] == merchant.name
        ]
        typical_maximum = max(usual, default=money(merchant.high))
        # Many times the usual, rounded to whole hundreds, as such charges often are.
        factor = self.rng.integer(25, 60)
        amount = max(
            round(factor * cents_of(typical_maximum) / 10_000) * 10_000, 1_000_000
        )
        department = self.rng.choice(_DEPARTMENTS)
        location = self.rng.choice(_LOCATIONS)
        number = self._next_number()
        unusual = Transaction(
            key=key(number),
            kind="card_expense",
            entry_date=day,
            description=f"Card purchase - {merchant.name}",
            lines=(
                Line(
                    merchant.account,
                    money(amount),
                    dimensions=dims(DEPARTMENT=department, LOCATION=location),
                ),
                Line(OPERATING_ACCOUNT, -money(amount)),
            ),
            document=Document(
                object_type=AccountingObjectType.EXPENSE,
                occurred_at=datetime(
                    day.year,
                    day.month,
                    day.day,
                    self.rng.integer(0, 23),
                    self.rng.integer(0, 59),
                    tzinfo=UTC,
                ),
                source="expense_app",
                data={
                    "merchant": merchant.name,
                    "category": merchant.category,
                    "amount": text(amount),
                    "transaction_date": day.isoformat(),
                    "payment_method": "Company debit card ****4417",
                    "department": department,
                    "location": location,
                },
            ),
            bank_line=BankLine(
                posted_on=min(next_business_day(day), date(day.year, 12, 31)),
                amount=-money(amount),
                description=f"DEBIT CARD {merchant.name.upper()}",
                reference=reference("BNK", number),
            ),
        )
        self._add(unusual)
        self._record(
            "unusual_transaction",
            f"A charge of {text(amount)} at {merchant.name} on a "
            f"{day.strftime('%A')}, far above the {typical_maximum} the company "
            "usually spends there.",
            [unusual],
            [],
            journal_entry_id=Ref("entry", unusual.key),
            accounting_object_id=Ref("document", unusual.key),
            bank_transaction_object_id=Ref("bank", unusual.key),
            amount=text(amount),
            merchant=merchant.name,
            typical_maximum=str(typical_maximum),
            weekday=day.strftime("%A"),
        )

    def _incorrect_amount(self) -> None:
        original = self.claim(self.pick("incorrect_amount"))
        options = _transpositions(original.amount)
        posted_amount = options[self.rng.integer(0, len(options) - 1)]
        posted = replace(
            original,
            lines=tuple(
                replace(
                    line,
                    amount=posted_amount if line.amount > ZERO else -posted_amount,
                )
                for line in original.lines
            ),
        )
        self.replaced[original.key] = posted
        data = _document(original).data
        self._record(
            "incorrect_amount",
            f"Bill {data['invoice_number']} from {data['vendor_name']} for "
            f"{data['amount']} was recorded as {posted_amount}.",
            [posted],
            [original],
            journal_entry_id=Ref("entry", original.key),
            accounting_object_id=Ref("document", original.key),
            invoice_number=data["invoice_number"],
            posted_amount=str(posted_amount),
            correct_amount=data["amount"],
        )

    def _missing_vendor(self) -> None:
        original = self.claim(self.pick("missing_vendor"))
        document = _document(original)
        recorded = replace(
            original,
            document=replace(
                document,
                counterparty=None,
                status=AccountingObjectStatus.EXTRACTED,
            ),
        )
        self.replaced[original.key] = recorded
        self._record(
            "missing_vendor",
            f"Bill {document.data['invoice_number']} was recorded without naming "
            f"its vendor, {document.data['vendor_name']}.",
            [recorded],
            [original],
            journal_entry_id=Ref("entry", original.key),
            accounting_object_id=Ref("document", original.key),
            vendor=document.counterparty,
            vendor_name=document.data["vendor_name"],
        )


def _document(transaction: Transaction) -> Document:
    if transaction.document is None:
        raise AssertionError(f"{transaction.key} has no document")
    return transaction.document


def _shiftable(day: date) -> bool:
    """Whether a bill dated ``day`` can be misdated into a neighbouring period of
    the same year."""
    return (day.day >= 21 and day.month < 12) or (day.day <= 7 and day.month > 1)


def _transpositions(amount: Decimal) -> list[Decimal]:
    """The amounts ``amount`` becomes when two neighbouring digits are swapped,
    as a keying error does, leaving out those that start with a zero."""
    digits = str(cents_of(amount))
    found = []
    for i in range(len(digits) - 1):
        if digits[i] == digits[i + 1]:
            continue
        swapped = digits[:i] + digits[i + 1] + digits[i] + digits[i + 2 :]
        if swapped[0] != "0":
            found.append(money(int(swapped)))
    return found


def cents_of(amount: Decimal) -> int:
    return int(amount * 100)


def _moved(moment: datetime, day: date) -> datetime:
    return moment.replace(year=day.year, month=day.month, day=day.day)


def _first_key(error: InjectedError) -> str:
    return min(t.key for t in (*error.actual, *error.expected))
