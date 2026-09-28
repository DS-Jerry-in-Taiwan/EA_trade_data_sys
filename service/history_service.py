"""Backward-compatible import and executable shim for the history worker."""

from service.history.worker import HistoryService, _handle_shutdown_signal, main

__all__ = ["HistoryService", "_handle_shutdown_signal", "main"]


if __name__ == "__main__":
    main()
