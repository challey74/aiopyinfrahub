"""Record subclasses registered per schema kind.

Infrahub kinds come from each instance's own schema rather than a fixed
set of apps, so this ships empty: downstream applications register Record
subclasses for the kinds they care about and KindEndpoint hands them back
in place of the base Record.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiopyinfrahub.response import Record

__all__ = [
    "KIND_MODELS",
    "register_model",
]

KIND_MODELS: dict[str, type[Record]] = {}


def register_model(kind: str, record_class: type[Record]) -> None:
    """Register a Record subclass for a kind, e.g.

    register_model("InfraDevice", InfraDevice).
    """
    KIND_MODELS[kind] = record_class
