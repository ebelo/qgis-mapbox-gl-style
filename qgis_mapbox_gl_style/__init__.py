"""Development package for the QGIS Mapbox GL Style project."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

for _name in ("mapbox_config", "validation", "visualization"):
    _module = importlib.import_module(_name)
    globals()[_name] = _module
    sys.modules[f"{__name__}.{_name}"] = _module

__all__ = ["mapbox_config", "validation", "visualization"]
