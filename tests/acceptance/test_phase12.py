"""Phase 12 acceptance: PostgreSQL compatibility.

Run against a PostgreSQL server, named by OPENSUMMA_TEST_POSTGRESQL_URL; without
one these tests are skipped, and the rest of the suite runs on SQLite. With one,
every test that takes a database from the suite's fixtures runs on PostgreSQL too,
which is most of them; these add what only makes sense there: a plain
``postgresql://`` URL, the same books on both backends, the installed servers on
PostgreSQL, and requests that run at the same time.
"""

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from anyio.from_thread import start_blocking_portal
from mcp import Client, StdioServerParameters
from sqlalchemy import make_url, text
from sqlalchemy.orm import Session

from opensumma.datasets import books_fingerprint, generate_dataset, write_dataset
from opensumma.db import alembic_config, create_engine, init_db
from opensumma.kernel import (
    ImmutableEntryError,
    JournalEntryError,
    LineInput,
    balance_sheet,
    close_period,
    create_calendar_year_periods,
    create_journal_entry,
    get_period,
    post_journal_entry,
    seed_chart_of_accounts,
    trial_balance,
)
from opensumma.objects import create_accounting_object
from opensumma.workflow import (
    ActorType,
    Permission,
    audit_history,
    create_actor,
    issue_api_key,
    verify_audit_log,
)

pytestmark = pytest.mark.postgresql


