"""API keys: how an interface knows which actor is calling."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from opensumma.workflow import (
    Actor,
    ApiKey,
    AuthenticationError,
    audit_history,
    authenticate,
    issue_api_key,
    revoke_api_keys,
)


def test_a_key_identifies_its_actor(books: Session, agent: Actor) -> None:
    key = issue_api_key(books, agent)
    books.commit()

    assert key.startswith("osk_")
    assert len(key) > 40
    assert authenticate(books, key) is agent


def test_only_the_keys_hash_is_stored(books: Session, agent: Actor) -> None:
    key = issue_api_key(books, agent)
    books.commit()

    (stored,) = books.scalars(select(ApiKey)).all()
    assert stored.key_hash != key
    assert len(stored.key_hash) == 64
    assert key not in str(audit_history(books, action="create_api_key")[0].input)


def test_every_key_is_different(books: Session, agent: Actor) -> None:
    keys = {issue_api_key(books, agent) for _ in range(5)}
    books.commit()
    assert len(keys) == 5
    assert all(authenticate(books, key) is agent for key in keys)


def test_an_unknown_or_revoked_key_identifies_no_one(
    books: Session, agent: Actor, clerk: Actor
) -> None:
    kept = issue_api_key(books, clerk)
    first, second = issue_api_key(books, agent), issue_api_key(books, agent)
    books.commit()

    with pytest.raises(AuthenticationError):
        authenticate(books, "osk_forged")

    assert revoke_api_keys(books, agent) == 2
    books.commit()
    for key in (first, second):
        with pytest.raises(AuthenticationError):
            authenticate(books, key)
    assert authenticate(books, kept) is clerk
    assert revoke_api_keys(books, agent) == 0  # nothing left to revoke


def test_the_audit_log_is_paged_by_sequence(books: Session, agent: Actor) -> None:
    books.commit()
    everything = audit_history(books)
    page = audit_history(books, after=everything[9].sequence, limit=5)
    assert [e.sequence for e in page] == [e.sequence for e in everything[10:15]]
    accounts = audit_history(books, object_type="account", action="create_account")
    assert len(accounts) == 41
    one = audit_history(books, object_type="account", object_id=accounts[0].object_id)
    assert one == [accounts[0]]
