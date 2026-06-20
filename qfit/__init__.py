"""Compatibility namespace for qfit-originated validation history.

The public plugin has moved to QGIS Mapbox GL Style, but the retained
validation scripts and reports still use qfit-era field names. This package
keeps those historical imports reproducible without changing their audit
vocabulary.
"""

from __future__ import annotations

__all__ = ["mapbox_config", "validation", "visualization"]
