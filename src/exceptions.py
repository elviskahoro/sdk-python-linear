from typing import Any


class LinearAPIError(Exception):
    """Exception raised for Linear API errors."""

    def __init__(
        self,
        message: str,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize LinearAPIError.

        Args:
            message: Error message.
            errors: List of error dictionaries from the API.
        """
        self.message = message
        self.errors = errors or []
        super().__init__(self.message)
