"""The REST interface's workflow endpoints: proposals, validation, approval,
posting, reversal, objects, periods, and how refusals are answered and audited."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from opensumma.db import create_engine
from opensumma.workflow import AuditResult, audit_history

Auth = Callable[[str], dict[str, str]]

AWS = {
    "entry_date": "2026-03-15",
    "description": "AWS, March",
    "lines": [
        {"account": "6100", "debit": "120.50", "dimensions": {"DEPARTMENT": "ENG"}},
        {"account": "2110", "credit": "120.50"},
    ],
}


def _propose(api: TestClient, auth: Auth, body: dict[str, Any] | None = None) -> int:
    response = api.post("/journal-entries", json=body or AWS, headers=auth("agent"))
    assert response.status_code == 201, response.json()
    entry_id: int = response.json()["id"]
    return entry_id


def _step(
    api: TestClient, auth: Auth, entry_id: int, step: str, actor: str, **body: Any
) -> Any:
    return api.post(
        f"/journal-entries/{entry_id}/{step}", json=body, headers=auth(actor)
    )


def _audited(api_url: str, action: str) -> list[tuple[str | None, str]]:
    """What the audit log holds for ``action``, read afresh from the database."""
    engine = create_engine(api_url)
    with Session(engine) as session:
        found = [
            (e.actor.code if e.actor else None, e.result.value)
            for e in audit_history(session, action=action)
        ]
    engine.dispose()
    return found


def test_an_entry_goes_from_proposal_to_the_ledger(api: TestClient, auth: Auth) -> None:
    created = api.post(
        "/journal-entries",
        json={**AWS, "reason": "Hosting", "evidence": ["vendor_id=42"]},
        headers=auth("agent"),
    )
    assert created.status_code == 201
    entry = created.json()
    assert (entry["status"], entry["total_debits"]) == ("PROPOSED", "120.50")
    entry_id = entry["id"]

    validation = _step(api, auth, entry_id, "validate", "agent").json()
    assert validation == {"journal_entry_id": entry_id, "is_valid": True, "issues": []}

    for step, actor, status in [
        ("submit", "agent", "PENDING_APPROVAL"),
        ("approve", "controller", "APPROVED"),
        ("post", "poster", "POSTED"),
    ]:
        response = _step(api, auth, entry_id, step, actor)
        assert (response.status_code, response.json()["status"]) == (200, status)

    trial = api.get(
        "/reports/trial-balance", params={"as_of": "2026-03-31"}, headers=auth("reader")
    ).json()
    assert [(line["account_code"], line["debit"]) for line in trial["lines"]] == [
        ("2110", "0.00"),
        ("6100", "120.50"),
    ]

    reversal = _step(
        api, auth, entry_id, "reverse", "poster", entry_date="2026-03-31", reason="Dup"
    )
    assert reversal.status_code == 201
    assert (reversal.json()["reversal_of"], reversal.json()["status"]) == (
        entry_id,
        "POSTED",
    )
    reversed_entry = api.get(f"/journal-entries/{entry_id}", headers=auth("reader"))
    assert reversed_entry.json()["reversed_by"] == reversal.json()["id"]


def test_amounts_are_strings_and_a_float_never_gets_in(
    api: TestClient, auth: Auth
) -> None:
    body = {**AWS, "lines": [{"account": "6100", "debit": 120.5}, AWS["lines"][1]]}
    response = api.post("/journal-entries", json=body, headers=auth("agent"))
    assert response.status_code == 422
    assert response.json()["error"] == "RequestValidationError"
    assert response.json()["details"][0]["loc"] == ["body", "lines", 0, "debit"]


def test_unknown_fields_are_refused_not_ignored(api: TestClient, auth: Auth) -> None:
    response = api.post(
        "/journal-entries", json={**AWS, "approved": True}, headers=auth("agent")
    )
    assert response.status_code == 422


def test_an_entry_the_kernel_cannot_record_is_refused_with_its_codes(
    api: TestClient, api_url: str, auth: Auth
) -> None:
    body = {
        **AWS,
        "lines": [
            {"account": "9999", "debit": "10.00"},
            {"account": "2110", "credit": "10.005"},
        ],
    }
    response = api.post("/journal-entries", json=body, headers=auth("agent"))
    assert response.status_code == 422
    assert response.json()["error"] == "JournalEntryError"
    assert [issue["code"] for issue in response.json()["issues"]] == [
        "UNKNOWN_ACCOUNT",
        "INVALID_AMOUNT",
    ]
    assert _audited(api_url, "propose_journal_entry") == [("agent", "REFUSED")]


def test_validation_reports_every_issue(api: TestClient, auth: Auth) -> None:
    entry_id = _propose(
        api,
        auth,
        {
            **AWS,
            "lines": [
                {"account": "6000", "debit": "120.50"},
                {"account": "2110", "credit": "100.00"},
            ],
        },
    )
    result = _step(api, auth, entry_id, "validate", "agent").json()
    assert result["is_valid"] is False
    assert [(i["code"], i["line_number"]) for i in result["issues"]] == [
        ("UNBALANCED", None),
        ("ACCOUNT_NOT_POSTABLE", 1),
    ]

    refused = _step(api, auth, entry_id, "submit", "agent")
    assert (refused.status_code, refused.json()["error"]) == (422, "JournalEntryError")


@pytest.mark.parametrize(
    ("step", "actor", "status", "error", "detail"),
    [
        ("approve", "agent", 409, "InvalidTransitionError", ("status", "PROPOSED")),
        ("post", "controller", 409, "InvalidTransitionError", ("status", "PROPOSED")),
        (
            "validate",
            "reader",
            403,
            "PermissionDeniedError",
            ("permission", "PROPOSER"),
        ),
        ("reject", "controller", 409, "InvalidTransitionError", ("action", "reject")),
    ],
)
def test_a_refused_step_is_answered_in_the_audit_logs_terms(
    api: TestClient,
    auth: Auth,
    step: str,
    actor: str,
    status: int,
    error: str,
    detail: tuple[str, str],
) -> None:
    entry_id = _propose(api, auth)
    response = _step(api, auth, entry_id, step, actor, reason="Checking")
    assert response.status_code == status
    body = response.json()
    assert body["error"] == error
    assert body[detail[0]] == detail[1]


def test_approval_needs_an_approver_other_than_the_preparer(
    api: TestClient, auth: Auth
) -> None:
    entry_id = _propose(api, auth)
    _step(api, auth, entry_id, "submit", "agent")

    denied = _step(api, auth, entry_id, "approve", "agent")
    assert (denied.status_code, denied.json()["permission"]) == (403, "APPROVER")

    approved = _step(api, auth, entry_id, "approve", "controller")
    assert approved.json()["status"] == "APPROVED"


def test_posting_needs_approval_and_a_poster(api: TestClient, auth: Auth) -> None:
    entry_id = _propose(api, auth)
    _step(api, auth, entry_id, "submit", "agent")
    early = _step(api, auth, entry_id, "post", "poster")
    assert (early.status_code, early.json()["status"]) == (409, "PENDING_APPROVAL")

    _step(api, auth, entry_id, "approve", "controller")
    wrong = _step(api, auth, entry_id, "post", "controller")
    assert (wrong.status_code, wrong.json()["permission"]) == (403, "POSTER")
    assert _step(api, auth, entry_id, "post", "poster").json()["status"] == "POSTED"


def test_a_refusal_is_committed_to_the_audit_log(
    api: TestClient, api_url: str, auth: Auth
) -> None:
    entry_id = _propose(api, auth)
    _step(api, auth, entry_id, "submit", "agent")
    assert _step(api, auth, entry_id, "approve", "agent").status_code == 403
    assert _step(api, auth, entry_id, "approve", "controller").status_code == 200

    assert _audited(api_url, "approve_journal_entry") == [
        ("agent", AuditResult.REFUSED.value),
        ("controller", AuditResult.SUCCEEDED.value),
    ]


def test_a_malformed_request_never_reaches_the_workflow(
    api: TestClient, api_url: str, auth: Auth
) -> None:
    response = api.post(
        "/journal-entries", json={"description": "x"}, headers=auth("agent")
    )
    assert response.status_code == 422
    assert _audited(api_url, "propose_journal_entry") == []


def test_rejecting_and_voiding_need_a_reason(api: TestClient, auth: Auth) -> None:
    entry_id = _propose(api, auth)
    _step(api, auth, entry_id, "submit", "agent")

    without = _step(api, auth, entry_id, "reject", "controller")
    assert (without.status_code, without.json()["error"]) == (422, "ValueError")
    rejected = _step(api, auth, entry_id, "reject", "controller", reason="Use 5200")
    assert rejected.json()["status"] == "PROPOSED"

    assert _step(api, auth, entry_id, "void", "agent").status_code == 422
    voided = _step(api, auth, entry_id, "void", "agent", reason="Replaced")
    assert voided.json()["status"] == "VOIDED"


def test_an_unknown_entry_is_not_found(api: TestClient, auth: Auth) -> None:
    response = _step(api, auth, 999, "approve", "controller")
    assert (response.status_code, response.json()["error"]) == (
        404,
        "UnknownJournalEntryError",
    )


def test_an_agent_takes_a_bill_through_the_workflow(
    api: TestClient, auth: Auth
) -> None:
    observed = api.post(
        "/accounting-objects",
        json={
            "object_type": "vendor_bill",
            "occurred_at": "2026-03-03T09:30:00Z",
            "source": "email",
            "data": {"raw": "PAPER TRAIL / INV-5 / 45.00"},
        },
        headers=auth("agent"),
    )
    assert observed.status_code == 201
    bill_id = observed.json()["id"]

    extracted = api.post(
        f"/accounting-objects/{bill_id}/extract",
        json={"data": {"invoice_number": "INV-5", "amount": "45.00"}},
        headers=auth("agent"),
    )
    assert extracted.json()["status"] == "EXTRACTED"

    wrong_kind = api.post(
        f"/accounting-objects/{bill_id}/classify",
        json={"counterparty": "C-HELIO"},
        headers=auth("agent"),
    )
    assert (wrong_kind.status_code, wrong_kind.json()["error"]) == (
        422,
        "CounterpartyKindError",
    )
    classified = api.post(
        f"/accounting-objects/{bill_id}/classify",
        json={"counterparty": "V-PAPER"},
        headers=auth("agent"),
    )
    assert (classified.json()["status"], classified.json()["counterparty"]) == (
        "CLASSIFIED",
        "V-PAPER",
    )

    entry = api.post(
        "/journal-entries",
        json={
            "entry_date": "2026-03-03",
            "description": "Paper Trail INV-5",
            "lines": [
                {"account": "6700", "debit": "45.00"},
                {"account": "2110", "credit": "45.00"},
            ],
            "accounting_object_id": bill_id,
        },
        headers=auth("agent"),
    )
    assert entry.json()["accounting_object_ids"] == [bill_id]
    bill = api.get(f"/accounting-objects/{bill_id}", headers=auth("reader")).json()
    assert bill["journal_entry_ids"] == [entry.json()["id"]]

    in_use = api.post(
        f"/accounting-objects/{bill_id}/void",
        json={"reason": "Duplicate"},
        headers=auth("controller"),
    )
    assert (in_use.status_code, in_use.json()["error"]) == (
        409,
        "ObjectHasAccountingImpactError",
    )
    assert in_use.json()["entry_ids"] == [entry.json()["id"]]


def test_a_float_in_business_data_is_refused(api: TestClient, auth: Auth) -> None:
    response = api.post(
        "/accounting-objects",
        json={
            "object_type": "expense",
            "occurred_at": "2026-03-03T09:30:00Z",
            "source": "card",
            "data": {"amount": 12.5},
        },
        headers=auth("agent"),
    )
    assert (response.status_code, response.json()["error"]) == (422, "TypeError")


def test_a_naive_timestamp_is_refused(api: TestClient, auth: Auth) -> None:
    response = api.post(
        "/accounting-objects",
        json={
            "object_type": "expense",
            "occurred_at": "2026-03-03T09:30:00",
            "source": "card",
        },
        headers=auth("agent"),
    )
    assert (response.status_code, response.json()["error"]) == (422, "ValueError")


def test_periods_close_in_order_by_an_admin(api: TestClient, auth: Auth) -> None:
    denied = api.post("/periods/2026-01/close", headers=auth("controller"))
    assert (denied.status_code, denied.json()["permission"]) == (403, "ADMIN")

    early = api.post("/periods/2026-02/close", headers=auth("admin"))
    assert (early.status_code, early.json()["period_codes"]) == (409, ["2026-01"])

    closed = api.post("/periods/2026-01/close", headers=auth("admin"))
    assert closed.json()["status"] == "CLOSED"

    no_reason = api.post("/periods/2026-01/reopen", headers=auth("admin"))
    assert no_reason.status_code == 422
    reopened = api.post(
        "/periods/2026-01/reopen",
        json={"reason": "Late invoice"},
        headers=auth("admin"),
    )
    assert reopened.json()["status"] == "OPEN"

    assert api.post("/periods/1999-01/close", headers=auth("admin")).status_code == 404


def test_the_audit_log_names_who_did_what(api: TestClient, auth: Auth) -> None:
    entry_id = _propose(api, auth)
    events = api.get(
        "/audit-events",
        params={"object_type": "journal_entry", "object_id": entry_id},
        headers=auth("reader"),
    ).json()
    assert [(e["action"], e["actor"], e["actor_type"]) for e in events] == [
        ("propose_journal_entry", "agent", "AGENT")
    ]
    by_actor = api.get(
        "/audit-events", params={"actor": "agent"}, headers=auth("reader")
    ).json()
    assert [e["action"] for e in by_actor] == ["propose_journal_entry"]
