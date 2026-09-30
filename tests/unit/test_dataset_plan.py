"""Planning a company's year of business, before any of it is recorded."""

import hashlib
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

import pytest

from opensumma.datasets import FIXED_TRANSACTIONS, MIN_TRANSACTIONS
from opensumma.datasets.business import month_end, plan_business, statement_date
from opensumma.datasets.model import Plan, Transaction, net_by_account
from opensumma.kernel import DEFAULT_CHART_OF_ACCOUNTS, DEFAULT_DIMENSIONS
from opensumma.money import ZERO
from opensumma.objects import AccountingObjectType

LEAF_ACCOUNTS = {
    spec.code
    for spec in DEFAULT_CHART_OF_ACCOUNTS
    if not any(other.parent == spec.code for other in DEFAULT_CHART_OF_ACCOUNTS)
}
DIMENSION_VALUES = {
    (spec.code, value) for spec in DEFAULT_DIMENSIONS for value, _ in spec.values
}
SCHEDULED_KINDS = {
    "opening_balances",
    "payroll",
    "payroll_funding",
    "payroll_tax",
    "depreciation",
    "prepaid_amortization",
    "bank_fee",
    "accrual",
    "accrual_reversal",
}


@pytest.fixture(scope="module")
def plan() -> Plan:
    return plan_business(transactions=1000, seed=42)


def _documents(plan: Plan, kind: str) -> list[Transaction]:
    return [t for t in plan.transactions if t.kind == kind]


def _data(transaction: Transaction) -> dict[str, object]:
    assert transaction.document is not None
    return dict(transaction.document.data)


@pytest.mark.parametrize("size", [MIN_TRANSACTIONS, 301, 1000, 4321])
def test_a_plan_has_exactly_the_transactions_asked_for(size: int) -> None:
    plan = plan_business(transactions=size, seed=7)
    assert len(plan.transactions) == size
    assert len({t.key for t in plan.transactions}) == size


@pytest.mark.parametrize("size", [MIN_TRANSACTIONS, 1000, 5000])
def test_the_fixed_schedule_is_the_same_size_for_every_company(size: int) -> None:
    plan = plan_business(transactions=size, seed=3)
    scheduled = [t for t in plan.transactions if t.kind in SCHEDULED_KINDS]
    recurring_bills = {
        _data(t)["invoice_number"]
        for t in _documents(plan, "vendor_bill")
        if "service_period" in _data(t)
    }
    their_payments = [
        t
        for t in _documents(plan, "vendor_payment")
        if _data(t)["invoice_number"] in recurring_bills
    ]
    assert len(scheduled) + len(recurring_bills) + len(their_payments) == (
        FIXED_TRANSACTIONS
    )


def test_every_transaction_is_a_valid_journal_entry(plan: Plan) -> None:
    for t in plan.transactions:
        assert len(t.lines) >= 2, t.key
        assert t.is_balanced, t.key
        assert date(2026, 1, 1) <= t.entry_date <= date(2026, 12, 31), t.key
        assert t.description.strip(), t.key
        for line in t.lines:
            assert (line.debit > ZERO) != (line.credit > ZERO), t.key
            assert ZERO in (line.debit, line.credit), t.key
            for amount in (line.debit, line.credit):
                assert amount.as_tuple().exponent == -2, t.key
            assert line.account in LEAF_ACCOUNTS, t.key
            assert set(line.dimensions) <= DIMENSION_VALUES, t.key


def test_the_business_has_every_kind_of_transaction(plan: Plan) -> None:
    kinds = Counter(t.kind for t in plan.transactions)
    assert set(kinds) == SCHEDULED_KINDS | {
        "sale",
        "customer_payment",
        "refund",
        "vendor_bill",
        "vendor_payment",
        "card_expense",
        "adjustment",
    }
    categories = {_data(t)["category"] for t in _documents(plan, "vendor_bill")}
    assert {
        "rent",
        "hosting",
        "software_subscription",
        "legal",
        "insurance",
        "inventory",
        "equipment",
    } <= categories
    classes = {_data(t)["class"] for t in _documents(plan, "sale")}
    assert classes == {"PRODUCT", "SERVICES"}


def test_the_same_seed_gives_the_same_plan() -> None:
    first = plan_business(transactions=1000, seed=42)
    assert plan_business(transactions=1000, seed=42) == first
    assert plan_business(transactions=1000, seed=43) != first
    assert plan_business(transactions=1000, seed=42, year=2027) != first


