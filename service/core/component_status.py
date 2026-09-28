"""Compatibility imports for relocated component status primitives."""

from service.infrastructure.status.component_status import (
    DEFAULT_STATUS_DIR,
    atomic_write_status,
    read_status,
    utc_now,
)

__all__ = [
    "DEFAULT_STATUS_DIR",
    "atomic_write_status",
    "read_status",
    "utc_now",
]
