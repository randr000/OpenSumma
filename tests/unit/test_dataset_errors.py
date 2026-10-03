"""Injecting known errors into a plan, and the ground truth describing each."""

from collections import Counter
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

import pytest

from opensumma.datasets import ERROR_TYPES, default_error_count
from opensumma.datasets.business import month_end, plan_business
from opensumma.datasets.errors import (
    WRONG_ACCOUNTS,
    InjectedError,
    Ref,
    inject_errors,
)
from opensumma.datasets.model import Plan, Transaction, net_by_account
from opensumma.money import ZERO
from opensumma.objects import AccountingObjectStatus

THREE_EACH = 3 * len(ERROR_TYPES)


@pytest.fixture(scope="module")
def clean() -> Plan:
    return plan_business(transactions=1000, seed=42)


@pytest.fixture(scope="module")
def injected(clean: Plan) -> tuple[Plan, list[InjectedError]]:
    return inject_errors(clean, seed=42, count=THREE_EACH)


def _of(errors: list[InjectedError], error_type: str) -> list[InjectedError]:
    found = [e for e in errors if e.error_type == error_type]
    assert found, error_type
    return found


def _data(transaction: Transaction) -> dict[str, object]:
    assert transaction.document is not None
    return dict(transaction.document.data)


def test_the_default_is_one_error_per_hundred_and_one_of_each_type() -> None:
    assert default_error_count(300) == len(ERROR_TYPES)
    assert default_error_count(1000) == len(ERROR_TYPES)
    assert default_error_count(10_000) == 100


