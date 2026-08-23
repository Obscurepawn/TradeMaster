"""One authoritative aggregation point for built-in managed factors."""

from __future__ import annotations

from .fundamental import fundamental_factor_suite
from .management import ManagedFactorRegistry
from .public_factors import public_executable_factor_suite


def builtin_managed_factor_registry() -> ManagedFactorRegistry:
    """Return every executable built-in definition and its explicit external fields."""

    fundamental = fundamental_factor_suite()
    public = public_executable_factor_suite()
    return ManagedFactorRegistry(
        (*fundamental.registrations, *public.registrations),
        external_dataset_fields=tuple(
            sorted(set(fundamental.external_dataset_fields) | set(public.external_dataset_fields))
        ),
    )


__all__ = ["builtin_managed_factor_registry"]
