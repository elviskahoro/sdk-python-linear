class LinearAPIError(Exception):
    """Exception raised for Linear API errors."""

    def __init__(self, message: str, errors: list | None = None):
        self.message = message
        self.errors = errors or []
        super().__init__(self.message)