"""Stable public errors for the execution API."""


class ExecutionError(Exception):
    def __init__(self, code, message, *, status=400, retcode=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retcode = retcode


def parse_ticket_id(value, *, resource):
    """Validate MT5's unsigned ticket IDs before any terminal access."""
    field = f"{resource}_id"
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        ticket = 0
    else:
        text = str(value)
        ticket = (
            int(text)
            if text.isascii() and text.isdecimal() and len(text) <= 20
            else 0
        )
    if not 0 < ticket <= (1 << 64) - 1:
        raise ExecutionError(
            f"invalid_{field}",
            f"{field} must be a positive unsigned 64-bit integer",
        )
    return ticket


class AmbiguousMT5Result(ExecutionError):
    def __init__(self, *, retcode=None):
        super().__init__(
            "execution_outcome_unknown",
            "MT5 acceptance could not be determined; use client_order_id lookup",
            status=503,
            retcode=retcode,
        )
