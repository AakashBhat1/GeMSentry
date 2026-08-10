"""Procurement portal sources.

``gem/`` holds the native GeM client used by the main pipeline. Every other
portal is a :class:`~gemsentry.sources.base.BaseAdapter` dispatched by
:class:`~gemsentry.sources.registry.SourceRegistry`.
"""

from gemsentry.sources.attribution import annotate_sources, derive_source
from gemsentry.sources.base import BaseAdapter, UnsupportedAdapter
from gemsentry.sources.registry import ENGINES, NATIVE_ENGINES, SourceRegistry

__all__ = [
    "BaseAdapter", "UnsupportedAdapter", "SourceRegistry", "ENGINES", "NATIVE_ENGINES",
    "annotate_sources", "derive_source",
]
