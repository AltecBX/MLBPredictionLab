"""Domain error types.

Providers never raise past their own boundary (DATA_SOURCES.md §1); these
types are for internal invariant violations that must fail loudly.
"""

from __future__ import annotations


class JerryError(Exception):
    """Base class for all application errors."""


class ConfigurationError(JerryError):
    """Required configuration is missing or invalid."""


class ProviderError(JerryError):
    """An external provider failed in a way the caller must handle."""


class LeakageError(JerryError):
    """An as-of invariant was violated. Always a bug, never a data condition."""


class FeatureUnavailableError(JerryError):
    """A required feature could not be computed from available data."""


class ModelNotFoundError(JerryError):
    """No active model version is registered."""


class DataUnavailableError(JerryError):
    """Requested data does not exist and must not be fabricated."""
