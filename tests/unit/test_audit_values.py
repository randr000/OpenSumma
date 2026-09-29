"""What the audit log can hold, and how an event's hash is computed."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from opensumma.kernel import JournalEntryStatus, LineInput
from opensumma.workflow import (
    ActorType,
    AuditEvent,
    AuditResult,
    audit_hash,
    audit_json,
    evidence_references,
)


def test_values_become_exact_json() -> None:
    line = LineInput("5200", debit=Decimal("310.00"), dimensions={"DEPARTMENT": "ENG"})
    assert audit_json(
        {
            "line": line,
            "on": date(2026, 3, 3),
            "at": datetime(2026, 3, 3, 9, 30, tzinfo=UTC),
            "status": JournalEntryStatus.POSTED,
            "ids": (1, 2),
            "flag": True,
            "nothing": None,
        }
    ) == {
        "line": {
            "account": "5200",
            "debit": "310.00",
            "credit": "0.00",
            "memo": None,
            "dimensions": {"DEPARTMENT": "ENG"},
        },
        "on": "2026-03-03",
        "at": "2026-03-03T09:30:00+00:00",
        "status": "POSTED",
        "ids": [1, 2],
        "flag": True,
        "nothing": None,
    }


def test_a_float_attempt_is_recorded_as_text_not_as_a_float() -> None:
    assert audit_json({"amount": 0.1}) == {"amount": "0.1"}


def test_something_json_cannot_hold_is_refused() -> None:
    with pytest.raises(TypeError, match="cannot record a set"):
        audit_json({"accounts": {"5200"}})


def test_evidence_is_a_short_list_of_short_references() -> None:
    assert evidence_references(["  vendor_id=42 ", "historical_account=6100"]) == [
        "vendor_id=42",
        "historical_account=6100",
    ]
    assert evidence_references(()) == []

    with pytest.raises(TypeError, match="not a single string"):
        evidence_references("vendor_id=42")
    with pytest.raises(TypeError, match="text"):
        evidence_references([42])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="empty"):
        evidence_references([" "])
    with pytest.raises(ValueError, match="200 characters"):
        evidence_references(["x" * 201])
    with pytest.raises(ValueError, match="at most 50"):
        evidence_references([f"entry={n}" for n in range(51)])


def _event(**changes: Any) -> AuditEvent:
    fields: dict[str, Any] = {
        "sequence": 2,
        "occurred_at": datetime(2026, 3, 3, 9, 30, tzinfo=UTC),
        "actor_type": ActorType.AGENT,
        "actor_id": 7,
        "action": "propose_journal_entry",
        "object_type": "journal_entry",
        "object_id": 17,
        "input": {"description": "Stratus INV-88"},
        "output": {"status": "PROPOSED"},
        "result": AuditResult.SUCCEEDED,
        "reason": "Hosting, as for twelve months",
        "evidence": ["vendor=V-STRATUS"],
        "previous_hash": "0" * 64,
    }
    fields.update(changes)
    return AuditEvent(**fields)


def test_the_hash_is_a_sha256_of_the_content() -> None:
    digest = audit_hash(_event())
    assert len(digest) == 64
    assert digest == audit_hash(_event())  # deterministic
    assert digest == audit_hash(_event(input={"description": "Stratus INV-88"}))


@pytest.mark.parametrize(
    "change",
    [
        {"sequence": 3},
        {"occurred_at": datetime(2026, 3, 3, 9, 31, tzinfo=UTC)},
        {"actor_type": ActorType.HUMAN},
        {"actor_id": 8},
        {"action": "approve_journal_entry"},
        {"object_type": "accounting_object"},
        {"object_id": 18},
        {"input": {"description": "Stratus INV-89"}},
        {"output": {"status": "APPROVED"}},
        {"result": AuditResult.REFUSED},
        {"reason": "Hosting"},
        {"evidence": ["vendor=V-PAPER"]},
        {"previous_hash": "1" * 64},
    ],
    ids=lambda change: next(iter(change)),
)
def test_changing_any_field_changes_the_hash(change: dict[str, Any]) -> None:
    assert audit_hash(_event(**change)) != audit_hash(_event())
