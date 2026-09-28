"""Backward-compatible imports for the history repository."""

from service.history.repository import (
    HistoryNotFoundError,
    HistoryNotReadyError,
    HistoryReadError,
    HistoryRepository,
    HistoryStorageError,
)

__all__ = [
    "HistoryStorageError",
    "HistoryNotFoundError",
    "HistoryNotReadyError",
    "HistoryReadError",
    "HistoryRepository",
]
