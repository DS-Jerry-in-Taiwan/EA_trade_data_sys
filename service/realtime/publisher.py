"""Realtime-facing publication boundary.

The wire protocol and socket lifecycle are infrastructure concerns.  Realtime
code imports them through this module so callers do not need to know where the
transport implementation lives.
"""

from service.infrastructure.ipc.tick_protocol import TickPublisher

__all__ = ["TickPublisher"]
