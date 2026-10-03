"""The journal entry agent: it records bills the way the company records that
vendor's bills, checks entries, and corrects those that cannot be posted.

It learns how to record a bill from the books, not from a rule of its own: the
accounts, and the tags, of the entries that recorded the vendor's latest posted
bills. The bill gives the amount, the date, and the value of each tag. The agent
proposes through the workflow, validates what it proposed, and submits it for
approval only if it is valid. It holds no permission to approve or post, so its
work waits for a person, and every change it makes carries a concise reason and
the evidence for it, which the audit log keeps.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from opensumma.agents.common import Workflow, WorkflowAgent, find, use
from opensumma.benchmark import TaskPrompt, Tools

# How many of the vendor's latest posted bills to learn from.
PRECEDENTS = 10


@dataclass(frozen=True)
class Shape:
    """How an entry records a bill: the account debited and the one credited, and
    the tags each line carries."""

    debit_account: str
    debit_tags: tuple[str, ...]
    credit_account: str
    credit_tags: tuple[str, ...]


def shape(entry: dict[str, Any]) -> Shape | None:
    """How ``entry`` records its bill, if it has one debit line and one credit line:
    one positive amount and one negative."""
    debits = [line for line in entry["lines"] if Decimal(line["amount"]) > 0]
    credits = [line for line in entry["lines"] if Decimal(line["amount"]) < 0]
    if len(debits) != 1 or len(credits) != 1:
        return None
    (debit,), (credit,) = debits, credits
    return Shape(
        debit["account"],
        tuple(sorted(debit["dimensions"])),
        credit["account"],
        tuple(sorted(credit["dimensions"])),
    )


@dataclass(frozen=True)
class Precedent:
    """How the vendor's latest posted bills were recorded, and the entries that
    recorded them that way."""

    shape: Shape
    entries: tuple[int, ...]
    considered: int


def precedent(tools: Tools, bill: dict[str, Any]) -> Precedent:
    """How the latest posted bills from ``bill``'s vendor were mostly recorded."""
    earlier = use(
        tools,
        "search_accounting_objects",
        object_type=bill["object_type"],
        counterparty=bill["counterparty"],
    )["accounting_objects"]
    earlier.sort(
        key=lambda obj: (datetime.fromisoformat(obj["occurred_at"]), obj["id"]),
        reverse=True,
    )
    seen: list[tuple[Shape, int]] = []
    for obj in earlier:
        if obj["id"] == bill["id"]:
            continue
        for entry_id in obj["journal_entry_ids"]:
            entry = use(tools, "get_journal_entry", entry_id=entry_id)
            found = shape(entry) if entry["status"] == "POSTED" else None
            if found is not None:
                seen.append((found, entry_id))
        if len(seen) >= PRECEDENTS:
            break
    if not seen:
        raise ValueError(
            f"no posted bill from {bill['counterparty']} shows how its bills are "
            "recorded"
        )
    usual, _ = Counter(found for found, _ in seen).most_common(1)[0]
    return Precedent(
        usual,
        tuple(entry_id for found, entry_id in seen if found == usual),
        len(seen),
    )


def record_bill(tools: Tools, object_id: int) -> int:
    """Propose the entry that records the bill ``object_id``, as its vendor's bills
    are recorded, and submit it for approval if it is valid; return its id."""
    bill = use(tools, "get_accounting_object", object_id=object_id)
    if bill["counterparty"] is None:
        raise ValueError(f"bill {object_id} has no vendor; classify it first")
    learned = precedent(tools, bill)
    data, usual = bill["data"], learned.shape

    def tags(names: tuple[str, ...]) -> dict[str, str]:
        """The value of each tag, from the bill's field of the same name."""
        return {
            name: data[name.lower()]
            for name in names
            if isinstance(data.get(name.lower()), str)
        }

    entry = use(
        tools,
        "propose_journal_entry",
        entry_date=data["invoice_date"],
        description=f"{data['vendor_name']} - bill {data['invoice_number']}",
        lines=[
            {
                "account": usual.debit_account,
                "amount": data["amount"],
                "dimensions": tags(usual.debit_tags),
            },
            {
                "account": usual.credit_account,
                "amount": str(-Decimal(data["amount"])),
                "dimensions": tags(usual.credit_tags),
            },
        ],
        accounting_object_id=object_id,
        reason=(
            f"Recorded as {len(learned.entries)} of {bill['counterparty']}'s latest "
            f"{learned.considered} posted bills were: debit {usual.debit_account}, "
            f"credit {usual.credit_account}, tagged as the bill names"
        ),
        evidence=[
            f"accounting_object_id={object_id}",
            f"invoice={data['invoice_number']}",
            f"vendor={bill['counterparty']}",
            f"historical_debit_account={usual.debit_account}",
            f"historical_credit_account={usual.credit_account}",
            *(f"precedent_journal_entry_id={i}" for i in learned.entries),
        ],
    )
    submit_if_valid(tools, entry["id"])
    entry_id: int = entry["id"]
    return entry_id


def validate(tools: Tools, entry_id: int, reason: str) -> list[str]:
    """The codes of the issues that stop entry ``entry_id`` from being posted."""
    checked = use(
        tools,
        "validate_journal_entry",
        entry_id=entry_id,
        reason=reason,
        evidence=[f"journal_entry_id={entry_id}"],
    )
    return sorted({issue["code"] for issue in checked["issues"]})


def submit_if_valid(tools: Tools, entry_id: int) -> None:
    if not validate(tools, entry_id, "Checking the entry before submitting it"):
        use(
            tools,
            "submit_for_approval",
            entry_id=entry_id,
            reason="The entry is valid and ready for approval",
            evidence=[f"journal_entry_id={entry_id}"],
        )


def generate_entry(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    object_id = int(find(r"accounting object (\d+)", task, "which bill"))
    return {"journal_entry_id": record_bill(tools, object_id)}


def review_entry(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    entry_id = int(find(r"[Jj]ournal entry (\d+)", task, "which entry"))
    reason = "Checking a colleague's entry before it is submitted"
    return {"issue_codes": validate(tools, entry_id, reason)}


def correct_entry(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    """Void an entry that cannot be posted, and record its bill afresh."""
    entry_id = int(find(r"[Jj]ournal entry (\d+)", task, "which entry"))
    entry = use(tools, "get_journal_entry", entry_id=entry_id)
    issues = validate(tools, entry_id, "Checking why the entry cannot be posted")
    if not issues:  # nothing to correct
        return {"journal_entry_id": entry_id}
    documents = entry["accounting_object_ids"]
    if len(documents) != 1:
        raise ValueError(f"entry {entry_id} does not record one bill to record afresh")
    use(
        tools,
        "void_journal_entry",
        entry_id=entry_id,
        reason=(
            f"It cannot be posted ({', '.join(issues)}); a corrected entry for the "
            "same bill replaces it"
        ),
        evidence=[
            f"journal_entry_id={entry_id}",
            f"accounting_object_id={documents[0]}",
            *(f"issue={code}" for code in issues),
        ],
    )
    return {
        "journal_entry_id": record_bill(tools, documents[0]),
        "voided_journal_entry_id": entry_id,
    }


class JournalEntryAgent(WorkflowAgent):
    """Records bills as the company records them, validates entries, and corrects
    invalid ones, leaving its work for a person to approve."""

    name = "journal-entry"
    workflows: ClassVar[Mapping[str, Workflow]] = {
        "JE-001": generate_entry,
        "JE-002": review_entry,
        "JE-003": correct_entry,
    }
