"""Compatibility package mapped to the retained visualization modules."""

from __future__ import annotations

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[2] / "visualization")]
