"""Generating datasets into the books, and checking the books against the ground
truth that describes their errors."""

import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from opensumma.datasets import (
    ERROR_TYPES,
    Dataset,
    books_fingerprint,
    generate_dataset,
    plan_dataset,
    write_dataset,
)
from opensumma.datasets.business import month_end
from opensumma.db import Base, create_engine
from opensumma.kernel import (
    JournalEntry,
    balance_sheet,
    get_journal_entry,
    ledger_lines,
    posted_activity,
    trial_balance,
)
from opensumma.money import ZERO
from opensumma.objects import (
    AccountingObject,
    AccountingObjectStatus,
    AccountingObjectType,
    get_accounting_object,
    search_accounting_objects,
)
from opensumma.workflow import audit_history, verify_audit_log

SIZE, SEED = 400, 7
Generated = tuple[Path, Dataset]


@pytest.fixture(scope="module")
def errored(tmp_path_factory: pytest.TempPathFactory) -> Generated:
    directory = tmp_path_factory.mktemp("errored") / "acme"
    return directory, write_dataset(
        directory, company="acme", transactions=SIZE, seed=SEED, errors=22
    )


@pytest.fixture(scope="module")
def clean(tmp_path_factory: pytest.TempPathFactory) -> Generated:
    directory = tmp_path_factory.mktemp("clean") / "acme"
    return directory, write_dataset(
        directory, company="acme", transactions=SIZE, seed=SEED, errors=0
    )


@contextmanager
def open_books(directory: Path) -> Iterator[Session]:
    engine = create_engine(f"sqlite:///{directory / 'books.db'}")
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@contextmanager
def empty_books() -> Iterator[Session]:
    engine: Engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _net(activity: dict[str, Any]) -> dict[str, Decimal]:
    return {
        account: a.debits - a.credits
        for account, a in activity.items()
        if a.debits != a.credits
    }


def _entry_net(entries: list[dict[str, Any]], through: date) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for entry in entries:
        if date.fromisoformat(entry["entry_date"]) > through:
            continue
        for line in entry["lines"]:
            net = Decimal(line["debit"]) - Decimal(line["credit"])
            totals[line["account"]] = totals.get(line["account"], ZERO) + net
    return totals


def _difference(
    wrong: list[dict[str, Any]], right: list[dict[str, Any]], through: date
) -> dict[str, Decimal]:
    """Each account's net activity in ``wrong`` less that in ``right``."""
    wrong_net, right_net = _entry_net(wrong, through), _entry_net(right, through)
    return {
        account: difference
        for account in sorted({*wrong_net, *right_net})
        if (difference := wrong_net.get(account, ZERO) - right_net.get(account, ZERO))
    }


def test_the_books_hold_exactly_the_plan(errored: Generated) -> None:
    directory, dataset = errored
    planned = plan_dataset(company="acme", transactions=SIZE, seed=SEED, errors=22)
    with open_books(directory) as session:
        entries = session.scalars(select(JournalEntry)).all()
        objects = session.scalars(select(AccountingObject)).all()
    plan = planned.plan
    assert len(entries) == len(plan.transactions)
    documents = sum(t.document is not None for t in plan.transactions)
    bank_lines = sum(t.bank_line is not None for t in plan.transactions)
    assert len(objects) == documents + bank_lines + len(plan.statement_lines)
    assert dataset.manifest["counts"]["journal_entries"] == len(entries)
    assert dataset.manifest["counts"]["accounting_objects"] == len(objects)
    assert {e.status.value for e in entries} == {"POSTED", "REVERSED"}


def test_the_books_balance_at_every_month_end(errored: Generated) -> None:
    directory, dataset = errored
    with open_books(directory) as session:
        for month in range(1, 13):
            end = month_end(2026, month)
            assert trial_balance(session, as_of=end).is_balanced, end
            assert balance_sheet(session, as_of=end).is_balanced, end
    assert dataset.manifest["year_end"]["is_balanced"] is True


