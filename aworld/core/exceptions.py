class AworldException(Exception):
    """Exception Aworld."""

    message: str

    def __init__(self, message: str):
        self.message = message