def test_a_plan_does_not_depend_on_hash_randomization() -> None:
    program = (
        "import hashlib; from opensumma.datasets.business import plan_business; "
        "plan = plan_business(transactions=600, seed=5); "
        "print(hashlib.sha256(repr(plan).encode()).hexdigest())"
    )
    digests = {
        subprocess.run(
            [sys.executable, "-c", program],
            env={"PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2")
    }
    here = hashlib.sha256(repr(plan_business(transactions=600, seed=5)).encode())
    assert digests == {here.hexdigest() + "\n"}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"transactions": MIN_TRANSACTIONS - 1}, "300 to"),
        ({"transactions": 500_001}, "300 to"),
        ({"transactions": 1000, "year": 1800}, "plausible year"),
    ],
)
def test_impossible_plans_are_refused(arguments: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        plan_business(seed=1, **arguments)


def test_every_movement_of_cash_is_on_the_bank_statement(plan: Plan) -> None:
    references = []
    for t in plan.transactions:
        cash = sum((line.net for line in t.lines if line.account == "1111"), ZERO)
        posted = statement_date(t)
        if t.kind == "opening_balances":
            assert posted is None
            continue
        assert (cash != ZERO) == (posted is not None), t.key
        if posted is None:
            continue
        if t.bank_line is not None:
            amount, reference = t.bank_line.amount, t.bank_line.reference
        else:
            data = _data(t)
            assert t.document is not None
            assert t.document.object_type is AccountingObjectType.BANK_TRANSACTION
            amount, reference = Decimal(str(data["amount"])), str(data["reference"])
        assert amount == cash, t.key
        assert t.entry_date <= posted <= date(2026, 12, 31), t.key
        assert posted - t.entry_date <= timedelta(days=6), t.key
        references.append(reference)
    assert len(references) == len(set(references))


@pytest.mark.parametrize(("size", "seed"), [(300, 1), (300, 2024), (1000, 42)])
def test_cash_and_inventory_never_run_out(size: int, seed: int) -> None:
    plan = plan_business(transactions=size, seed=seed)
    balances = {"1111": ZERO, "1112": ZERO, "1140": ZERO}
    by_day: dict[date, list[Transaction]] = {}
    for t in plan.transactions:
        by_day.setdefault(t.entry_date, []).append(t)
    for day in sorted(by_day):
        for t in by_day[day]:
            for line in t.lines:
                if line.account in balances:
                    balances[line.account] += line.net
        assert min(balances.values()) >= ZERO, (day, balances)


def test_the_control_accounts_equal_their_open_items(plan: Plan) -> None:
    net = net_by_account(plan.transactions)

    def open_amount(document_kind: str, settlement_kind: str) -> Decimal:
        settled = {
            str(_data(t)["invoice_number"]) for t in _documents(plan, settlement_kind)
        }
        return sum(
            (
                Decimal(str(_data(t)["amount"]))
                for t in _documents(plan, document_kind)
                if _data(t)["invoice_number"] not in settled
            ),
            ZERO,
        )

    assert -net["2110"] == open_amount("vendor_bill", "vendor_payment")
    assert net["1120"] == open_amount("sale", "customer_payment")


def test_accruals_reverse_on_the_first_of_the_next_month(plan: Plan) -> None:
    accruals = _documents(plan, "accrual")
    reversals = {t.reverses: t for t in _documents(plan, "accrual_reversal")}
    assert [a.entry_date for a in accruals] == [
        month_end(2026, m) for m in range(1, 13)
    ]
    for accrual in accruals[:-1]:
        reversal = reversals[accrual.key]
        assert reversal.entry_date == accrual.entry_date + timedelta(days=1)
        assert [(line.account, line.debit, line.credit) for line in reversal.lines] == [
            (line.account, line.credit, line.debit) for line in accrual.lines
        ]
    assert accruals[-1].key not in reversals  # December's stays at year end


def test_documents_carry_what_their_entries_record(plan: Plan) -> None:
    for t in plan.transactions:
        if t.kind in ("vendor_bill", "vendor_payment", "customer_payment", "refund"):
            assert t.document is not None and t.document.counterparty, t.key
            assert Decimal(str(_data(t)["amount"])) == t.amount, t.key
        if t.kind == "sale":
            assert Decimal(str(_data(t)["amount"])) == t.lines[0].debit
        if t.kind in ("vendor_bill", "card_expense") and t.lines[0].dimensions:
            data = _data(t)
            assert t.lines[0].dimension("DEPARTMENT") == data["department"], t.key
            assert t.lines[0].dimension("LOCATION") == data["location"], t.key


def test_document_numbers_are_unique() -> None:
    plan = plan_business(transactions=10_000, seed=9)
    for kind, field in [
        ("vendor_bill", "invoice_number"),
        ("sale", "invoice_number"),
        ("vendor_payment", "payment_number"),
        ("customer_payment", "receipt_number"),
    ]:
        numbers = [_data(t)[field] for t in _documents(plan, kind)]
        assert len(numbers) == len(set(numbers)), kind
    invoices = _documents(plan, "sale")
    assert [_data(t)["invoice_number"] for t in invoices] == [
        f"INV-26-{n:05d}" for n in range(1, len(invoices) + 1)
    ]


@pytest.mark.parametrize("seed", [1, 42, 2024])
def test_the_company_earns_a_plausible_margin(seed: int) -> None:
    net = net_by_account(plan_business(transactions=1000, seed=seed).transactions)
    revenue = -sum((v for a, v in net.items() if a.startswith("4")), ZERO)
    expenses = sum((v for a, v in net.items() if a[0] in "56"), ZERO)
    assert revenue > ZERO
    assert abs(revenue - expenses) < revenue / 4
    assert max(net, key=lambda a: net[a] if a[0] in "56" else ZERO) == "6300"
