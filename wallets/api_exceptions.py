"""Maps the domain exceptions raised by services.py/idempotency.py onto
proper HTTP responses, so a business-rule violation is a clean 4xx with a
consistent {"error": ..., "detail": ...} body instead of an unhandled 500.

This is a minimal version of the exception handler described in plan
section 7 — enough to make section 6's endpoints behave correctly; it can
be extended later without changing the shape callers rely on.
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .exceptions import (
    DomainError,
    InsufficientFundsError,
    InvalidAmountError,
    SameWalletTransferError,
    WalletNotFoundError,
)
from .idempotency import IdempotencyConflictError

_STATUS_BY_EXCEPTION = {
    InsufficientFundsError: status.HTTP_400_BAD_REQUEST,
    InvalidAmountError: status.HTTP_400_BAD_REQUEST,
    SameWalletTransferError: status.HTTP_400_BAD_REQUEST,
    WalletNotFoundError: status.HTTP_404_NOT_FOUND,
    IdempotencyConflictError: status.HTTP_409_CONFLICT,
}


def custom_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    if isinstance(exc, DomainError):
        status_code = _STATUS_BY_EXCEPTION.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return Response({"error": exc.default_code, "detail": str(exc)}, status=status_code)

    return None
