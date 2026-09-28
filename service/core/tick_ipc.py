"""Compatibility imports for the relocated Tick IPC protocol."""

from service.infrastructure.ipc.tick_protocol import (
    DEFAULT_SOCKET_PATH,
    PROTOCOL_VERSION,
    TickPublisher,
    _ClientChannel,
)

__all__ = [
    "DEFAULT_SOCKET_PATH",
    "PROTOCOL_VERSION",
    "TickPublisher",
    "_ClientChannel",
]
