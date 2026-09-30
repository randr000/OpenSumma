"""The duplicate-invoice agent: it finds vendor bills recorded more than once, and
bills paid more than once, through the read-only tools alone. It changes nothing.

A vendor numbers its invoices uniquely, so two bills from one vendor with the same
invoice number are one bill recorded twice, whatever their amounts, dates, or the
channel each arrived through: the copies received after the first are the
duplicates. Invoice numbers are compared ignoring case, spaces, and punctuation,
since differences in keying them are how duplicates usually slip past. A payment
is a duplicate when it is made for a bill already paid in full.
"""

import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from opensumma.agents.common import Books, Finding, Workflow, WorkflowAgent, report
from opensumma.benchmark import TaskPrompt, Tools

Key = tuple[str, str]


def invoice_key(number: str) -> str:
    """An invoice number as it is compared: letters and digits, in lowercase."""
    return re.sub(r"[^0-9a-z]", "", number.casefold())


def document_key(obj: dict[str, Any]) -> Key | None:
    """The vendor and invoice number a bill or payment is for, if it names both:
    the vendor by the name the document gives, which a bill recorded without its
    vendor still carries."""
    data = obj["data"]
    vendor, number = (
        data.get("vendor_name") or obj["counterparty"],
        data.get("invoice_number"),
    )
    if not isinstance(vendor, str) or not isinstance(number, str):
        return None
    return vendor.casefold(), invoice_key(number)


def _by_document(objects: list[dict[str, Any]]) -> dict[Key, list[dict[str, Any]]]:
    """``objects`` by the bill they are for, each group in the order received."""
    groups: defaultdict[Key, list[dict[str, Any]]] = defaultdict(list)
    for obj in objects:
        key = document_key(obj)
        if key is not None:
            groups[key].append(obj)
    for group in groups.values():
        group.sort(key=lambda o: (datetime.fromisoformat(o["occurred_at"]), o["id"]))
    return groups


def duplicate_bills(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    findings = []
    for copies in _by_document(Books(tools).objects("vendor_bill")).values():
        first = copies[0]
        for copy in copies[1:]:
            data = copy["data"]
            findings.append(
                Finding(
                    copy["id"],
                    f"Bill {data['invoice_number']} from {data.get('vendor_name')} "
                    f"was first received on {first['occurred_at'][:10]}, recorded as "
                    f"object {first['id']}, and again on {copy['occurred_at'][:10]}, "
                    f"by {copy['source']}.",
                    (
                        f"accounting_object_id={copy['id']}",
                        f"original_accounting_object_id={first['id']}",
                        f"invoice={data['invoice_number']}",
                        f"amount={data.get('amount')}",
                        f"original_amount={first['data'].get('amount')}",
                    ),
                )
            )
    return report("accounting_object_ids", findings)


def duplicate_payments(task: TaskPrompt, tools: Tools) -> dict[str, Any]:
    books = Books(tools)
    owed = {
        key: Decimal(copies[0]["data"]["amount"])
        for key, copies in _by_document(books.objects("vendor_bill")).items()
    }
    findings = []
    for key, payments in _by_document(books.objects("vendor_payment")).items():
        paid = Decimal("0.00")
        for payment in payments:
            data = payment["data"]
            settled = paid >= owed[key] if key in owed else paid > 0
            if settled:
                findings.append(
                    Finding(
                        payment["id"],
                        f"Bill {data['invoice_number']} was already paid in full "
                        f"({paid}) when payment {data.get('payment_number')} of "
                        f"{data['amount']} was made on {data.get('paid_on')}.",
                        (
                            f"accounting_object_id={payment['id']}",
                            f"invoice={data['invoice_number']}",
                            f"amount_billed={owed.get(key)}",
                            f"amount_paid_before={paid}",
                        ),
                    )
                )
            paid += Decimal(data["amount"])
    return report("accounting_object_ids", findings)


class DuplicateInvoiceAgent(WorkflowAgent):
    """Finds vendor bills recorded more than once, and bills paid more than once."""

    name = "duplicate-invoice"
    workflows: ClassVar[Mapping[str, Workflow]] = {
        "AP-001": duplicate_bills,
        "AP-003": duplicate_payments,
    }
