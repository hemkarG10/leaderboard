"""Domain / application errors with stable machine-readable codes."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base error rendered as the standard error envelope."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details if details is not None else {}


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class GameNotFound(NotFound):
    code = "game_not_found"


class UserNotFound(NotFound):
    code = "user_not_found"


class CapacityExceeded(AppError):
    status_code = 409
    code = "capacity_exceeded"


class ValidationFailed(AppError):
    status_code = 400
    code = "validation_failed"