def _plain(database_url: str) -> str:
    """``database_url`` as people write it, naming no driver."""
    url = make_url(database_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def test_postgresql_connection_works(database_url: str) -> None:
    engine = create_engine(_plain(database_url))
    with engine.connect() as connection:
        version: str = connection.execute(text("SELECT version()")).scalar_one()
    assert engine.dialect.name == "postgresql" and engine.dialect.driver == "psycopg"
    assert version.startswith("PostgreSQL")
    engine.dispose()


def test_migrations_work_on_postgresql(database_url: str) -> None:
    url = _plain(database_url)
    config = alembic_config(url)
    head = ScriptDirectory.from_config(config).get_current_head()
    init_db(url)
    command.check(config)  # the migrated schema is exactly the models' schema
    command.downgrade(config, "base")
    init_db(url)
    engine = create_engine(url)
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == head
    engine.dispose()


def test_the_same_seed_gives_the_same_books_on_both_backends(
    database_url: str, tmp_path: Path
) -> None:
    on_sqlite = write_dataset(
        tmp_path / "acme", company="acme", transactions=300, seed=12
    )
    init_db(database_url)
    engine = create_engine(database_url)
    with Session(engine) as session:
        on_postgresql = generate_dataset(
            session, company="acme", transactions=300, seed=12
        )
        session.commit()
        # Every account, entry, line, amount, date, document, and business datum,
        # and every id, which the ground truth names records by.
        assert books_fingerprint(session) == on_sqlite.manifest["fingerprint"]
        assert on_postgresql.ground_truth == on_sqlite.ground_truth
        year_end = trial_balance(session, as_of=date(2026, 12, 31))
    engine.dispose()

    sqlite = create_engine(f"sqlite:///{tmp_path / 'acme' / 'books.db'}")
    with Session(sqlite) as session:
        assert trial_balance(session, as_of=date(2026, 12, 31)) == year_end
    sqlite.dispose()


def test_accounting_invariants_hold_on_postgresql(database_url: str) -> None:
    init_db(database_url)
    engine = create_engine(database_url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        create_calendar_year_periods(session, 2026)
        cents = [Decimal("0.10"), Decimal("0.20")]
        entries = [
            create_journal_entry(
                session,
                entry_date=date(2026, 3, 2),
                description="Stationery",
                lines=[LineInput("6700", a), LineInput("1111", -a)],
            )
            for a in cents
        ]
        for entry in entries:
            post_journal_entry(session, entry)
        session.commit()

        # Money is exact: 0.10 + 0.20 is 0.30, in the ledger and in SQL alike.
        summed: int = session.execute(
            text("SELECT sum(amount) FROM journal_line WHERE amount > 0")
        ).scalar_one()
        assert summed == 30  # integer cents
        march = trial_balance(session, as_of=date(2026, 3, 31))
        assert [(line.account_code, line.balance) for line in march.lines] == [
            ("1111", Decimal("-0.30")),
            ("6700", Decimal("0.30")),
        ]
        assert str(march.total) == "0.00"
        assert balance_sheet(session, as_of=date(2026, 3, 31)).is_balanced

        # Posted entries are immutable; unbalanced ones and closed periods refuse.
        entries[0].description = "Rewritten"
        with pytest.raises(ImmutableEntryError):
            session.flush()
        session.rollback()
        unbalanced = create_journal_entry(
            session,
            entry_date=date(2026, 3, 3),
            description="Unbalanced",
            lines=[
                LineInput("6700", Decimal("1.00")),
                LineInput("1111", Decimal("-0.99")),
            ],
        )
        with pytest.raises(JournalEntryError):
            post_journal_entry(session, unbalanced)
        close_period(get_period(session, "2026-03"))
        balanced = create_journal_entry(
            session,
            entry_date=date(2026, 3, 3),
            description="Too late",
            lines=[
                LineInput("6700", Decimal("1.00")),
                LineInput("1111", Decimal("-1.00")),
            ],
        )
        with pytest.raises(JournalEntryError) as refused:
            post_journal_entry(session, balanced)
        assert [issue.code.value for issue in refused.value.issues] == ["PERIOD_CLOSED"]
        session.rollback()

        # Timestamps come back in UTC; business data refuses floats.
        assert entries[0].posted_at is not None
        assert entries[0].posted_at.tzinfo is UTC
        with pytest.raises(TypeError, match="write amounts as strings"):
            create_accounting_object(
                session,
                object_type="expense",
                occurred_at=datetime(2026, 3, 2, tzinfo=UTC),
                source="card",
                data={"amount": 0.1},
            )
    engine.dispose()


# --- The installed servers, on PostgreSQL -------------------------------------------


@dataclass(frozen=True)
class Server:
    base: str
    keys: dict[str, str]
    database_url: str


CAST = {
    "reader": (ActorType.AGENT, [Permission.READ_ONLY]),
    "clerk": (ActorType.HUMAN, [Permission.READ_ONLY, Permission.PROPOSER]),
    "maria": (ActorType.HUMAN, [Permission.READ_ONLY, Permission.APPROVER]),
    "omar": (ActorType.HUMAN, [Permission.READ_ONLY, Permission.APPROVER]),
}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _request(url: str, key: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


@pytest.fixture
def server(database_url: str) -> Iterator[Server]:
    """``python -m opensumma.api`` serving migrated books on PostgreSQL, given a
    URL that names no driver."""
    url = _plain(database_url)
    init_db(url)
    engine = create_engine(url)
    with Session(engine) as session:
        seed_chart_of_accounts(session)
        create_calendar_year_periods(session, 2026)
        keys = {
            code: issue_api_key(
                session,
                create_actor(
                    session, code=code, name=code, actor_type=kind, permissions=granted
                ),
            )
            for code, (kind, granted) in CAST.items()
        }
        session.commit()
    engine.dispose()

    port = _free_port()
    process = subprocess.Popen(
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
                urllib.request.urlopen(f"{base}/health", timeout=2).close()
                break
            except (urllib.error.URLError, ConnectionError):
                assert process.poll() is None, (
                    process.stderr.read() if process.stderr else ""
                )
                assert time.monotonic() < deadline, "the server did not start"
                time.sleep(0.2)
        yield Server(base, keys, url)
    finally:
        process.terminate()
        process.wait(timeout=10)
        if process.stderr:
            process.stderr.close()


def _propose(server: Server) -> int:
    status, entry = _request(
        f"{server.base}/journal-entries",
        server.keys["clerk"],
        {
            "entry_date": "2026-03-10",
            "description": "Office supplies",
            "lines": [
                {"account": "6700", "amount": "45.00"},
                {"account": "2110", "amount": "-45.00"},
            ],
        },
    )
    assert status == 201, entry
    entry_id: int = entry["id"]
    return entry_id


def _all_at_once(*calls: tuple[Any, ...]) -> list[Any]:
    """Call each ``(function, *arguments)`` in a thread of its own, all at once."""
    start = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)

    def run(index: int, function: Any, *arguments: Any) -> None:
        start.wait()
        results[index] = function(*arguments)

    threads = [
        threading.Thread(target=run, args=(index, *call))
        for index, call in enumerate(calls)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


def test_the_rest_interface_serves_postgresql(server: Server) -> None:
    assert _request(f"{server.base}/health", server.keys["reader"]) == (
        200,
        {"status": "ok"},
    )
    status, accounts = _request(f"{server.base}/accounts", server.keys["reader"])
    assert status == 200 and len(accounts) == 41


def test_requests_at_the_same_time_keep_the_books_consistent(server: Server) -> None:
    # Many proposals at once: every one recorded, the audit log one chain.
    proposed = _all_at_once(*[(_propose, server) for _ in range(16)])
    assert len(set(proposed)) == 16

    # Two approvers approving the same entry at once: exactly one succeeds.
    for entry_id in proposed[:6]:
        base = f"{server.base}/journal-entries/{entry_id}"
        assert _request(f"{base}/submit", server.keys["clerk"], {})[0] == 200
        outcomes = _all_at_once(
            (_request, f"{base}/approve", server.keys["maria"], {}),
            (_request, f"{base}/approve", server.keys["omar"], {}),
        )
        assert sorted(status for status, _ in outcomes) == [200, 409], outcomes

    engine = create_engine(server.database_url)
    with Session(engine) as session:
        events = audit_history(session)
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        assert verify_audit_log(session).is_intact
        approvals = audit_history(
            session, action="approve_journal_entry", result="SUCCEEDED"
        )
        assert len(approvals) == 6
    engine.dispose()


def test_the_mcp_server_serves_postgresql(server: Server) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "opensumma.mcp", "--database-url", server.database_url],
        env={"OPENSUMMA_API_KEY": server.keys["clerk"]},
    )
    with (
        start_blocking_portal() as portal,
        portal.wrap_async_context_manager(Client(parameters)) as client,
    ):
        chart = portal.call(client.call_tool, "get_chart_of_accounts", {})
        assert chart.structured_content is not None
        assert len(chart.structured_content["accounts"]) == 41
        proposed = portal.call(
            client.call_tool,
            "propose_journal_entry",
            {
                "entry_date": "2026-03-11",
                "description": "Paper Trail INV-6",
                "lines": [
                    {"account": "6700", "amount": "30.00"},
                    {"account": "2110", "amount": "-30.00"},
                ],
                "reason": "Same vendor and account as INV-5",
                "evidence": ["invoice=INV-6"],
            },
        )
        assert not proposed.is_error, proposed.content
        assert proposed.structured_content is not None
        assert proposed.structured_content["total"] == "0.00"
        assert [line["amount"] for line in proposed.structured_content["lines"]] == [
            "30.00",
            "-30.00",
        ]
