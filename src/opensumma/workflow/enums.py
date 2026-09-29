"""The workflow engine's closed vocabularies."""

from enum import StrEnum


class ActorType(StrEnum):
    """Who is acting: a person, an AI agent, or the system itself."""

    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class Permission(StrEnum):
    """What an actor may do. Each is granted explicitly; none implies another.

    A normal AI accounting agent holds READ_ONLY and PROPOSER: it may read, analyse,
    and propose, but not approve or post.
    """

    READ_ONLY = "READ_ONLY"
    PROPOSER = "PROPOSER"
    APPROVER = "APPROVER"
    POSTER = "POSTER"
    ADMIN = "ADMIN"


class WorkflowAction(StrEnum):
    """The actions that move a journal entry, object, or period between states.

    They are lowercase, like the agent tools that will invoke them.
    """

    OBSERVE = "observe"
    EXTRACT = "extract"
    CLASSIFY = "classify"
    PROPOSE = "propose"
    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"
    POST = "post"
    REVERSE = "reverse"
    VOID = "void"
    CLOSE = "close"
    REOPEN = "reopen"


class AuditResult(StrEnum):
    """What came of an audited action.

    REFUSED means a rule stopped it and nothing changed but the audit log itself.
    """

    SUCCEEDED = "SUCCEEDED"
    REFUSED = "REFUSED"
