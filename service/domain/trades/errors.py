"""Domain errors raised while constructing canonical trade models."""


class DealMappingError(ValueError):
    """Raised when source deal data cannot form a valid canonical record."""


__all__ = ["DealMappingError"]

