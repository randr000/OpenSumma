"""The benchmark's tasks.

Sixteen tasks across the general ledger, payables, receivables, the close, and
journal entries. Every expected answer is derived from the dataset, never judged:

- **From the books:** an account balance, a trial balance, the invoices still
  unpaid. The expected answer is what the kernel reports for the books as
  recorded.
- **From the ground truth:** finding the errors the dataset generator injected,
  and naming each correction. These are scored by the F1 score of the items found,
  so a missed error and a false alarm cost alike, and, where an item names a
  correction, by how many of the errors found were also corrected rightly.
- **In the workflow:** proposing, validating, and correcting a journal entry that
  the task sets up through the workflow, as a clerk. The entry the agent leaves in
  the books is scored, line by line, together with its place in the workflow.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from opensumma import kernel, workflow
from opensumma.benchmark.environment import Environment
from opensumma.benchmark.schema import (
    CLASSIFICATION,
    NUMERICAL,
    WORKFLOW,
    Amount,
    Answer,
    Instance,
    Prepared,
    Score,
    Task,
    amount,
    dice,
)
from opensumma.benchmark.tools import Tools
from opensumma.datasets import cast
from opensumma.datasets.business import month_end, previous_business_day
from opensumma.kernel import JournalEntry, JournalEntryStatus, LineInput
from opensumma.objects import (
    AccountingObject,
    objects_for_journal_entry,
    search_accounting_objects,
)
from opensumma.workflow import audit_history, workflow_history

GENERAL_LEDGER = "general_ledger"
PAYABLES = "accounts_payable"
RECEIVABLES = "accounts_receivable"
CLOSE = "close"
JOURNAL_ENTRIES = "journal_entries"

# --- Answers --------------------------------------------------------------------------


class BalanceAnswer(Answer):
    balance: Amount


class TrialBalanceLine(BaseModel):
    account: str
    debit: Amount = "0.00"
    credit: Amount = "0.00"


class TrialBalanceAnswer(Answer):
    lines: list[TrialBalanceLine] = Field(default_factory=list)
    total_debits: Amount
    total_credits: Amount


class EntryIds(Answer):
    journal_entry_ids: list[int] = Field(default_factory=list)


class ObjectIds(Answer):
    accounting_object_ids: list[int] = Field(default_factory=list)


class Periods(Answer):
    periods: list[str] = Field(default_factory=list, description="Such as 2026-03")


class Reclassification(BaseModel):
    journal_entry_id: int
    correct_account: str


class Reclassifications(Answer):
    items: list[Reclassification] = Field(default_factory=list)


class DimensionFix(BaseModel):
    journal_entry_id: int
    dimension: Literal["DEPARTMENT", "LOCATION"]
    correct_value: str


class DimensionFixes(Answer):
    items: list[DimensionFix] = Field(default_factory=list)


class PeriodFix(BaseModel):
    journal_entry_id: int
    correct_period: str = Field(description="Such as 2026-03")


class PeriodFixes(Answer):
    items: list[PeriodFix] = Field(default_factory=list)


class VendorFix(BaseModel):
    accounting_object_id: int
    vendor: str = Field(description="The vendor's counterparty code")


class VendorFixes(Answer):
    items: list[VendorFix] = Field(default_factory=list)


class AmountFix(BaseModel):
    journal_entry_id: int
    correct_amount: Amount


class AmountFixes(Answer):
    items: list[AmountFix] = Field(default_factory=list)


class OpenInvoices(Answer):
    invoice_numbers: list[str] = Field(default_factory=list)
    total: Amount


class EntryAnswer(Answer):
    journal_entry_id: int


class IssueCodes(Answer):
    issue_codes: list[str] = Field(default_factory=list)


# --- Tasks read from the books --------------------------------------------------------


def _books(env: Environment) -> str:
    return f"You are working in a company's books for {env.year} through your tools."


def _balance_prepare(env: Environment) -> Prepared:
    as_of = month_end(env.year, env.rng.integer(3, 12))
    codes = [
        line.account_code
        for line in kernel.trial_balance(env.session, as_of=as_of).lines
    ]
    code = env.rng.choice(codes)
    balance = kernel.account_balance(env.session, code, as_of=as_of)
    return Prepared(
        f"{_books(env)} What was the balance of account {code} "
        f"({balance.account_name}) at the end of {as_of.isoformat()}? State it in the "
        "account's normal direction, positive when the account carries its usual "
        "balance.",
        {"balance": str(balance.balance)},
        {"account": code, "as_of": as_of.isoformat()},
    )


def _balance_score(instance: Instance, answer: Answer, env: Environment) -> Score:
    assert isinstance(answer, BalanceAnswer)
    right = float(amount(answer.balance) == amount(instance.expected["balance"]))
    return Score(right, numerical=right)


def _balance_solve(instance: Instance, tools: Tools) -> dict[str, Any]:
    call = tools.call(
        "get_account_balance",
        code=instance.context["account"],
        as_of=instance.context["as_of"],
    )
    return {"balance": call.result["balance"]} if call.ok else {}


def _trial_balance_prepare(env: Environment) -> Prepared:
    as_of = month_end(env.year, env.rng.integer(6, 12))
    trial = kernel.trial_balance(env.session, as_of=as_of)
    return Prepared(
        f"{_books(env)} Prepare the trial balance at the end of {as_of.isoformat()}: "
        "every account with a balance, in the debit or the credit column, and the "
        "total of each column.",
        {
            "lines": [
                {
                    "account": line.account_code,
                    "debit": str(line.debit),
                    "credit": str(line.credit),
                }
                for line in trial.lines
            ],
            "total_debits": str(trial.total_debits),
            "total_credits": str(trial.total_credits),
        },
        {"as_of": as_of.isoformat()},
    )


def _trial_balance_rows(
    lines: Sequence[tuple[str, str, str]], debits: str, credits: str
) -> set[tuple[str, Decimal, Decimal]]:
    rows = {
        (account, amount(debit), amount(credit)) for account, debit, credit in lines
    }
    return rows | {("TOTAL", amount(debits), amount(credits))}


def _trial_balance_score(instance: Instance, answer: Answer, env: Environment) -> Score:
    assert isinstance(answer, TrialBalanceAnswer)
    expected = instance.expected
    right = _trial_balance_rows(
        [
            (line["account"], line["debit"], line["credit"])
            for line in expected["lines"]
        ],
        expected["total_debits"],
        expected["total_credits"],
    )
    given = _trial_balance_rows(
        [(line.account, line.debit, line.credit) for line in answer.lines],
        answer.total_debits,
        answer.total_credits,
    )
    score = dice(right, given)
    return Score(score, numerical=score)


def _trial_balance_solve(instance: Instance, tools: Tools) -> dict[str, Any]:
    call = tools.call("get_trial_balance", as_of=instance.context["as_of"])
    if not call.ok:
        return {}
    report = call.result
    return {
        "lines": [
            {
                "account": line["account_code"],
                "debit": line["debit"],
                "credit": line["credit"],
            }
            for line in report["lines"]
        ],
        "total_debits": report["total_debits"],
        "total_credits": report["total_credits"],
    }


def _open_invoices(objects: Sequence[dict[str, Any]]) -> tuple[list[str], Decimal]:
    """Invoices no recorded customer payment settles, and their total; ``objects``
    are accounting objects as the tools present them."""
    paid = {
        obj["data"].get("invoice_number")
        for obj in objects
        if obj["object_type"] == "customer_payment"
        and obj["data"].get("payment_type") != "refund"
    }
    unpaid = [
        obj["data"]
        for obj in objects
        if obj["object_type"] == "customer_invoice"
        and obj["data"]["invoice_number"] not in paid
    ]
    return (
        sorted(data["invoice_number"] for data in unpaid),
        sum((Decimal(data["amount"]) for data in unpaid), Decimal("0.00")),
    )


def _open_invoices_prepare(env: Environment) -> Prepared:
    objects = [
        {"object_type": obj.object_type.value, "data": obj.data}
        for obj in search_accounting_objects(env.session)
        if obj.object_type.value in ("customer_invoice", "customer_payment")
    ]
    numbers, total = _open_invoices(objects)
    return Prepared(
        f"{_books(env)} Which customer invoices were still unpaid at the end of "
        f"{env.year}, according to the customer payments recorded in the books? Give "
        "their invoice numbers and the total they come to.",
        {"invoice_numbers": numbers, "total": str(total)},
    )


def _open_invoices_score(instance: Instance, answer: Answer, env: Environment) -> Score:
    assert isinstance(answer, OpenInvoices)
    found = dice(set(instance.expected["invoice_numbers"]), set(answer.invoice_numbers))
    total = float(amount(answer.total) == amount(instance.expected["total"]))
    return Score((found + total) / 2, numerical=total)


def _open_invoices_solve(instance: Instance, tools: Tools) -> dict[str, Any]:
    objects = []
    for object_type in ("customer_invoice", "customer_payment"):
        call = tools.call("search_accounting_objects", object_type=object_type)
        if not call.ok:
            return {}
        objects += call.result["accounting_objects"]
    numbers, total = _open_invoices(objects)
    return {"invoice_numbers": numbers, "total": str(total)}


# --- Tasks read from the ground truth -------------------------------------------------


@dataclass(frozen=True)
class _Finding:
    """How a task finds the errors of some types: the answer's list field, the
    fields of each item (none for a list of ids), and each error's expected item.
    An item's first field names the record; any others, its correction."""

    error_types: tuple[str, ...]
    field: str
    keys: tuple[str, ...]
    item: Callable[[dict[str, Any]], Any]


