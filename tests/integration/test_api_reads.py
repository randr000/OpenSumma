"""The REST interface: authentication, and every read endpoint."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from opensumma import kernel
from opensumma.api import create_app
from opensumma.db import alembic_config, create_engine
from opensumma.kernel import LineInput
from opensumma.objects import create_accounting_object, create_journal_entry_for_object
from opensumma.workflow import deactivate_actor, get_actor, revoke_api_keys

Auth = Callable[[str], dict[str, str]]


def _post_directly(url: str) -> int:
    """Post a small entry for a vendor bill through trusted code; return its id."""
    engine = create_engine(url)
    with Session(engine) as session:
        bill = create_accounting_object(
            session,
            object_type="vendor_bill",
            occurred_at=datetime(2026, 3, 3, 9, tzinfo=UTC),
            source="email",
            counterparty="V-STRATUS",
            data={"invoice_number": "INV-88", "amount": "310.00"},
        )
        entry = create_journal_entry_for_object(
            session,
            bill,
            entry_date=date(2026, 3, 3),
            description="Stratus INV-88",
            lines=[
                LineInput("5200", Decimal("310.00"), dimensions={"DEPARTMENT": "ENG"}),
                LineInput("2110", Decimal("-310.00")),
            ],
        )
        kernel.post_journal_entry(session, entry)
        session.commit()
        entry_id = entry.id
    engine.dispose()
    return entry_id


# --- Health and authentication -----------------------------------------------------


def test_health_needs_no_key(api: TestClient) -> None:
    response = api.get("/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_health_reports_a_schema_that_is_not_current(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "e933f730d688")  # before API keys
    with TestClient(create_app(database_url)) as client:
        response = client.get("/health")
    assert (response.status_code, response.json()) == (
        503,
        {"status": "schema_out_of_date"},
    )


def test_a_request_without_a_key_is_refused(api: TestClient) -> None:
    response = api.get("/accounts")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == "AuthenticationError"


def test_an_unknown_key_is_refused(api: TestClient) -> None:
    response = api.get("/accounts", headers={"Authorization": "Bearer osk_forged"})
    assert (response.status_code, response.json()["error"]) == (
        401,
        "AuthenticationError",
    )


def test_a_revoked_key_or_an_inactive_actor_is_refused(
    api: TestClient, api_url: str, auth: Auth
) -> None:
    assert api.get("/accounts", headers=auth("reader")).status_code == 200
    assert api.get("/accounts", headers=auth("agent")).status_code == 200
    engine = create_engine(api_url)
    with Session(engine) as session:
        assert revoke_api_keys(session, get_actor(session, "reader")) == 1
        deactivate_actor(get_actor(session, "agent"))
        session.commit()
    engine.dispose()

    assert api.get("/accounts", headers=auth("reader")).status_code == 401
    inactive = api.get("/accounts", headers=auth("agent"))
    assert (inactive.status_code, inactive.json()["error"]) == (
        403,
        "PermissionDeniedError",
    )


def test_reading_needs_the_read_only_permission(api: TestClient, auth: Auth) -> None:
    response = api.get("/accounts", headers=auth("nobody"))
    assert response.status_code == 403
    assert response.json()["permission"] == "READ_ONLY"


# --- Master data ----------------------------------------------------------------------


def test_the_chart_of_accounts(api: TestClient, auth: Auth) -> None:
    accounts = api.get("/accounts", headers=auth("reader")).json()
    assert len(accounts) == 41
    assert accounts[0] == {
        "code": "1000",
        "name": "Assets",
        "account_type": "ASSET",
        "normal_balance": "DEBIT",
        "parent": None,
        "is_active": True,
        "is_postable": False,
    }
    one = api.get("/accounts/1590", headers=auth("reader")).json()
    assert (one["parent"], one["normal_balance"], one["is_postable"]) == (
        "1500",
        "CREDIT",
        True,
    )


def test_an_unknown_account_is_not_found(api: TestClient, auth: Auth) -> None:
    response = api.get("/accounts/9999", headers=auth("reader"))
    assert response.status_code == 404
    assert response.json() == {
        "error": "UnknownAccountError",
        "message": "no account with code '9999'",
    }


def test_an_account_balance(api: TestClient, api_url: str, auth: Auth) -> None:
    _post_directly(api_url)
    balance = api.get("/accounts/2000/balance", headers=auth("reader")).json()
    assert balance == {
        "account_code": "2000",
        "account_name": "Liabilities",
        "account_type": "LIABILITY",
        "normal_balance": "CREDIT",
        "as_of": None,
        "balance": "-310.00",
    }
    earlier = api.get(
        "/accounts/2000/balance", params={"as_of": "2026-03-02"}, headers=auth("reader")
    ).json()
    assert (earlier["as_of"], earlier["balance"]) == ("2026-03-02", "0.00")


def test_periods_dimensions_and_counterparties(api: TestClient, auth: Auth) -> None:
    periods = api.get("/periods", headers=auth("reader")).json()
    assert [p["code"] for p in periods][:2] == ["2026-01", "2026-02"]
    assert periods[0] == {
        "code": "2026-01",
        "start_date": "2026-01-01",
        "end_date": "2026-01-31",
        "status": "OPEN",
    }

    dimensions = api.get("/dimensions", headers=auth("reader")).json()
    assert [d["code"] for d in dimensions] == ["CLASS", "DEPARTMENT", "LOCATION"]
    assert {"code": "ENG", "name": "Engineering", "is_active": True} in dimensions[1][
        "values"
    ]

    vendors = api.get(
        "/counterparties", params={"kind": "VENDOR"}, headers=auth("reader")
    ).json()
    assert len(vendors) == 6
    assert vendors[4] == {
        "code": "V-STRATUS",
        "name": "Stratus Cloud Hosting",
        "kind": "VENDOR",
        "is_active": True,
    }
    everyone = api.get("/counterparties", headers=auth("reader")).json()
    assert len(everyone) == 11


# --- Journal entries and the ledger ------------------------------------------------


def test_a_journal_entry(api: TestClient, api_url: str, auth: Auth) -> None:
    entry_id = _post_directly(api_url)
    entry = api.get(f"/journal-entries/{entry_id}", headers=auth("reader")).json()

    assert entry["status"] == "POSTED"
    assert entry["posted_at"] is not None
    assert entry["total"] == "0.00"
    assert entry["lines"][0] == {
        "line_number": 1,
        "account": "5200",
        "amount": "310.00",
        "memo": None,
        "dimensions": {"DEPARTMENT": "ENG"},
    }
    assert entry["lines"][1]["amount"] == "-310.00"
    assert entry["accounting_object_ids"] == [1]

    missing = api.get("/journal-entries/999", headers=auth("reader"))
    assert (missing.status_code, missing.json()["error"]) == (
        404,
        "UnknownJournalEntryError",
    )


def test_the_ledger(api: TestClient, api_url: str, auth: Auth) -> None:
    _post_directly(api_url)
    lines = api.get("/ledger", headers=auth("reader")).json()
    assert [(line["account_code"], line["amount"]) for line in lines] == [
        ("5200", "310.00"),
        ("2110", "-310.00"),
    ]
    only = api.get(
        "/ledger", params={"account": ["2110", "1111"]}, headers=auth("reader")
    ).json()
    assert [line["account_code"] for line in only] == ["2110"]
    assert (
        api.get(
            "/ledger", params={"start": "2026-04-01"}, headers=auth("reader")
        ).json()
        == []
    )


# --- Reports -----------------------------------------------------------------------


def test_the_reports(api: TestClient, api_url: str, auth: Auth) -> None:
    _post_directly(api_url)

    def get(path: str, **params: str) -> Any:
        return api.get(path, params=params, headers=auth("reader")).json()

    trial = get("/reports/trial-balance", as_of="2026-03-31")
    assert [(line["account_code"], line["balance"]) for line in trial["lines"]] == [
        ("2110", "-310.00"),
        ("5200", "310.00"),
    ]
    assert (trial["total"], trial["is_balanced"]) == ("0.00", True)

    income = get("/reports/income-statement", start="2026-03-01", end="2026-03-31")
    assert income["net_income"] == "-310.00"
    expenses = income["expenses"]["lines"]
    assert [(line["account_code"], line["amount"]) for line in expenses] == [
        ("5000", "310.00"),
        ("5200", "310.00"),
    ]

    sheet = get("/reports/balance-sheet", as_of="2026-03-31")
    assert (sheet["liabilities"]["total"], sheet["unclosed_net_income"]) == (
        "310.00",
        "-310.00",
    )
    assert sheet["is_balanced"] is True

    ledger = get(
        "/reports/general-ledger", start="2026-03-01", end="2026-03-31", account="2110"
    )
    (payable,) = ledger["accounts"]
    assert (payable["opening_balance"], payable["closing_balance"]) == (
        "0.00",
        "-310.00",
    )
    assert [(line["amount"], line["balance"]) for line in payable["lines"]] == [
        ("-310.00", "-310.00")
    ]


def test_report_dates_are_required_and_checked(api: TestClient, auth: Auth) -> None:
    missing = api.get("/reports/trial-balance", headers=auth("reader"))
    assert (missing.status_code, missing.json()["error"]) == (
        422,
        "RequestValidationError",
    )
    backwards = api.get(
        "/reports/income-statement",
        params={"start": "2026-03-31", "end": "2026-03-01"},
        headers=auth("reader"),
    )
    assert (backwards.status_code, backwards.json()["error"]) == (422, "ValueError")


# --- Accounting objects and the audit log --------------------------------------------


def test_accounting_objects(api: TestClient, api_url: str, auth: Auth) -> None:
    entry_id = _post_directly(api_url)
    listed = api.get(
        "/accounting-objects",
        params={"object_type": "vendor_bill", "counterparty": "V-STRATUS"},
        headers=auth("reader"),
    ).json()
    assert [obj["id"] for obj in listed] == [1]

    bill = api.get("/accounting-objects/1", headers=auth("reader")).json()
    assert bill["data"] == {"invoice_number": "INV-88", "amount": "310.00"}
    assert (bill["journal_entry_ids"], bill["has_net_impact"]) == ([entry_id], True)
    assert bill["counterparty"] == "V-STRATUS"

    assert api.get("/accounting-objects/99", headers=auth("reader")).status_code == 404


def test_the_audit_log_is_read_page_by_page(api: TestClient, auth: Auth) -> None:
    first = api.get("/audit-events", params={"limit": 5}, headers=auth("reader")).json()
    assert [e["sequence"] for e in first] == [1, 2, 3, 4, 5]
    second = api.get(
        "/audit-events",
        params={"limit": 5, "after": first[-1]["sequence"]},
        headers=auth("reader"),
    ).json()
    assert [e["sequence"] for e in second] == [6, 7, 8, 9, 10]

    accounts = api.get(
        "/audit-events",
        params={"action": "create_account", "limit": 1000},
        headers=auth("reader"),
    ).json()
    assert len(accounts) == 41
    assert accounts[0]["actor"] is None and accounts[0]["actor_type"] == "SYSTEM"

    too_many = api.get("/audit-events", params={"limit": 5000}, headers=auth("reader"))
    assert too_many.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/accounts",
        "/accounts/{id}",
        "/accounts/{id}/balance",
        "/ledger",
        "/journal-entries/{id}",
        "/reports/trial-balance",
        "/reports/income-statement",
        "/reports/balance-sheet",
        "/accounting-objects",
        "/audit-events",
    ],
)
def test_every_read_endpoint_the_specification_names_exists(
    api: TestClient, path: str
) -> None:
    documented = {
        p.replace("{code}", "{id}").replace("{entry_id}", "{id}")
        for p in api.get("/openapi.json").json()["paths"]
    }
    assert path in documented
