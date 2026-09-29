"""Request and response bodies for the REST and MCP interfaces.

Amounts cross the interface as strings, such as ``"120.50"``, in both directions: a
JSON number is refused, because it would arrive as a binary float. Requests forbid
fields the schema does not name, so a misspelt or invented field is an error rather
than silently ignored.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Amount = Annotated[str, StringConstraints(pattern=r"^-?\d+(\.\d+)?$")]

# The most audit events one read returns; read further pages with ``after``.
AUDIT_PAGE_LIMIT = 1000


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Requests ------------------------------------------------------------------------


class Action(Request):
    """A workflow action's concise reason and evidence references.

    Rejecting, voiding, and reopening require a reason; the workflow says so, and
    the refusal is audited, if it is missing.
    """

    reason: str | None = None
    evidence: list[str] = Field(default_factory=list)


class LineIn(Request):
    account: str
    debit: Amount = "0.00"
    credit: Amount = "0.00"
    memo: str | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)


class Proposal(Action):
    entry_date: date
    description: str
    lines: list[LineIn]
    accounting_object_id: int | None = None


class Reversal(Action):
    entry_date: date
    description: str | None = None


class Observation(Action):
    object_type: str
    occurred_at: datetime
    source: str
    counterparty: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Extraction(Action):
    data: dict[str, Any]


class Classification(Action):
    counterparty: str | None = None


# --- Responses -----------------------------------------------------------------------


class Health(BaseModel):
    status: str


class AccountOut(BaseModel):
    code: str
    name: str
    account_type: str
    normal_balance: str
    parent: str | None
    is_active: bool
    is_postable: bool


class BalanceOut(BaseModel):
    account_code: str
    account_name: str
    account_type: str
    normal_balance: str
    as_of: date | None
    debits: Decimal
    credits: Decimal
    balance: Decimal


class PeriodOut(BaseModel):
    code: str
    start_date: date
    end_date: date
    status: str


class DimensionValueOut(BaseModel):
    code: str
    name: str
    is_active: bool


class DimensionOut(BaseModel):
    code: str
    name: str
    values: list[DimensionValueOut]


class CounterpartyOut(BaseModel):
    code: str
    name: str
    kind: str
    is_active: bool


class LineOut(BaseModel):
    line_number: int
    account: str
    debit: Decimal
    credit: Decimal
    memo: str | None
    dimensions: dict[str, str]


class JournalEntryOut(BaseModel):
    id: int
    entry_date: date
    description: str
    status: str
    posted_at: datetime | None
    reversal_of: int | None
    reversed_by: int | None
    total_debits: Decimal
    total_credits: Decimal
    lines: list[LineOut]
    accounting_object_ids: list[int]


class IssueOut(BaseModel):
    code: str
    line_number: int | None
    message: str


class ValidationOut(BaseModel):
    journal_entry_id: int
    is_valid: bool
    issues: list[IssueOut]


class LedgerLineOut(BaseModel):
    entry_id: int
    entry_date: date
    line_number: int
    account_code: str
    description: str
    memo: str | None
    debit: Decimal
    credit: Decimal
    dimensions: dict[str, str]


class TrialBalanceLineOut(BaseModel):
    account_code: str
    account_name: str
    account_type: str
    debit: Decimal
    credit: Decimal


class TrialBalanceOut(BaseModel):
    as_of: date
    lines: list[TrialBalanceLineOut]
    total_debits: Decimal
    total_credits: Decimal
    is_balanced: bool


class StatementLineOut(BaseModel):
    account_code: str
    account_name: str
    depth: int
    amount: Decimal
    is_leaf: bool


class StatementSectionOut(BaseModel):
    account_type: str
    lines: list[StatementLineOut]
    total: Decimal


class IncomeStatementOut(BaseModel):
    start: date
    end: date
    revenue: StatementSectionOut
    expenses: StatementSectionOut
    net_income: Decimal


class BalanceSheetOut(BaseModel):
    as_of: date
    assets: StatementSectionOut
    liabilities: StatementSectionOut
    equity: StatementSectionOut
    unclosed_net_income: Decimal
    total_equity: Decimal
    total_liabilities_and_equity: Decimal
    is_balanced: bool


class GeneralLedgerLineOut(BaseModel):
    entry_id: int
    entry_date: date
    line_number: int
    description: str
    memo: str | None
    debit: Decimal
    credit: Decimal
    dimensions: dict[str, str]
    balance: Decimal


class GeneralLedgerAccountOut(BaseModel):
    account_code: str
    account_name: str
    normal_balance: str
    opening_balance: Decimal
    lines: list[GeneralLedgerLineOut]
    closing_balance: Decimal


class GeneralLedgerOut(BaseModel):
    start: date
    end: date
    accounts: list[GeneralLedgerAccountOut]


class BusinessEventOut(BaseModel):
    event_type: str
    occurred_at: datetime
    source: str
    data: dict[str, Any]


class AccountingObjectOut(BaseModel):
    id: int
    object_type: str
    status: str
    occurred_at: datetime
    source: str
    counterparty: str | None
    data: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    events: list[BusinessEventOut]
    journal_entry_ids: list[int]
    has_net_impact: bool


class AuditEventOut(BaseModel):
    sequence: int
    occurred_at: datetime
    actor_type: str
    actor: str | None
    action: str
    object_type: str | None
    object_id: int | None
    input: dict[str, Any]
    output: dict[str, Any]
    result: str
    reason: str | None
    evidence: list[str]
    hash: str
