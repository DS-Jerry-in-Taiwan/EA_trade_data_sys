"""Public query failures independent of the MT5 infrastructure adapter."""


class TradeQueryError(Exception):
    """An already-sanitized service failure with a stable public status/code."""

    def __init__(self, code, message, status):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)

    def as_response(self):
        return {"error": self.message, "code": self.code}
