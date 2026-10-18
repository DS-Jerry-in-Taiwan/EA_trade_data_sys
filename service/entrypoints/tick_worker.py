"""Process entrypoint for Realtime Tick acquisition."""

import signal

from service.realtime.worker import TickService


def main():
    service = TickService()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: service.stop())
    signal.signal(signal.SIGINT, lambda _signum, _frame: service.stop())
    try:
        service.run()
    finally:
        service.stop()


if __name__ == '__main__':
    main()
