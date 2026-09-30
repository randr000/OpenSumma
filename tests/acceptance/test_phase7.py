"""Phase 7 acceptance: the FastAPI REST interface.

The application is started for real, as a server process, and answers over HTTP.
Then an AI agent, a human controller, and a posting service keep March's books
through the API alone: the agent reads the chart, proposes an entry and validates
it, the controller approves it, the service posts it, and everyone reads the
reports that result. The entry is Dr 6100 120.50, Cr 2110 120.50; after it posts,
the trial balance at Mar 31 totals 120.50 on each side and net income is -120.50.
"""

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from opensumma.db import create_engine, init_db
from opensumma.kernel import seed_chart_of_accounts
from opensumma.workflow import ActorType, create_actor, issue_api_key

Auth = Callable[[str], dict[str, str]]

PROPOSAL = {
    "entry_date": "2026-03-15",
    "description": "AWS, March",
    "lines": [
        {"account": "6100", "debit": "120.50"},
        {"account": "2110", "credit": "120.50"},
    ],
    "reason": "Historical AWS transactions were classified to account 6100.",
    "evidence": ["vendor_id=42", "historical_account=6100"],
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _get(url: str, key: str | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(url)
    if key is not None:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_the_fastapi_application_starts(database_url: str) -> None:
    url = database_url
    init_db(url)
    engine = create_engine(url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        reader = create_actor(
            session,
            code="reader",
            name="Reader",
            actor_type=ActorType.AGENT,
            permissions=["READ_ONLY"],
        )
        key = issue_api_key(session, reader)
        session.commit()
    engine.dispose()

    port = _free_port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "opensumma.api",
            "--port",
            str(port),
            "--database-url",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                status, body = _get(f"{base}/health")
                break
            except (urllib.error.URLError, ConnectionError):
                assert server.poll() is None, (
                    server.stderr.read() if server.stderr else ""
                )
                assert time.monotonic() < deadline, "the server did not start"
                time.sleep(0.2)
        assert (status, body) == (200, {"status": "ok"})

        status, accounts = _get(f"{base}/accounts", key)
        assert status == 200 and len(accounts) == 41
        assert _get(f"{base}/accounts")[0] == 401
    finally:
        server.terminate()
        server.wait(timeout=10)
        if server.stderr:
            server.stderr.close()


def test_read_endpoints_work(api: TestClient, auth: Auth) -> None:
    checks: list[tuple[str, Callable[[Any], bool]]] = [
        ("/accounts", lambda body: len(body) == 41),
        ("/accounts/6100", lambda body: body["name"] == "Software Subscriptions"),
        ("/accounts/6100/balance", lambda body: body["balance"] == "0.00"),
        ("/ledger", lambda body: body == []),
        ("/periods", lambda body: len(body) == 12),
        ("/dimensions", lambda body: len(body) == 3),
        ("/counterparties", lambda body: len(body) == 11),
        ("/accounting-objects", lambda body: body == []),
        ("/audit-events", lambda body: len(body) == 100),
    ]
    for path, check in checks:
        response = api.get(path, headers=auth("reader"))
        assert response.status_code == 200, path
        assert check(response.json()), path


def test_journal_proposal_endpoint_works(api: TestClient, auth: Auth) -> None:
    response = api.post("/journal-entries", json=PROPOSAL, headers=auth("agent"))
    assert response.status_code == 201
    entry = response.json()
    assert entry["status"] == "PROPOSED"
    assert [
        (line["account"], line["debit"], line["credit"]) for line in entry["lines"]
    ] == [
        ("6100", "120.50", "0.00"),
        ("2110", "0.00", "120.50"),
    ]

    fetched = api.get(f"/journal-entries/{entry['id']}", headers=auth("reader"))
    assert fetched.json() == entry

    (audited,) = api.get(
        "/audit-events",
        params={"object_type": "journal_entry", "object_id": entry["id"]},
        headers=auth("reader"),
    ).json()
    assert (audited["actor"], audited["actor_type"]) == ("agent", "AGENT")
    assert audited["reason"] == PROPOSAL["reason"]
    assert audited["evidence"] == PROPOSAL["evidence"]


def test_validation_endpoint_works(api: TestClient, auth: Auth) -> None:
    good = api.post("/journal-entries", json=PROPOSAL, headers=auth("agent")).json()
    bad = api.post(
        "/journal-entries",
        json={
            **PROPOSAL,
            "lines": [
                {"account": "6100", "debit": "120.50"},
                {"account": "2110", "credit": "12.05"},
            ],
        },
        headers=auth("agent"),
    ).json()

    def validate(entry_id: int) -> Any:
        return api.post(
            f"/journal-entries/{entry_id}/validate", headers=auth("agent")
        ).json()

    assert validate(good["id"]) == {
        "journal_entry_id": good["id"],
        "is_valid": True,
        "issues": [],
    }
    result = validate(bad["id"])
    assert result["is_valid"] is False
    assert [issue["code"] for issue in result["issues"]] == ["UNBALANCED"]


def test_approval_endpoint_works(api: TestClient, auth: Auth) -> None:
    entry_id = api.post(
        "/journal-entries", json=PROPOSAL, headers=auth("agent")
    ).json()["id"]
    api.post(f"/journal-entries/{entry_id}/submit", headers=auth("agent"))

    by_agent = api.post(f"/journal-entries/{entry_id}/approve", headers=auth("agent"))
    assert (by_agent.status_code, by_agent.json()["permission"]) == (403, "APPROVER")

    approved = api.post(
        f"/journal-entries/{entry_id}/approve",
        json={"reason": "Matches the invoice"},
        headers=auth("controller"),
    )
    assert (approved.status_code, approved.json()["status"]) == (200, "APPROVED")


def test_posting_endpoint_works(api: TestClient, auth: Auth) -> None:
    entry_id = api.post(
        "/journal-entries", json=PROPOSAL, headers=auth("agent")
    ).json()["id"]
    api.post(f"/journal-entries/{entry_id}/submit", headers=auth("agent"))
    unapproved = api.post(f"/journal-entries/{entry_id}/post", headers=auth("poster"))
    assert (unapproved.status_code, unapproved.json()["error"]) == (
        409,
        "InvalidTransitionError",
    )

    api.post(f"/journal-entries/{entry_id}/approve", headers=auth("controller"))
    posted = api.post(f"/journal-entries/{entry_id}/post", headers=auth("poster"))
    assert (posted.status_code, posted.json()["status"]) == (200, "POSTED")
    assert posted.json()["posted_at"] is not None
    ledger = api.get("/ledger", headers=auth("reader")).json()
    assert {line["entry_id"] for line in ledger} == {entry_id}


def test_reports_are_accessible(api: TestClient, auth: Auth) -> None:
    entry_id = api.post(
        "/journal-entries", json=PROPOSAL, headers=auth("agent")
    ).json()["id"]
    for step, actor in [
        ("submit", "agent"),
        ("approve", "controller"),
        ("post", "poster"),
    ]:
        api.post(f"/journal-entries/{entry_id}/{step}", headers=auth(actor))

    def report(path: str, **params: str) -> Any:
        response = api.get(f"/reports/{path}", params=params, headers=auth("reader"))
        assert response.status_code == 200
        return response.json()

    trial = report("trial-balance", as_of="2026-03-31")
    assert (trial["total_debits"], trial["total_credits"]) == ("120.50", "120.50")
    income = report("income-statement", start="2026-03-01", end="2026-03-31")
    assert income["net_income"] == "-120.50"
    sheet = report("balance-sheet", as_of="2026-03-31")
    assert (sheet["total_liabilities_and_equity"], sheet["is_balanced"]) == (
        "0.00",
        True,
    )
    general = report(
        "general-ledger", start="2026-03-01", end="2026-03-31", account="6100"
    )
    assert general["accounts"][0]["closing_balance"] == "120.50"
