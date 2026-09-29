"""Backward-compatible imports for the canonical trade-domain models.

New production code should import these contracts from ``service.domain.trades``.
The module remains as a compatibility shim until the legacy import migration is
completed in a follow-up ticket.
"""

from service.domain.trades.models import DealRecord, DealSummary

__all__ = ["DealRecord", "DealSummary"]
