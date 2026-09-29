"""Counterparty services: the vendors and customers accounting objects concern.

Counterparties are master data, like accounts and dimensions. Objects name them by
code, and ``resolve_counterparty`` is the gate every object passes through when it
names one: the counterparty exists, is active, and is the kind the object's type
calls for.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensumma.kernel import DuplicateCodeError
from opensumma.objects.enums import AccountingObjectType, CounterpartyKind
from opensumma.objects.errors import (
    CounterpartyKindError,
    InactiveCounterpartyError,
    UnknownCounterpartyError,
)
from opensumma.objects.models import Counterparty


@dataclass(frozen=True)
class CounterpartySpec:
    """One counterparty in a seed list."""

    code: str
    name: str
    kind: CounterpartyKind


# A fixed cast of fictional vendors and customers. It is a fixed list rather than a
# random one so that anything built on it is reproducible.
DEFAULT_COUNTERPARTIES: Sequence[CounterpartySpec] = (
    CounterpartySpec("V-STRATUS", "Stratus Cloud Hosting", CounterpartyKind.VENDOR),
    CounterpartySpec("V-HARBOR", "Harbor Point Properties", CounterpartyKind.VENDOR),
    CounterpartySpec("V-BRIGHT", "Brightline Software", CounterpartyKind.VENDOR),
    CounterpartySpec("V-KELLER", "Keller & Moss LLP", CounterpartyKind.VENDOR),
    CounterpartySpec("V-PAPER", "Paper Trail Office Supply", CounterpartyKind.VENDOR),
    CounterpartySpec("V-SUMMIT", "Summit Mutual Insurance", CounterpartyKind.VENDOR),
    CounterpartySpec("C-BLUEFIN", "Bluefin Logistics", CounterpartyKind.CUSTOMER),
    CounterpartySpec("C-CEDAR", "Cedar & Pine Hospitality", CounterpartyKind.CUSTOMER),
    CounterpartySpec("C-HELIO", "Helio Medical Group", CounterpartyKind.CUSTOMER),
    CounterpartySpec("C-ORCHARD", "Orchard Lane Schools", CounterpartyKind.CUSTOMER),
    CounterpartySpec("C-VERTEX", "Vertex Robotics", CounterpartyKind.CUSTOMER),
)


def create_counterparty(
    session: Session,
    *,
    code: str,
    name: str,
    kind: CounterpartyKind | str,
    is_active: bool = True,
) -> Counterparty:
    """Add a vendor or customer."""
    code = code.strip()
    if not code:
        raise ValueError("counterparty code must not be empty")
    if find_counterparty(session, code) is not None:
        raise DuplicateCodeError(f"counterparty code {code!r} already exists")
    counterparty = Counterparty(
        code=code, name=name, kind=CounterpartyKind(kind), is_active=is_active
    )
    session.add(counterparty)
    return counterparty


def find_counterparty(session: Session, code: str) -> Counterparty | None:
    """Return the counterparty with ``code``, or ``None``."""
    statement = select(Counterparty).where(Counterparty.code == code)
    return session.scalars(statement).one_or_none()


def get_counterparty(session: Session, code: str) -> Counterparty:
    """Return the counterparty with ``code``, or raise."""
    counterparty = find_counterparty(session, code)
    if counterparty is None:
        raise UnknownCounterpartyError(f"no counterparty with code {code!r}")
    return counterparty


def counterparties(
    session: Session, *, kind: CounterpartyKind | str | None = None
) -> list[Counterparty]:
    """Every counterparty, or every one of ``kind``, ordered by code."""
    statement = select(Counterparty).order_by(Counterparty.code)
    if kind is not None:
        statement = statement.where(Counterparty.kind == CounterpartyKind(kind))
    return list(session.scalars(statement))


def deactivate_counterparty(counterparty: Counterparty) -> None:
    """Retire a counterparty. Objects that already name it keep it."""
    counterparty.is_active = False


def activate_counterparty(counterparty: Counterparty) -> None:
    """Make a retired counterparty available to objects again."""
    counterparty.is_active = True


def resolve_counterparty(
    session: Session, code: str, *, object_type: AccountingObjectType
) -> Counterparty:
    """Return counterparty ``code`` if an object of ``object_type`` may name it.

    Raises if it does not exist, is inactive, or is the wrong kind for the type:
    a vendor bill names a vendor, a customer invoice a customer.
    """
    counterparty = get_counterparty(session, code)
    if not counterparty.is_active:
        raise InactiveCounterpartyError(f"counterparty {code} is inactive")
    expected = object_type.counterparty_kind
    if expected is not None and counterparty.kind is not expected:
        raise CounterpartyKindError(
            f"a {object_type.value} names a {expected.value.lower()}, but "
            f"{code} is a {counterparty.kind.value.lower()}"
        )
    return counterparty


def seed_counterparties(session: Session) -> dict[str, Counterparty]:
    """Create the default vendors and customers, keyed by code."""
    return {
        spec.code: create_counterparty(
            session, code=spec.code, name=spec.name, kind=spec.kind
        )
        for spec in DEFAULT_COUNTERPARTIES
    }
