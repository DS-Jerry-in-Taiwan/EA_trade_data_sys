"""Canonical trade-domain models and mapping errors."""

from service.domain.trades.errors import DealMappingError
from service.domain.trades.models import DealRecord, DealSummary

__all__ = ["DealMappingError", "DealRecord", "DealSummary"]

