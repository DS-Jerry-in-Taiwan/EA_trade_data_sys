"""Backward-compatible import for the canonical trade-domain error."""

from service.domain.trades.errors import DealMappingError

__all__ = ["DealMappingError"]
