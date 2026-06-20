"""Compatibility alias for qfit-era imports."""

from __future__ import annotations

import importlib
import sys

_module = importlib.import_module("mapbox_config")
sys.modules[__name__] = _module
