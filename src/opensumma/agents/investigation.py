"""The investigation agent: it answers questions about the books and finds what is
wrong in them, through the read-only tools alone. It changes nothing.

Each check is an accounting rule applied to what the books hold, the business
documents and the posted ledger:

- **Unusual charges (GL-003).** A company card charge is unusual when it sits above
  a gap of an order of magnitude in the charges at its merchant, among no more
  charges than lie below the gap, and is ten times the typical one. A merchant
  with fewer than three charges is too little to go on, so its charges are
  compared with every card charge instead. Vendor bills are not judged this way:
  a vendor bills for different things, a subscription and one-off work, or stock
  ordered to demand, so large differences between its bills are normal.
- **Misclassified expenses (GL-004).** Documents of one category, such as meals,
  are posted to one account. Where most of a category's documents agree, one
  posted elsewhere is misclassified, and the account most agree on is right.
- **Wrong tags (GL-005).** An expense line tagged with a department or location
  other than the one its document names.
- **Wrong period (GL-006).** A bill recorded in a period other than its invoice
  date's.
- **Wrong amount (AP-004).** A bill whose entry records another amount than the
  bill.
- **Missing vendor (AP-002).** A bill recorded without a vendor, matched to the
  vendor whose name it gives.
- **Missing receipts (AR-002).** A deposit on the bank statement that no entry in
  the account the statement is for accounts for: the same amount, recorded in the
  week before the bank received it.
- **Missing accruals (CLOSE-001).** A month of the year that no accrual covers.

It also reads a balance (GL-001) and the trial balance (GL-002), and lists the
invoices no recorded payment settles (AR-001).
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from opensumma.agents.common import (
    CENT,
    Books,
    Finding,
    Workflow,
    WorkflowAgent,
    find,
    majority,
    report,
    use,
)
from opensumma.benchmark import TaskPrompt, Tools

ORDER_OF_MAGNITUDE = Decimal(10)
# A merchant with fewer charges than this is compared with every card charge.
ENOUGH_HISTORY = 3
# How long before the bank receives a payment it may have been recorded.
RECEIPT_WINDOW = timedelta(days=7)
# The tags GL-005 asks about, each named in a document by its lowercase name.
TAGS = ("DEPARTMENT", "LOCATION")

DAY = r"end of (\d{4}-\d{2}-\d{2})"


# --- Rules ------------------------------------------------------------------------


def typical(amounts: Sequence[Decimal]) -> Decimal:
    """The median. Of an even number, the geometric mean of the middle two, so that
    one huge amount among two cannot pass for typical."""
    ordered = sorted(amounts)
    if not ordered:
        raise ValueError("no amounts")
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] * ordered[middle]).sqrt().quantize(CENT)


def far_above(amounts: Sequence[Decimal]) -> set[Decimal]:
    """The amounts far above the rest: above the first gap of an order of magnitude
    between one amount and the next, when they are no more of them than below
    it, and ten times the typical amount or more."""
    ordered = sorted(a for a in amounts if a > 0)
    for i in range(1, len(ordered)):
        if ordered[i] > ORDER_OF_MAGNITUDE * ordered[i - 1]:
            above = ordered[i:]
            if len(above) > i:
                return set()
            usual = typical(ordered)
            return {a for a in above if a >= ORDER_OF_MAGNITUDE * usual}
    return set()


@dataclass(frozen=True)
class Deposit:
    """Money the bank statement shows arriving."""

    object_id: int
    posted_on: date
    amount: Decimal


@dataclass(frozen=True)
class Receipt:
    """Money the books record arriving in the bank account."""

    entry_id: int
    entry_date: date
    amount: Decimal


def unmatched_deposits(
    deposits: Sequence[Deposit], receipts: Sequence[Receipt]
) -> list[Deposit]:
    """The deposits no receipt accounts for. In date order, each deposit takes the
    latest receipt still unmatched of the same amount, recorded within the week
    before the bank received it."""
    available = sorted(receipts, key=lambda r: (r.entry_date, r.entry_id))
    unmatched = []
    for deposit in sorted(deposits, key=lambda d: (d.posted_on, d.object_id)):
        candidates = [
            r
            for r in available
            if r.amount == deposit.amount
            and deposit.posted_on - RECEIPT_WINDOW <= r.entry_date <= deposit.posted_on
        ]
        if candidates:
            available.remove(candidates[-1])
        else:
            unmatched.append(deposit)
    return unmatched


# --- Reading the books --------------------------------------------------------------


def account_balance(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    code = find(r"balance of account (\S+)", task, "which account")
    as_of = find(DAY, task, "at what date")
    return {
        "balance": use(tools, "get_account_balance", code=code, as_of=as_of)["balance"]
    }


def trial_balance(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    found = use(tools, "get_trial_balance", as_of=find(DAY, task, "at what date"))
    return {
        "lines": [
            {
                "account": line["account_code"],
                "debit": line["debit"],
                "credit": line["credit"],
            }
            for line in found["lines"]
        ],
        "total_debits": found["total_debits"],
        "total_credits": found["total_credits"],
    }


def open_invoices(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    """The invoices the customer payments recorded by year end leave unpaid, and
    what is left to pay on them."""
    year_end = date(int(find(r"end of (\d{4})\b", task, "which year")), 12, 31)
    books = Books(tools)
    received: defaultdict[str, Decimal] = defaultdict(Decimal)
    for payment in books.objects("customer_payment"):
        data = payment["data"]
        if data.get("payment_type") == "refund":  # money paid back, not received
            continue
        if date.fromisoformat(data["received_on"]) <= year_end:
            received[data["invoice_number"]] += Decimal(data["amount"])
    unpaid = {}
    for invoice in books.objects("customer_invoice"):
        data = invoice["data"]
        if date.fromisoformat(data["invoice_date"]) > year_end:
            continue
        outstanding = Decimal(data["amount"]) - received[data["invoice_number"]]
        if outstanding > 0:
            unpaid[data["invoice_number"]] = outstanding
    total = sum(unpaid.values(), Decimal("0.00"))
    return {"invoice_numbers": sorted(unpaid), "total": str(total.quantize(CENT))}


# --- Finding errors -----------------------------------------------------------------


def unusual_charges(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    books = Books(tools)
    charges: defaultdict[str, list[tuple[Decimal, int, int]]] = defaultdict(list)
    for obj in books.objects("expense"):
        entry = books.posted_entry(obj)
        merchant = obj["data"].get("merchant") or obj["counterparty"]
        if entry is not None and merchant:
            charges[merchant].append((entry.total, entry.id, obj["id"]))
    everything = [charge[0] for group in charges.values() for charge in group]
    findings = []
    for merchant, group in charges.items():
        amounts = [charge[0] for charge in group]
        if len(group) < ENOUGH_HISTORY:
            compared, flagged = everything, far_above(everything)
            others = f"the company's {len(everything)} card charges"
        else:
            compared, flagged = amounts, far_above(amounts)
            others = f"the {len(group)} charges at {merchant}"
        for charged, entry_id, object_id in group:
            if charged in flagged:
                usual = typical(compared)
                findings.append(
                    Finding(
                        entry_id,
                        f"A charge of {charged} at {merchant}, far above the rest of "
                        f"{others}, whose typical charge is {usual}.",
                        (
                            f"journal_entry_id={entry_id}",
                            f"accounting_object_id={object_id}",
                            f"merchant={merchant}",
                            f"amount={charged}",
                            f"typical_amount={usual}",
                        ),
                    )
                )
    return report("journal_entry_ids", findings)


def misclassified_expenses(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    books = Books(tools)
    postings: defaultdict[str, list[tuple[str, int, dict[str, Any]]]] = defaultdict(
        list
    )
    for obj in books.objects("vendor_bill") + books.objects("expense"):
        category, entry = obj["data"].get("category"), books.posted_entry(obj)
        if category and entry is not None and len(entry.debited_accounts) == 1:
            (account,) = entry.debited_accounts
            postings[category].append((account, entry.id, obj))
    findings = []
    for category, group in postings.items():
        agreed = majority(account for account, _, _ in group)
        if agreed is None or len(group) < ENOUGH_HISTORY:
            continue
        usual, count = agreed
        for account, entry_id, obj in group:
            if account != usual:
                findings.append(
                    Finding(
                        {"journal_entry_id": entry_id, "correct_account": usual},
                        f"{count} of the {len(group)} {category} documents are posted "
                        f"to {usual}; this one was posted to {account}.",
                        (
                            f"journal_entry_id={entry_id}",
                            f"accounting_object_id={obj['id']}",
                            f"category={category}",
                            f"posted_account={account}",
                            f"usual_account={usual}",
                        ),
                    )
                )
    return report("items", findings)


def mistagged_lines(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    books = Books(tools)
    expenses = {
        code for code, a in books.accounts().items() if a["account_type"] == "EXPENSE"
    }
    findings: dict[tuple[int, str], Finding] = {}
    for obj in books.objects():
        entry = books.posted_entry(obj)
        if entry is None:
            continue
        for line in entry.lines:
            if line["account_code"] not in expenses:
                continue
            for tag in TAGS:
                named, tagged = (
                    obj["data"].get(tag.lower()),
                    line["dimensions"].get(tag),
                )
                if isinstance(named, str) and tagged is not None and tagged != named:
                    findings.setdefault(
                        (entry.id, tag),
                        Finding(
                            {
                                "journal_entry_id": entry.id,
                                "dimension": tag,
                                "correct_value": named,
                            },
                            f"The {obj['object_type']} names {tag.lower()} {named}, "
                            f"but its entry's line {line['line_number']} on "
                            f"{line['account_code']} is tagged {tagged}.",
                            (
                                f"journal_entry_id={entry.id}",
                                f"accounting_object_id={obj['id']}",
                                f"tagged_{tag.lower()}={tagged}",
                                f"document_{tag.lower()}={named}",
                            ),
                        ),
                    )
    return report("items", list(findings.values()))


def misdated_bills(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    books = Books(tools)
    findings = []
    for bill in books.objects("vendor_bill"):
        entry, dated = books.posted_entry(bill), bill["data"].get("invoice_date")
        if entry is None or not dated:
            continue
        belongs, recorded = (
            books.period_of(date.fromisoformat(dated)),
            books.period_of(entry.entry_date),
        )
        if belongs is not None and recorded != belongs:
            findings.append(
                Finding(
                    {"journal_entry_id": entry.id, "correct_period": belongs},
                    f"Bill {bill['data'].get('invoice_number')} is dated {dated}, in "
                    f"{belongs}, but was recorded on {entry.entry_date}, in "
                    f"{recorded}.",
                    (
                        f"journal_entry_id={entry.id}",
                        f"accounting_object_id={bill['id']}",
                        f"invoice_date={dated}",
                        f"entry_date={entry.entry_date}",
                    ),
                )
            )
    return report("items", findings)


def misstated_bills(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    books = Books(tools)
    findings = []
    for bill in books.objects("vendor_bill"):
        entry, billed = books.posted_entry(bill), bill["data"].get("amount")
        if entry is None or billed is None or entry.total == Decimal(billed):
            continue
        findings.append(
            Finding(
                {"journal_entry_id": entry.id, "correct_amount": billed},
                f"Bill {bill['data'].get('invoice_number')} is for {billed}, but its "
                f"entry records {entry.total}.",
                (
                    f"journal_entry_id={entry.id}",
                    f"accounting_object_id={bill['id']}",
                    f"billed_amount={billed}",
                    f"recorded_amount={entry.total}",
                ),
            )
        )
    return report("items", findings)


def bills_missing_vendor(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    books = Books(tools)
    vendors = {v["name"].casefold(): v["code"] for v in books.counterparties("VENDOR")}
    findings = []
    for bill in books.objects("vendor_bill"):
        name = bill["data"].get("vendor_name")
        if bill["counterparty"] is not None or not isinstance(name, str):
            continue
        vendor = vendors.get(name.casefold())
        if vendor is None:  # nothing in the books says who sent it
            continue
        findings.append(
            Finding(
                {"accounting_object_id": bill["id"], "vendor": vendor},
                f"Bill {bill['data'].get('invoice_number')} was recorded without a "
                f"vendor, but it names {name}, vendor {vendor}.",
                (
                    f"accounting_object_id={bill['id']}",
                    f"vendor_name={name}",
                    f"vendor={vendor}",
                ),
            )
        )
    return report("items", findings)


def unrecorded_receipts(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    books = Books(tools)
    statement = books.objects("bank_transaction")
    ledger = books.ledger()
    recorded = {i for line in statement for i in line["journal_entry_ids"]}
    # The account the statement is for: the one every recorded line's entry posts to.
    shared: set[str] | None = None
    for entry_id in recorded & ledger.keys():
        accounts = {line["account_code"] for line in ledger[entry_id].lines}
        shared = accounts if shared is None else shared & accounts
    if shared is None or len(shared) != 1:
        raise ValueError("cannot tell which account the bank statement is for")
    (bank_account,) = shared
    receipts = [
        Receipt(entry.id, entry.entry_date, Decimal(line["debit"]))
        for entry in ledger.values()
        if entry.id not in recorded
        for line in entry.lines
        if line["account_code"] == bank_account and Decimal(line["debit"])
    ]
    deposits = {
        line["id"]: line
        for line in statement
        if not line["journal_entry_ids"] and Decimal(line["data"]["amount"]) > 0
    }
    unmatched = unmatched_deposits(
        [
            Deposit(
                line["id"],
                date.fromisoformat(line["data"]["posted_on"]),
                Decimal(line["data"]["amount"]),
            )
            for line in deposits.values()
        ],
        receipts,
    )
    findings = [
        Finding(
            deposit.object_id,
            f"The bank received {deposit.amount} on {deposit.posted_on} "
            f"({deposits[deposit.object_id]['data'].get('description')}), and no "
            f"entry in {bank_account} records it.",
            (
                f"accounting_object_id={deposit.object_id}",
                f"reference={deposits[deposit.object_id]['data'].get('reference')}",
                f"amount={deposit.amount}",
                f"bank_account={bank_account}",
            ),
        )
        for deposit in unmatched
    ]
    return report("accounting_object_ids", findings)


def missing_accruals(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    year = find(r"books for (\d{4})", task, "which year")
    books = Books(tools)
    accruals = [
        obj
        for obj in books.objects("journal_entry")
        if obj["data"].get("kind") == "accrual" and obj["journal_entry_ids"]
    ]
    accrued = {obj["data"].get("service_period") for obj in accruals}
    accruing = {obj["data"].get("vendor_name") for obj in accruals}
    billed = {
        bill["data"].get("service_period"): bill["data"].get("invoice_number")
        for bill in books.objects("vendor_bill")
        if bill["data"].get("vendor_name") in accruing
    }
    months = [p["code"] for p in books.periods() if p["start_date"].startswith(year)]
    findings = [
        Finding(
            month,
            f"No accrual was recorded for {month}, though {len(accrued & set(months))} "
            f"of the year's {len(months)} months have one.",
            (f"period={month}",)
            + ((f"invoice_for_period={billed[month]}",) if month in billed else ()),
        )
        for month in months
        if month not in accrued
    ]
    return report("periods", findings)


class InvestigationAgent(WorkflowAgent):
    """Answers questions about the books, and finds what is wrong in them, reading
    through the tools and changing nothing."""

    name = "investigator"
    workflows: ClassVar[Mapping[str, Workflow]] = {
        "GL-001": account_balance,
        "GL-002": trial_balance,
        "GL-003": unusual_charges,
        "GL-004": misclassified_expenses,
        "GL-005": mistagged_lines,
        "GL-006": misdated_bills,
        "AP-002": bills_missing_vendor,
        "AP-004": misstated_bills,
        "AR-001": open_invoices,
        "AR-002": unrecorded_receipts,
        "CLOSE-001": missing_accruals,
    }
