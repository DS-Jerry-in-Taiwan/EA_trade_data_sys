"""Backward-compatible imports for read-only account and trade queries."""

from service.trade_query.account_service import AccountService, MT5_NOT_CONNECTED, main

__all__ = ["AccountService", "MT5_NOT_CONNECTED", "main"]


if __name__ == "__main__":
    main()
