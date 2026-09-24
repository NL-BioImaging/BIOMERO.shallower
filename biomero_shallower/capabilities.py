"""Stable runtime capabilities shared by Python and container consumers."""

from . import __version__
from .adapters import ADAPTERS


CAPABILITY_SCHEMA = 1
RUNTIME_CONTRACTS = tuple(sorted(ADAPTERS))
SHALLOW_MANIFEST_SCHEMAS = (2,)
MIGRATIONS = (
    "schema-1-to-2",
    "schema-1-path-only-labels",
)


def capabilities() -> dict:
    """Return the machine-readable compatibility contract for this build."""
    return {
        "capabilitySchema": CAPABILITY_SCHEMA,
        "version": __version__,
        "contracts": list(RUNTIME_CONTRACTS),
        "manifestSchemas": list(SHALLOW_MANIFEST_SCHEMAS),
        "migrations": list(MIGRATIONS),
    }


def require_migrations(*required: str) -> None:
    """Fail closed when this package cannot perform a requested migration."""
    missing = sorted(set(required).difference(MIGRATIONS))
    if missing:
        raise RuntimeError(
            "Installed biomero-shallower does not support migration "
            "capabilities: " + ", ".join(missing)
        )


__all__ = [
    "CAPABILITY_SCHEMA",
    "MIGRATIONS",
    "RUNTIME_CONTRACTS",
    "SHALLOW_MANIFEST_SCHEMAS",
    "capabilities",
    "require_migrations",
]