def _normal(key: str, value: Any) -> Any:
    return amount(value) if key.endswith("amount") else value


def _finding_task(
    task_id: str,
    title: str,
    category: str,
    description: str,
    instructions: str,
    answer: type[Answer],
    finding: _Finding,
    measures: frozenset[str] = frozenset(),
) -> Task:
    def expected_items(env: Environment) -> list[Any]:
        return [finding.item(e) for e in env.errors(*finding.error_types)]

    def prepare(env: Environment) -> Prepared:
        items = sorted(expected_items(env), key=str)
        if finding.keys:
            listed: list[Any] = [
                dict(zip(finding.keys, item, strict=True)) for item in items
            ]
        else:
            listed = items
        return Prepared(f"{_books(env)} {instructions}", {finding.field: listed})

    def normal(item: Any) -> Any:
        if not finding.keys:
            return item
        return tuple(_normal(k, v) for k, v in zip(finding.keys, item, strict=True))

    def score(instance: Instance, given: Answer, env: Environment) -> Score:
        listed = instance.expected[finding.field]
        if finding.keys:
            right = {normal(tuple(item[k] for k in finding.keys)) for item in listed}
            answered = {
                normal(tuple(getattr(item, k) for k in finding.keys))
                for item in getattr(given, finding.field)
            }
        else:
            right, answered = set(listed), set(getattr(given, finding.field))
        result = dice(right, answered)
        if len(finding.keys) < 2:
            return Score(result)
        records = {item[0] for item in right}
        classified = [item for item in answered if item[0] in records]
        correct = sum(item in right for item in classified)
        numerical = None
        if NUMERICAL in measures:
            numerical = correct / len(classified) if classified else 0.0
        return Score(
            result,
            numerical=numerical,
            classified=len(classified),
            correctly_classified=correct,
        )

    def solve(instance: Instance, tools: Tools) -> dict[str, Any]:
        return dict(instance.expected)  # the reference solution knows the ground truth

    return Task(
        id=task_id,
        title=title,
        category=category,
        description=description,
        answer=answer,
        measures=measures | ({CLASSIFICATION} if len(finding.keys) > 1 else set()),
        prepare=prepare,
        score=score,
        solve=solve,
    )