def test_errors_are_spread_over_every_type(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    _, errors = injected
    assert Counter(e.error_type for e in errors) == dict.fromkeys(ERROR_TYPES, 3)

    _, many = inject_errors(
        plan_business(transactions=10_000, seed=1), seed=1, count=100
    )
    counts = Counter(e.error_type for e in many)
    assert set(counts) == set(ERROR_TYPES) and len(many) == 100
    assert counts["missing_accrual"] == 3  # a quarter of the twelve months at most


def test_no_errors_leaves_the_plan_as_it_was(clean: Plan) -> None:
    assert inject_errors(clean, seed=42, count=0) == (clean, [])


def test_too_many_errors_are_refused(clean: Plan) -> None:
    with pytest.raises(ValueError, match="at most"):
        inject_errors(clean, seed=42, count=10_000)
    with pytest.raises(ValueError):
        inject_errors(clean, seed=42, count=-1)


def test_the_same_seed_injects_the_same_errors(clean: Plan) -> None:
    assert inject_errors(clean, seed=42, count=11) == inject_errors(
        clean, seed=42, count=11
    )
    assert inject_errors(clean, seed=42, count=11) != inject_errors(
        clean, seed=43, count=11
    )


def test_the_plans_differ_by_exactly_the_errors(
    clean: Plan, injected: tuple[Plan, list[InjectedError]]
) -> None:
    errored, errors = injected
    before = {t.key: t for t in clean.transactions}
    after = {t.key: t for t in errored.transactions}
    actual = {t.key: t for e in errors for t in e.actual}
    expected = {t.key: t for e in errors for t in e.expected}
    for key, transaction in after.items():
        assert transaction == actual.get(key, before.get(key)), key
    for key, transaction in before.items():
        assert transaction == expected.get(key, after.get(key)), key
    assert set(actual) <= set(after)
    assert not (set(expected) - set(actual)) & set(after)


def test_each_transaction_takes_part_in_one_error_at_most(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    _, errors = injected
    seen: set[str] = set()
    for error in errors:
        keys = {t.key for t in (*error.actual, *error.expected)}
        assert not keys & seen
        seen |= keys


def _difference(
    wrong: Sequence[Transaction], right: Sequence[Transaction], through: date
) -> dict[str, Decimal]:
    """Each account's net activity in ``wrong`` less that in ``right``."""
    wrong_net = net_by_account(wrong, through=through)
    right_net = net_by_account(right, through=through)
    return {
        account: difference
        for account in sorted({*wrong_net, *right_net})
        if (difference := wrong_net.get(account, ZERO) - right_net.get(account, ZERO))
    }


def test_the_misstatements_add_up_to_the_difference_between_the_books(
    clean: Plan, injected: tuple[Plan, list[InjectedError]]
) -> None:
    errored, errors = injected
    for month in range(1, 13):
        end = month_end(2026, month)
        explained: dict[str, Decimal] = {}
        for error in errors:
            for account, net in _difference(error.actual, error.expected, end).items():
                explained[account] = explained.get(account, ZERO) + net
        explained = {account: net for account, net in explained.items() if net}
        assert _difference(errored.transactions, clean.transactions, end) == dict(
            sorted(explained.items())
        ), end
    for error in errors:
        assert error.misstatement() == _difference(
            error.actual, error.expected, error.as_of
        )


def test_errors_are_ordered_by_the_period_they_misstate(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    _, errors = injected
    assert [e.as_of for e in errors] == sorted(e.as_of for e in errors)


def test_a_duplicate_invoice_repeats_a_bill_received_again(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    errored, errors = injected
    for error in _of(errors, "duplicate_invoice"):
        (duplicate,) = error.actual
        assert error.expected == ()
        original = errored.transaction(error.details["original_journal_entry_id"].key)
        assert original.document is not None and duplicate.document is not None
        assert duplicate.lines == original.lines
        assert duplicate.entry_date == original.entry_date
        assert duplicate.document.data == original.document.data
        assert duplicate.document.source != original.document.source
        assert duplicate.document.occurred_at > original.document.occurred_at


def test_a_wrong_account_differs_from_the_right_one_only_in_its_account(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    _, errors = injected
    for error in _of(errors, "wrong_gl_account"):
        (posted,), (correct,) = error.actual, error.expected
        details = error.details
        assert WRONG_ACCOUNTS[details["correct_account"]] == details["posted_account"]
        assert posted.lines[0].account == details["posted_account"]
        assert correct.lines[0].account == details["correct_account"]
        assert posted.lines[1:] == correct.lines[1:]
        assert posted.lines[0].amount == correct.lines[0].amount


@pytest.mark.parametrize("dimension", ["DEPARTMENT", "LOCATION"])
def test_a_wrong_dimension_contradicts_the_document(
    injected: tuple[Plan, list[InjectedError]], dimension: str
) -> None:
    _, errors = injected
    name = dimension.lower()
    for error in _of(errors, f"wrong_{name}"):
        (posted,), (correct,) = error.actual, error.expected
        assert posted.lines[0].dimension(dimension) == error.details[f"posted_{name}"]
        assert correct.lines[0].dimension(dimension) == error.details[f"correct_{name}"]
        assert _data(posted)[name] == error.details[f"correct_{name}"]
        assert error.misstatement() == {}


def test_a_wrong_period_moves_the_entry_across_a_month_end(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    _, errors = injected
    for error in _of(errors, "wrong_accounting_period"):
        (posted,), (correct,) = error.actual, error.expected
        assert (posted.entry_date.year, posted.entry_date.month) != (
            correct.entry_date.year,
            correct.entry_date.month,
        )
        assert abs(posted.entry_date - correct.entry_date) < timedelta(days=14)
        assert _data(posted)["invoice_date"] == correct.entry_date.isoformat()
        assert posted.lines == correct.lines


def test_a_missing_accrual_leaves_out_the_accrual_and_its_reversal(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    errored, errors = injected
    keys = {t.key for t in errored.transactions}
    for error in _of(errors, "missing_accrual"):
        assert error.actual == ()
        accrual, *reversal = error.expected
        assert accrual.kind == "accrual"
        assert [t.reverses for t in reversal] == (
            [] if accrual.entry_date.month == 12 else [accrual.key]
        )
        assert not {t.key for t in error.expected} & keys
        assert error.details["amount"] == str(accrual.amount)


def test_a_duplicate_payment_pays_the_same_bill_again_later(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    errored, errors = injected
    for error in _of(errors, "duplicate_payment"):
        (duplicate,) = error.actual
        original = errored.transaction(error.details["original_journal_entry_id"].key)
        assert duplicate.entry_date > original.entry_date
        assert duplicate.lines == original.lines
        assert _data(duplicate)["invoice_number"] == _data(original)["invoice_number"]
        assert _data(duplicate)["payment_number"] != _data(original)["payment_number"]
        assert duplicate.bank_line is not None and original.bank_line is not None
        assert duplicate.bank_line.amount == original.bank_line.amount
        assert duplicate.bank_line.reference != original.bank_line.reference


def test_an_unreconciled_transaction_is_on_the_statement_but_not_in_the_books(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    errored, errors = injected
    for error in _of(errors, "unreconciled_transaction"):
        (payment,) = error.expected
        assert payment.kind == "customer_payment" and error.actual == ()
        assert payment.key not in {t.key for t in errored.transactions}
        (line,) = [s for s in errored.statement_lines if s.key == payment.key]
        assert line.bank_line == payment.bank_line
        assert error.details["bank_transaction_object_id"] == Ref("bank", payment.key)


def test_an_unusual_transaction_is_large_and_on_a_weekend(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    _, errors = injected
    for error in _of(errors, "unusual_transaction"):
        (charge,) = error.actual
        assert error.expected == ()
        assert charge.entry_date.weekday() >= 5
        assert charge.amount >= 25 * Decimal(error.details["typical_maximum"])
        assert charge.amount >= Decimal("10000.00")
        assert charge.lines[0].account == "6500"


def test_an_incorrect_amount_swaps_two_digits_of_the_bill(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    _, errors = injected
    for error in _of(errors, "incorrect_amount"):
        (posted,), (correct,) = error.actual, error.expected
        document_amount = str(_data(posted)["amount"])
        assert document_amount == error.details["correct_amount"] == str(correct.amount)
        assert str(posted.amount) == error.details["posted_amount"] != document_amount
        digits = document_amount.replace(".", "")
        swapped = str(posted.amount).replace(".", "")
        assert sorted(digits) == sorted(swapped)
        assert sum(a != b for a, b in zip(digits, swapped, strict=True)) == 2
        assert posted.is_balanced


def test_a_missing_vendor_leaves_the_bill_unclassified(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    _, errors = injected
    for error in _of(errors, "missing_vendor"):
        (posted,), (correct,) = error.actual, error.expected
        assert posted.document is not None and correct.document is not None
        assert posted.document.counterparty is None
        assert posted.document.status is AccountingObjectStatus.EXTRACTED
        assert correct.document.counterparty == error.details["vendor"]
        assert posted.lines == correct.lines
        assert _data(posted)["vendor_name"] == error.details["vendor_name"]


def test_error_dates_stay_within_the_year(
    injected: tuple[Plan, list[InjectedError]],
) -> None:
    errored, _ = injected
    for t in errored.transactions:
        assert date(2026, 1, 1) <= t.entry_date <= date(2026, 12, 31)
        if t.bank_line is not None:
            assert t.bank_line.posted_on <= date(2026, 12, 31)