def test_the_files_describe_the_books_they_came_with(errored: Generated) -> None:
    directory, dataset = errored
    manifest = json.loads((directory / "manifest.json").read_text())
    ground_truth = json.loads((directory / "ground_truth.json").read_text())
    assert (manifest, ground_truth) == (dataset.manifest, dataset.ground_truth)
    with open_books(directory) as session:
        assert books_fingerprint(session) == manifest["fingerprint"]
    assert ground_truth["fingerprint"] == manifest["fingerprint"]
    assert {
        key: manifest[key] for key in ("company", "year", "seed", "transactions")
    } == {
        "company": "acme",
        "year": 2026,
        "seed": SEED,
        "transactions": SIZE,
    }


def test_every_error_type_is_in_the_ground_truth(errored: Generated) -> None:
    _, dataset = errored
    errors = dataset.ground_truth["errors"]
    assert Counter(e["type"] for e in errors) == dict.fromkeys(ERROR_TYPES, 2)
    assert [e["id"] for e in errors] == [f"ERR-{n:03d}" for n in range(1, 23)]


def test_the_ground_truth_matches_the_books(errored: Generated) -> None:
    directory, dataset = errored
    with open_books(directory) as session:
        for error in dataset.ground_truth["errors"]:
            assert [e["id"] for e in error["actual"]] == error["journal_entry_ids"]
            for recorded in error["actual"]:
                entry = get_journal_entry(session, recorded["id"])
                assert entry.entry_date.isoformat() == recorded["entry_date"]
                assert entry.description == recorded["description"]
                assert [
                    {
                        "account": line.account.code,
                        "debit": str(line.debit),
                        "credit": str(line.credit),
                        "dimensions": {
                            tag.value.dimension.code: tag.value.code
                            for tag in line.dimensions
                        },
                    }
                    for line in entry.lines
                ] == recorded["lines"], error["id"]
            for correct in error["expected"]:
                # An entry the books should hold is either the wrong one, to be
                # corrected, or missing from them altogether.
                assert correct["id"] in (None, *error["journal_entry_ids"])
            for object_id in error["accounting_object_ids"]:
                get_accounting_object(session, object_id)


def test_the_ground_truth_explains_every_difference_from_clean_books(
    errored: Generated, clean: Generated
) -> None:
    errors = errored[1].ground_truth["errors"]
    with open_books(errored[0]) as wrong, open_books(clean[0]) as right:
        for month in range(1, 13):
            end = month_end(2026, month)
            wrong_net = _net(posted_activity(wrong, end=end))
            right_net = _net(posted_activity(right, end=end))
            explained: dict[str, Decimal] = {}
            for error in errors:
                for sign, side in ((1, error["actual"]), (-1, error["expected"])):
                    for account, net in _entry_net(side, end).items():
                        explained[account] = explained.get(account, ZERO) + sign * net
            for account in sorted({*wrong_net, *right_net, *explained}):
                assert wrong_net.get(account, ZERO) - right_net.get(
                    account, ZERO
                ) == explained.get(account, ZERO), (end, account)
    for error in errors:
        as_of = date.fromisoformat(error["misstatement"]["as_of"])
        stated = {a: Decimal(v) for a, v in error["misstatement"]["accounts"].items()}
        assert stated == _difference(error["actual"], error["expected"], as_of)


def test_errors_leave_their_evidence_in_the_documents(errored: Generated) -> None:
    directory, dataset = errored
    by_type: dict[str, list[dict[str, Any]]] = {}
    for error in dataset.ground_truth["errors"]:
        by_type.setdefault(error["type"], []).append(error)
    with open_books(directory) as session:
        for error in by_type["missing_vendor"]:
            bill = get_accounting_object(
                session, error["details"]["accounting_object_id"]
            )
            assert bill.counterparty is None
            assert bill.status is AccountingObjectStatus.EXTRACTED
            assert bill.data["vendor_name"] == error["details"]["vendor_name"]
        for error in by_type["duplicate_invoice"]:
            details = error["details"]
            twins = search_accounting_objects(
                session, data={"invoice_number": details["invoice_number"]}
            )
            bills = [
                t for t in twins if t.object_type is AccountingObjectType.VENDOR_BILL
            ]
            assert {b.id for b in bills} == {
                details["original_accounting_object_id"],
                details["duplicate_accounting_object_id"],
            }
        for error in by_type["unreconciled_transaction"]:
            details = error["details"]
            line = get_accounting_object(session, details["bank_transaction_object_id"])
            assert line.object_type is AccountingObjectType.BANK_TRANSACTION
            assert line.entry_links == [] and line.data["amount"] == details["amount"]
            payments = search_accounting_objects(
                session,
                object_type="customer_payment",
                data={"invoice_number": details["invoice_number"]},
            )
            assert payments == []
        for error in by_type["unusual_transaction"]:
            entry = get_journal_entry(session, error["details"]["journal_entry_id"])
            assert entry.entry_date.weekday() >= 5


