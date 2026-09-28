"""Legacy compatibility entrypoint for the Realtime Tick worker."""

from service.entrypoints.tick_worker import main
from service.realtime.worker import TickService

__all__ = ["TickService", "main"]


if __name__ == '__main__':
    main()