def _details(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda error: error["details"][key]


def _dimension_item(error: dict[str, Any]) -> tuple[int, str, str]:
    dimension = "DEPARTMENT" if error["type"] == "wrong_department" else "LOCATION"
    details = error["details"]
    return (
        details["journal_entry_id"],
        dimension,
        details[f"correct_{dimension.lower()}"],
    )


# --- Tasks in the workflow ------------------------------------------------------------

Line = tuple[str, Decimal, Decimal, tuple[tuple[str, str], ...]]


def _line(account: str, debit: str, credit: str, dimensions: dict[str, str]) -> Line:
    return account, amount(debit), amount(credit), tuple(sorted(dimensions.items()))


def _recorded_lines(entry: JournalEntry) -> set[Line]:
    return {
        _line(
            line.account.code,
            str(line.debit),
            str(line.credit),
            {t.value.dimension.code: t.value.code for t in line.dimensions},
        )
        for line in entry.lines
    }


def _new_bill(env: Environment) -> tuple[AccountingObject, dict[str, Any]]:
    """A bill that has just arrived, observed and classified by the clerk, and the
    entry that would record it as the company records that vendor's bills."""
    rng = env.rng
    vendor = rng.weighted(cast.OCCASIONAL_VENDORS, cast.OCCASIONAL_WEIGHTS)
    total = str(
        (Decimal(rng.skewed(vendor.low, vendor.high)) / 100).quantize(Decimal("0.01"))
    )
    department = rng.choice(vendor.departments)
    location = "HQ" if department == "GA" else rng.choice(("EAST", "HQ", "WEST"))
    day = previous_business_day(date(env.year, 12, 30))
    number = f"{vendor.invoice_prefix}-{rng.integer(100_000, 999_999)}"
    while search_accounting_objects(env.session, data={"invoice_number": number}):
        number = f"{vendor.invoice_prefix}-{rng.integer(100_000, 999_999)}"
    bill = workflow.observe_accounting_object(
        env.session,
        actor=env.clerk,
        object_type="vendor_bill",
        occurred_at=datetime(day.year, day.month, day.day, 9, 30, tzinfo=UTC),
        source="email",
        data={
            "invoice_number": number,
            "vendor_name": vendor.name,
            "invoice_date": day.isoformat(),
            "due_date": date(env.year + 1, 1, 29).isoformat(),
            "description": "Services and supplies",
            "category": vendor.category,
            "amount": total,
            "department": department,
            "location": location,
        },
        reason="Bill received by email",
    )
    workflow.classify_accounting_object(
        env.session,
        bill,
        actor=env.clerk,
        counterparty=vendor.code,
        reason="The bill names its vendor",
        evidence=[f"vendor_name={vendor.name}"],
    )
    env.session.flush()
    tags = {"DEPARTMENT": department, "LOCATION": location}
    return bill, {
        "object_id": bill.id,
        "entry_date": day.isoformat(),
        "description": f"{vendor.name} - bill {number}",
        "lines": [
            {
                "account": vendor.account,
                "debit": total,
                "credit": "0.00",
                "dimensions": tags,
            },
            {"account": "2110", "debit": "0.00", "credit": total, "dimensions": {}},
        ],
    }


def _score_entry(
    instance: Instance, entry_id: int, env: Environment, *, replaces: int | None = None
) -> Score:
    """The entry the agent proposed for the task's bill, scored line by line, and
    whether it stands where the workflow expects: proposed or submitted, valid, and,
    when it replaces an invalid entry, with that one voided."""
    entry = env.session.get(JournalEntry, entry_id)
    if entry is None:
        return Score(0.0, workflow=0.0, details={"problem": "no such journal entry"})
    recorded = {obj.id for obj in objects_for_journal_entry(env.session, entry)}
    history = workflow_history(env.session, entry)
    if instance.context["object_id"] not in recorded:
        return Score(
            0.0, workflow=0.0, details={"problem": "it does not record the bill"}
        )
    if not history or history[0].actor_id != env.agent.id:
        return Score(
            0.0, workflow=0.0, details={"problem": "the agent did not propose it"}
        )
    expected = {_line(**line) for line in instance.context["lines"]}
    lines = dice(expected, _recorded_lines(entry))
    waiting = entry.status in (
        JournalEntryStatus.PROPOSED,
        JournalEntryStatus.PENDING_APPROVAL,
    )
    valid = not kernel.validate_journal_entry(env.session, entry)
    replaced = True
    if replaces is not None:
        original = env.session.get(JournalEntry, replaces)
        replaced = original is not None and original.status is JournalEntryStatus.VOIDED
    return Score(
        lines,
        workflow=float(waiting and valid and replaced),
        details={
            "status": entry.status.value,
            "valid": valid,
            "replaced_voided": replaced,
        },
    )


def _propose_bill(instance: Instance, tools: Tools) -> dict[str, Any]:
    """Propose the entry the task expects for its bill, as the reference solution."""
    context = instance.context
    call = tools.call(
        "propose_journal_entry",
        entry_date=context["entry_date"],
        description=context["description"],
        lines=context["lines"],
        accounting_object_id=context["object_id"],
        reason="Posted as this vendor's other bills are, with the bill's tags",
        evidence=[f"accounting_object_id={context['object_id']}"],
    )
    return {"journal_entry_id": call.result["id"]} if call.ok else {}


def _generate_prepare(env: Environment) -> Prepared:
    bill, context = _new_bill(env)
    return Prepared(
        f"{_books(env)} A bill has just arrived and is recorded as accounting object "
        f"{bill.id}. Propose the journal entry that records it, linked to that object, "
        "posted the way the company posts this vendor's other bills and tagged with "
        "the department and location the bill names. Give a concise reason and "
        "evidence. Answer with the id of the entry you proposed.",
        {"lines": context["lines"]},
        context,
    )


def _generate_score(instance: Instance, answer: Answer, env: Environment) -> Score:
    assert isinstance(answer, EntryAnswer)
    return _score_entry(instance, answer.journal_entry_id, env)


_ISSUE_KINDS = ("unbalanced", "parent_account", "no_period")


def _validate_prepare(env: Environment) -> Prepared:
    kinds = env.rng.sample(_ISSUE_KINDS, env.rng.integer(1, 3))
    total = Decimal(env.rng.integer(20_000, 90_000)) / 100
    credit = total - Decimal("10.00") if "unbalanced" in kinds else total
    entry = workflow.propose_journal_entry(
        env.session,
        actor=env.clerk,
        entry_date=date(env.year + 1, 1, 15)
        if "no_period" in kinds
        else date(env.year, 12, 15),
        description="Office supplies, keyed from the supplier's invoice",
        lines=[
            LineInput("6000" if "parent_account" in kinds else "6700", debit=total),
            LineInput("2110", credit=credit),
        ],
        reason="Keyed from the supplier's invoice",
    )
    env.session.flush()
    codes = sorted(
        {i.code.value for i in kernel.validate_journal_entry(env.session, entry)}
    )
    return Prepared(
        f"{_books(env)} Journal entry {entry.id} was proposed by a colleague. Validate "
        "it, and report the code of every issue that stops it from being posted.",
        {"issue_codes": codes},
        {"entry_id": entry.id},
    )


def _validate_score(instance: Instance, answer: Answer, env: Environment) -> Score:
    assert isinstance(answer, IssueCodes)
    validated = audit_history(
        env.session,
        object_type="journal_entry",
        object_id=instance.context["entry_id"],
        actor=env.agent,
        action="validate_journal_entry",
        result="SUCCEEDED",
    )
    return Score(
        dice(set(instance.expected["issue_codes"]), set(answer.issue_codes)),
        workflow=float(bool(validated)),
    )


def _validate_solve(instance: Instance, tools: Tools) -> dict[str, Any]:
    call = tools.call(
        "validate_journal_entry",
        entry_id=instance.context["entry_id"],
        reason="Checking a colleague's entry before it is submitted",
        evidence=[f"journal_entry_id={instance.context['entry_id']}"],
    )
    return (
        {"issue_codes": [issue["code"] for issue in call.result["issues"]]}
        if call.ok
        else {}
    )


def _correct_prepare(env: Environment) -> Prepared:
    bill, context = _new_bill(env)
    right = context["lines"][0]
    parent = kernel.get_account(env.session, right["account"]).parent
    assert parent is not None
    wrong = workflow.propose_journal_entry(
        env.session,
        actor=env.clerk,
        entry_date=date.fromisoformat(context["entry_date"]),
        description=context["description"],
        lines=[
            LineInput(parent.code, debit=Decimal(right["debit"])),
            LineInput("2110", credit=Decimal(right["debit"]) + Decimal("9.00")),
        ],
        accounting_object=bill,
        reason="Keyed from the bill",
    )
    env.session.flush()
    return Prepared(
        f"{_books(env)} Journal entry {wrong.id} was proposed for the bill recorded as "
        f"accounting object {bill.id}, but it cannot be posted. Correct it: void it, "
        "and propose in its place a valid entry that records the bill the way the "
        "company records this vendor's bills, linked to the object and tagged with "
        "the department and location the bill names. Give a concise reason and "
        "evidence for each step. Answer with the id of the entry you proposed.",
        {"voided_journal_entry_id": wrong.id, "lines": context["lines"]},
        {**context, "entry_id": wrong.id},
    )


def _correct_score(instance: Instance, answer: Answer, env: Environment) -> Score:
    assert isinstance(answer, EntryAnswer)
    return _score_entry(
        instance, answer.journal_entry_id, env, replaces=instance.context["entry_id"]
    )


def _correct_solve(instance: Instance, tools: Tools) -> dict[str, Any]:
    tools.call(
        "void_journal_entry",
        entry_id=instance.context["entry_id"],
        reason="Posted to a summary account and does not balance",
        evidence=[f"journal_entry_id={instance.context['entry_id']}"],
    )
    return _propose_bill(instance, tools)


# --- The catalogue --------------------------------------------------------------------

TASKS: tuple[Task, ...] = (
    Task(
        id="GL-001",
        title="Get an account balance",
        category=GENERAL_LEDGER,
        description="Report one account's balance at a month end.",
        answer=BalanceAnswer,
        measures=frozenset({NUMERICAL}),
        prepare=_balance_prepare,
        score=_balance_score,
        solve=_balance_solve,
    ),
    Task(
        id="GL-002",
        title="Calculate the trial balance",
        category=GENERAL_LEDGER,
        description="Report every account's balance and the column totals at a "
        "month end.",
        answer=TrialBalanceAnswer,
        measures=frozenset({NUMERICAL}),
        prepare=_trial_balance_prepare,
        score=_trial_balance_score,
        solve=_trial_balance_solve,
    ),
    _finding_task(
        "GL-003",
        "Find unusual transactions",
        GENERAL_LEDGER,
        "Find the transactions far out of line with the company's normal spending.",
        "Find the transactions unusual enough to need investigating: amounts far "
        "outside what the company normally spends with the same merchant or vendor. "
        "Answer with their journal entry ids.",
        EntryIds,
        _Finding(
            ("unusual_transaction",),
            "journal_entry_ids",
            (),
            _details("journal_entry_id"),
        ),
    ),
    _finding_task(
        "GL-004",
        "Identify incorrectly classified expenses",
        GENERAL_LEDGER,
        "Find expenses posted to the wrong account, and name the right one.",
        "Some expenses were posted to the wrong general ledger account. Find each of "
        "those journal entries, and the account it should have been posted to.",
        Reclassifications,
        _Finding(
            ("wrong_gl_account",),
            "items",
            ("journal_entry_id", "correct_account"),
            lambda e: (
                e["details"]["journal_entry_id"],
                e["details"]["correct_account"],
            ),
        ),
    ),
    _finding_task(
        "GL-005",
        "Identify wrong department and location tags",
        GENERAL_LEDGER,
        "Find expense lines tagged differently from their source document.",
        "Some expense lines carry a department or location tag different from the "
        "one their source document names. Find each of those journal entries, the "
        "dimension that is wrong, and the value it should carry.",
        DimensionFixes,
        _Finding(
            ("wrong_department", "wrong_location"),
            "items",
            ("journal_entry_id", "dimension", "correct_value"),
            _dimension_item,
        ),
    ),
    _finding_task(
        "GL-006",
        "Identify entries in the wrong period",
        GENERAL_LEDGER,
        "Find bills recorded outside the period of their invoice date.",
        "Some bills were recorded in an accounting period other than the one their "
        "invoice date falls in. Find each of those journal entries, and the period "
        "it belongs to.",
        PeriodFixes,
        _Finding(
            ("wrong_accounting_period",),
            "items",
            ("journal_entry_id", "correct_period"),
            lambda e: (
                e["details"]["journal_entry_id"],
                e["details"]["correct_period"],
            ),
        ),
    ),
    _finding_task(
        "AP-001",
        "Find duplicate vendor invoices",
        PAYABLES,
        "Find vendor bills recorded more than once.",
        "Some vendor bills were recorded more than once. For each, give the "
        "accounting object id of every copy after the first one received.",
        ObjectIds,
        _Finding(
            ("duplicate_invoice",),
            "accounting_object_ids",
            (),
            _details("duplicate_accounting_object_id"),
        ),
    ),
    _finding_task(
        "AP-002",
        "Identify bills missing their vendor",
        PAYABLES,
        "Find vendor bills recorded without a vendor, and name the vendor.",
        "Some vendor bills were recorded without naming their vendor. Find each of "
        "those bills, by accounting object id, and the vendor it came from, by "
        "counterparty code.",
        VendorFixes,
        _Finding(
            ("missing_vendor",),
            "items",
            ("accounting_object_id", "vendor"),
            lambda e: (e["details"]["accounting_object_id"], e["details"]["vendor"]),
        ),
    ),
    _finding_task(
        "AP-003",
        "Find duplicate vendor payments",
        PAYABLES,
        "Find vendor bills paid more than once.",
        "Some vendor bills were paid more than once. Give the accounting object id of "
        "every payment made after the first for the same bill.",
        ObjectIds,
        _Finding(
            ("duplicate_payment",),
            "accounting_object_ids",
            (),
            _details("duplicate_accounting_object_id"),
        ),
    ),
    _finding_task(
        "AP-004",
        "Identify bills recorded at the wrong amount",
        PAYABLES,
        "Find bills whose entry records a different amount from the bill, and give "
        "the right amount.",
        "Some bills were recorded at an amount different from the bill itself. Find "
        "each of those journal entries, and the amount it should have recorded.",
        AmountFixes,
        _Finding(
            ("incorrect_amount",),
            "items",
            ("journal_entry_id", "correct_amount"),
            lambda e: (
                e["details"]["journal_entry_id"],
                e["details"]["correct_amount"],
            ),
        ),
        measures=frozenset({NUMERICAL}),
    ),
    Task(
        id="AR-001",
        title="Identify outstanding customer invoices",
        category=RECEIVABLES,
        description="List the invoices unpaid at year end, and their total.",
        answer=OpenInvoices,
        measures=frozenset({NUMERICAL}),
        prepare=_open_invoices_prepare,
        score=_open_invoices_score,
        solve=_open_invoices_solve,
    ),
    _finding_task(
        "AR-002",
        "Find customer receipts missing from the books",
        RECEIVABLES,
        "Find deposits on the bank statement that no entry records.",
        "The bank statement shows customer payments that were never recorded in the "
        "books. Find each of those bank statement lines, by accounting object id.",
        ObjectIds,
        _Finding(
            ("unreconciled_transaction",),
            "accounting_object_ids",
            (),
            _details("bank_transaction_object_id"),
        ),
    ),
    _finding_task(
        "CLOSE-001",
        "Identify missing accruals",
        CLOSE,
        "Find the month ends at which legal fees were not accrued.",
        "Legal fees are accrued at every month end, and the accrual reversed when "
        "the bill arrives. Find the months for which no accrual was recorded.",
        Periods,
        _Finding(("missing_accrual",), "periods", (), _details("period")),
    ),
    Task(
        id="JE-001",
        title="Generate a journal entry",
        category=JOURNAL_ENTRIES,
        description="Propose the entry for a newly arrived bill.",
        answer=EntryAnswer,
        measures=frozenset({WORKFLOW}),
        prepare=_generate_prepare,
        score=_generate_score,
        solve=_propose_bill,
    ),
    Task(
        id="JE-002",
        title="Validate a journal entry",
        category=JOURNAL_ENTRIES,
        description="Validate a colleague's entry and report its issue codes.",
        answer=IssueCodes,
        measures=frozenset({WORKFLOW}),
        prepare=_validate_prepare,
        score=_validate_score,
        solve=_validate_solve,
    ),
    Task(
        id="JE-003",
        title="Correct an invalid journal entry",
        category=JOURNAL_ENTRIES,
        description="Void an entry that cannot be posted, and propose a valid one.",
        answer=EntryAnswer,
        measures=frozenset({WORKFLOW}),
        prepare=_correct_prepare,
        score=_correct_score,
        solve=_correct_solve,
    ),
)


def get_task(task_id: str) -> Task:
    for task in TASKS:
        if task.id == task_id:
            return task
    raise KeyError(f"no benchmark task {task_id!r}")