def test_each_bank_line_matches_one_cash_movement_in_clean_books(
    clean: Generated,
) -> None:
    directory, _ = clean
    with open_books(directory) as session:
        statement = Counter(
            Decimal(obj.data["amount"])
            for obj in search_accounting_objects(
                session, object_type="bank_transaction"
            )
        )
        opening = session.scalars(
            select(JournalEntry).order_by(JournalEntry.id)
        ).first()
        assert opening is not None
        ledger = Counter(
            line.debit - line.credit
            for line in ledger_lines(session, account_codes=["1111"])
            if line.entry_id != opening.id
        )
    assert statement == ledger


def test_the_same_parameters_generate_the_same_books() -> None:
    results = []
    for seed in (SEED, SEED, SEED + 1):
        with empty_books() as session:
            generated = generate_dataset(
                session, company="acme", transactions=300, seed=seed
            )
            results.append((books_fingerprint(session), generated))
    (first, one), (second, two), (other, three) = results
    assert first == second == one.manifest["fingerprint"]
    assert one == two
    assert other != first and three.ground_truth != one.ground_truth


def test_a_dataset_is_the_same_on_every_machine() -> None:
    # Pinned: a change here means every dataset generated before it changes. If
    # the change is intended, bump GENERATOR_VERSION and update these values.
    with empty_books() as session:
        dataset = generate_dataset(session, company="acme", transactions=300, seed=2026)
    truth = json.dumps(dataset.ground_truth, sort_keys=True).encode()
    assert (
        dataset.manifest["fingerprint"]
        == "sha256:f13205030eb0d2c52926b8b0faeb65a89e4deaf650541bda9f37839ba67639e8"
    )
    assert (
        hashlib.sha256(truth).hexdigest()
        == "82c7634deeac7bee7e3b40fd1411212b91415c083bfcbd2bf1005a774c381fb8"
    )


def test_generating_is_one_audited_action_by_the_system() -> None:
    with empty_books() as session:
        generate_dataset(session, company="acme", transactions=300, seed=1)
        session.commit()
        (event,) = audit_history(session)
        assert (event.action, event.actor, event.result.value) == (
            "generate_dataset",
            None,
            "SUCCEEDED",
        )
        assert event.input == {"company": "acme", "year": 2026, "transactions": 300}
        assert event.output["counts"]["journal_entries"] == 300
        assert verify_audit_log(session).is_intact


def test_a_dataset_is_generated_only_into_empty_books(books: Session) -> None:
    with pytest.raises(ValueError, match="empty books"):
        generate_dataset(books, company="acme", transactions=300, seed=1)


def test_an_existing_dataset_is_not_overwritten(errored: Generated) -> None:
    directory, dataset = errored
    with pytest.raises(FileExistsError):
        write_dataset(directory, company="acme", transactions=300, seed=1)
    with open_books(directory) as session:
        assert books_fingerprint(session) == dataset.manifest["fingerprint"]


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"company": "Acme Corp", "transactions": 300, "seed": 1}, ValueError),
        ({"company": "acme", "transactions": 100, "seed": 1}, ValueError),
        (
            {"company": "acme", "transactions": 300, "seed": 1, "errors": 999},
            ValueError,
        ),
        ({"company": "acme", "transactions": 300, "seed": "1"}, TypeError),
    ],
)
def test_parameters_are_checked_before_anything_is_written(
    tmp_path: Path, arguments: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        write_dataset(tmp_path / "new", **arguments)
    assert not (tmp_path / "new").exists()
