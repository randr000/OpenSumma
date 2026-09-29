"""The Accounting Object layer's closed vocabularies.

Object types are lowercase, as the project specification names them, because
agents read and write them as they are. Like the kernel's vocabularies they are
stored as strings and constrained in the database as well as in Python.
"""

from enum import StrEnum


class AccountingObjectType(StrEnum):
    """The kinds of business document or event an Accounting Object represents."""

    VENDOR_BILL = "vendor_bill"
    CUSTOMER_INVOICE = "customer_invoice"
    CUSTOMER_PAYMENT = "customer_payment"
    VENDOR_PAYMENT = "vendor_payment"
    BANK_TRANSACTION = "bank_transaction"
    EXPENSE = "expense"
    PURCHASE_ORDER = "purchase_order"
    SALES_ORDER = "sales_order"
    CONTRACT = "contract"
    # A request for a manual entry, such as an accrual or an adjustment, as a
    # document in its own right; its accounting impact is a kernel journal entry.
    JOURNAL_ENTRY = "journal_entry"
    RECONCILIATION = "reconciliation"


class AccountingObjectStatus(StrEnum):
    """Whether an Accounting Object still stands.

    Phase 4 needs only these two. The workflow engine (Phase 5) adds the states an
    object passes through between being observed and being closed. Whether an
    object is recorded in the ledger is never a stored status: it is derived from
    the journal entries that record it (see ``accounting_impact``).
    """

    OBSERVED = "OBSERVED"
    VOIDED = "VOIDED"
