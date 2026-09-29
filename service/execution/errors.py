"""Stable public errors for the execution API."""


class ExecutionError(Exception):
    def __init__(self, code, message, *, status=400, retcode=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retcode = retcode


class AmbiguousMT5Result(ExecutionError):
    def __init__(self):
        super().__init__(
            "execution_outcome_unknown",
            "MT5 acceptance could not be determined; use client_order_id lookup",
            status=503,
        )
