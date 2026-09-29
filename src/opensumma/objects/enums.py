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

    @property
    def counterparty_kind(self) -> "CounterpartyKind | None":
        """The kind of counterparty an object of this type concerns, if it implies one.

        A vendor bill concerns a vendor and a customer invoice a customer. An expense,
        a bank transaction, or a contract may concern either, or nobody.
        """
        if self in _VENDOR_TYPES:
            return CounterpartyKind.VENDOR
        if self in _CUSTOMER_TYPES:
            return CounterpartyKind.CUSTOMER
        return None


class AccountingObjectStatus(StrEnum):
    """How far an Accounting Object has been processed before accounting for it.

    OBSERVED is how an object arrives. EXTRACTED means its business data has been
    read from the source; CLASSIFIED that its counterparty is settled. VOIDED
    withdraws it. Which transitions are allowed, and who may make them, is the
    workflow engine's to decide.

    Whether an object is recorded in the ledger is never a stored status: it is
    derived from the journal entries that record it (see ``accounting_impact``),
    whose own statuses carry the rest of the accounting flow.
    """

    OBSERVED = "OBSERVED"
    EXTRACTED = "EXTRACTED"
    CLASSIFIED = "CLASSIFIED"
    VOIDED = "VOIDED"


class CounterpartyKind(StrEnum):
    """Whom the company does business with."""

    VENDOR = "VENDOR"
    CUSTOMER = "CUSTOMER"


_VENDOR_TYPES = frozenset(
    {
        AccountingObjectType.VENDOR_BILL,
        AccountingObjectType.VENDOR_PAYMENT,
        AccountingObjectType.PURCHASE_ORDER,
    }
)
_CUSTOMER_TYPES = frozenset(
    {
        AccountingObjectType.CUSTOMER_INVOICE,
        AccountingObjectType.CUSTOMER_PAYMENT,
        AccountingObjectType.SALES_ORDER,
    }
)
