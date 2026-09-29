"""How refusals become HTTP responses.

Every refusal is answered with the same JSON the audit log records for it: the
error's name, its message, and whatever it names (issue codes, the missing
permission, the states an action is allowed from). An agent reads the same
vocabulary in a response as an auditor reads in the log.
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from opensumma.kernel import (
    EntryStatusError,
    ImmutableEntryError,
    JournalEntryError,
    KernelError,
    UnknownAccountError,
    UnknownDimensionError,
    UnknownJournalEntryError,
    UnknownPeriodError,
)
from opensumma.objects import (
    AccountingObjectError,
    AlreadyLinkedError,
    ImmutableRecordError,
    ObjectHasAccountingImpactError,
    UnknownAccountingObjectError,
    UnknownCounterpartyError,
    VoidedObjectError,
)
from opensumma.workflow import (
    AuthenticationError,
    CounterpartyRequiredError,
    ImmutableHistoryError,
    InvalidTransitionError,
    PendingEntriesError,
    PeriodSequenceError,
    PermissionDeniedError,
    SegregationOfDutiesError,
    UnknownActorError,
    WorkflowError,
    describe_refusal,
)

# The first matching entry decides the status, so specific errors come first.
STATUS_CODES: tuple[tuple[type[Exception], int], ...] = (
    (AuthenticationError, 401),
    (PermissionDeniedError, 403),
    (SegregationOfDutiesError, 403),
    (UnknownAccountError, 404),
    (UnknownActorError, 404),
    (UnknownAccountingObjectError, 404),
    (UnknownCounterpartyError, 404),
    (UnknownDimensionError, 404),
    (UnknownJournalEntryError, 404),
    (UnknownPeriodError, 404),
    (InvalidTransitionError, 409),
    (CounterpartyRequiredError, 409),
    (PeriodSequenceError, 409),
    (PendingEntriesError, 409),
    (ObjectHasAccountingImpactError, 409),
    (VoidedObjectError, 409),
    (AlreadyLinkedError, 409),
    (EntryStatusError, 409),
    (ImmutableEntryError, 409),
    (ImmutableRecordError, 409),
    (ImmutableHistoryError, 409),
    (JournalEntryError, 422),
    (KernelError, 422),
    (AccountingObjectError, 422),
    (WorkflowError, 409),
    (ValueError, 422),
    (TypeError, 422),
)


def status_code(error: Exception) -> int:
    return next(code for kind, code in STATUS_CODES if isinstance(error, kind))


def install_error_handlers(app: FastAPI) -> None:
    async def refused(request: Request, error: Exception) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if status_code(error) == 401 else None
        return JSONResponse(
            jsonable_encoder(describe_refusal(error)),
            status_code=status_code(error),
            headers=headers,
        )

    async def malformed(request: Request, error: Exception) -> JSONResponse:
        details = error.errors() if isinstance(error, RequestValidationError) else []
        return JSONResponse(
            {
                "error": "RequestValidationError",
                "message": "the request does not match the schema",
                "details": jsonable_encoder(details),
            },
            status_code=422,
        )

    for kind in {kind for kind, _ in STATUS_CODES}:
        app.add_exception_handler(kind, refused)
    app.add_exception_handler(RequestValidationError, malformed)
