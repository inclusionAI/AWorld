class AWorldRuntimeException(Exception):
    """Base exception class for AWorld runtime errors.

    This exception should be raised when runtime-specific errors occur
    within the AWorld framework.

    Attributes:
        message: Human-readable error message describing what went wrong.
    """

    def __init__(self, message: str):
        """Initialize the AWorld runtime exception.

        Args:
            message: Descriptive error message.
        """
        self.message = message
        super().__init__(self.message)

    def __str__(self) -> str:
        """Return string representation of the exception."""
        return f"AWorldRuntimeException: {self.message}"


class AWorldConfigurationError(AWorldRuntimeException):
    """Exception raised for configuration-related errors."""

    pass


class AWorldConnectionError(AWorldRuntimeException):
    """Exception raised for connection and network-related errors."""

    pass


class AWorldTransientModelError(AWorldConnectionError):
    """A retryable model transport failure after in-turn retries are exhausted.

    The error deliberately carries only stable, content-free provider metadata.
    It lets the Agent distinguish a transient transport interruption from a
    deterministic request/model failure without parsing human-readable error
    messages.
    """

    def __init__(
        self,
        message: str = "transient model provider failure",
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        source_error_type: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.error_code = error_code
        self.source_error_type = source_error_type
        super().__init__(message)


class AWorldToolExecutionError(AWorldRuntimeException):
    """Exception raised when tool execution fails."""

    pass
