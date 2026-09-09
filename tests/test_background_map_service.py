"""Tests for BackgroundMapService.

Two test suites are provided:

1. ``*Tests`` classes — require a real QGIS installation; skipped elsewhere.
2. ``*MockTests`` classes — run against a MagicMock-backed module; skipped
   when QGIS is present (the suite above already provides coverage).
   These classes exist primarily to give SonarCloud line coverage in CI
   environments that lack QGIS.
"""
import importlib
import importlib.util
import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests import _path  # noqa: F401

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# ---------------------------------------------------------------------------
# Detect whether a real QGIS installation is present
# ---------------------------------------------------------------------------
try:
    _REAL_QGIS_PRESENT = importlib.util.find_spec("qgis") is not None
except ValueError:
    _REAL_QGIS_PRESENT = any(
        os.path.isdir(os.path.join(p, "qgis")) for p in sys.path if p
    )

# ---------------------------------------------------------------------------
# Real-QGIS import (used for skip-guarded tests)
# ---------------------------------------------------------------------------
try:
    from qfit.visualization.infrastructure.background_map_service import BackgroundMapService
    from qfit.mapbox_config import BACKGROUND_LAYER_PREFIX, TILE_MODE_RASTER, TILE_MODE_VECTOR

    QGIS_AVAILABLE = True
    QGIS_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover
    BackgroundMapService = None
    BACKGROUND_LAYER_PREFIX = "qfit background"
    TILE_MODE_RASTER = "Raster"
    TILE_MODE_VECTOR = "Vector"
    QGIS_AVAILABLE = False
    QGIS_IMPORT_ERROR = exc

SKIP_REAL = f"QGIS not available: {QGIS_IMPORT_ERROR}" if not QGIS_AVAILABLE else ""

# ---------------------------------------------------------------------------
# Mock-QGIS loader — mirrors the approach in test_layer_style_service.py
# ---------------------------------------------------------------------------

_mock_bms_cls = None
_mock_bms_mod = None
_qstub = None


def _load_service_with_mock_qgis():
    """Import BackgroundMapService with MagicMock QGIS stubs.

    Returns ``(BackgroundMapService_class, module, qstub)`` or
    ``(None, None, None)`` on failure.
    """
    qstub = MagicMock()

    # QgsRasterLayer needs to be a real type so isinstance() works.
    # We subclass MagicMock but override __init__ to ignore the QGIS constructor
    # positional args (uri, name, provider) that would otherwise be interpreted
    # as MagicMock's `spec=` parameter, constraining attribute access.
    class _QgsRasterLayer(MagicMock):
        def __init__(self, *args, **kwargs):
            super().__init__()  # no spec — full MagicMock attribute access

    qstub.QgsRasterLayer = _QgsRasterLayer

    _QGIS_MODS = ["qgis", "qgis.core", "qgis.PyQt", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui"]

    saved_qgis = {m: sys.modules.get(m) for m in _QGIS_MODS}
    saved_bms = sys.modules.get("qfit.visualization.infrastructure.background_map_service")

    for mod_name in _QGIS_MODS:
        sys.modules[mod_name] = qstub
    sys.modules.pop("qfit.visualization.infrastructure.background_map_service", None)

    try:
        bms_mod = importlib.import_module("qfit.visualization.infrastructure.background_map_service")
        return bms_mod.BackgroundMapService, bms_mod, qstub
    except Exception:  # pragma: no cover
        return None, None, None
    finally:
        for mod_name, original in saved_qgis.items():
            if original is None:
                sys.modules.pop(mod_name, None)
            else:
                sys.modules[mod_name] = original
        if saved_bms is None:
            sys.modules.pop("qfit.visualization.infrastructure.background_map_service", None)
        else:
            sys.modules["qfit.visualization.infrastructure.background_map_service"] = saved_bms


# Use QGIS_AVAILABLE (successful import) rather than _REAL_QGIS_PRESENT
# (package discoverable) so that an incomplete install (package found but
# native libs broken) still triggers the mock suite.
if not QGIS_AVAILABLE:
    _mock_bms_cls, _mock_bms_mod, _qstub = _load_service_with_mock_qgis()

SKIP_MOCK = "QGIS is installed — real-QGIS suite provides coverage" if QGIS_AVAILABLE else ""
SKIP_MOCK_LOAD = (
    "Could not load BackgroundMapService with mock QGIS"
    if (_mock_bms_cls is None and not _REAL_QGIS_PRESENT)
    else ""
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project_mock(layers=None):
    mock_project = MagicMock()
    mock_project.mapLayers.return_value = layers or {}
    mock_root = MagicMock()
    mock_root.children.return_value = []
    mock_project.layerTreeRoot.return_value = mock_root
    return mock_project


def _make_layer_node(layer_name, layer_cls=None):
    layer = MagicMock() if layer_cls is None else layer_cls()
    layer.name.return_value = layer_name
    node = MagicMock()
    node.layer.return_value = layer
    return node, layer


def _make_line_label_settings(
    *,
    line_anchor_percent=0.0,
    anchor_text_point=None,
    anchor_type=None,
    anchor_clipping=None,
):
    settings = MagicMock()
    settings.lineAnchorPercent.return_value = line_anchor_percent
    settings.anchorTextPoint.return_value = anchor_text_point
    settings.anchorType.return_value = anchor_type
    settings.anchorClipping.return_value = anchor_clipping
    return settings


# ===========================================================================
# Suite 1 — real-QGIS (skipped when QGIS unavailable)
# ===========================================================================

@unittest.skipUnless(QGIS_AVAILABLE, SKIP_REAL)
class EnsureBackgroundLayerDisabledTests(unittest.TestCase):
    def test_returns_none_when_disabled(self):
        service = BackgroundMapService()
        mock_project = _make_project_mock()
        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp:
            qp.instance.return_value = mock_project
            result = service.ensure_background_layer(
                enabled=False, preset_name="Outdoor", access_token="tok"
            )
        self.assertIsNone(result)

    def test_removes_existing_background_layers_when_disabled(self):
        service = BackgroundMapService()

        bg_layer = MagicMock()
        bg_layer.name.return_value = f"{BACKGROUND_LAYER_PREFIX} — Outdoor"
        bg_layer.id.return_value = "bg-id"
        other_layer = MagicMock()
        other_layer.name.return_value = "qfit activities"

        mock_project = _make_project_mock({"bg-id": bg_layer, "act-id": other_layer})
        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp:
            qp.instance.return_value = mock_project
            service.ensure_background_layer(
                enabled=False, preset_name="Outdoor", access_token="tok"
            )
        mock_project.removeMapLayer.assert_called_once_with("bg-id")


@unittest.skipUnless(QGIS_AVAILABLE, SKIP_REAL)
class MoveBackgroundLayersToBottomTests(unittest.TestCase):
    def test_background_node_moved_to_end(self):
        service = BackgroundMapService()
        bg_node, _ = _make_layer_node(f"{BACKGROUND_LAYER_PREFIX} — Outdoor")
        other_node, _ = _make_layer_node("qfit activities")

        mock_root = MagicMock()
        mock_root.children.return_value = [bg_node, other_node]
        mock_project = _make_project_mock()
        mock_project.layerTreeRoot.return_value = mock_root

        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp:
            qp.instance.return_value = mock_project
            service.move_background_layers_to_bottom()

        mock_root.reorderChildren.assert_called_once_with([other_node, bg_node])

    def test_no_reorder_when_already_at_bottom(self):
        service = BackgroundMapService()
        other_node, _ = _make_layer_node("qfit activities")
        bg_node, _ = _make_layer_node(f"{BACKGROUND_LAYER_PREFIX} — Outdoor")

        mock_root = MagicMock()
        mock_root.children.return_value = [other_node, bg_node]
        mock_project = _make_project_mock()
        mock_project.layerTreeRoot.return_value = mock_root

        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp:
            qp.instance.return_value = mock_project
            service.move_background_layers_to_bottom()

        mock_root.reorderChildren.assert_not_called()

    def test_empty_tree_does_not_raise(self):
        service = BackgroundMapService()
        mock_root = MagicMock()
        mock_root.children.return_value = []
        mock_project = _make_project_mock()
        mock_project.layerTreeRoot.return_value = mock_root

        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp:
            qp.instance.return_value = mock_project
            service.move_background_layers_to_bottom()

        mock_root.reorderChildren.assert_not_called()


@unittest.skipUnless(QGIS_AVAILABLE, SKIP_REAL)
class SnapExtentToBackgroundTileZoomTests(unittest.TestCase):
    def _make_extent(self, xmin, ymin, xmax, ymax):
        e = MagicMock()
        e.isEmpty.return_value = False
        e.xMinimum.return_value = float(xmin)
        e.yMinimum.return_value = float(ymin)
        e.xMaximum.return_value = float(xmax)
        e.yMaximum.return_value = float(ymax)
        return e

    def test_returns_none_unchanged(self):
        service = BackgroundMapService()
        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject"):
            result = service.snap_extent_to_background_tile_zoom(None, MagicMock())
        self.assertIsNone(result)

    def test_returns_empty_extent_unchanged(self):
        service = BackgroundMapService()
        extent = MagicMock()
        extent.isEmpty.return_value = True
        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject"):
            result = service.snap_extent_to_background_tile_zoom(extent, MagicMock())
        self.assertIs(result, extent)

    def test_returns_unchanged_when_not_web_mercator(self):
        service = BackgroundMapService()
        extent = self._make_extent(0, 0, 1000, 1000)
        mock_project = _make_project_mock()
        mock_project.crs.return_value.authid.return_value = "EPSG:4326"

        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp:
            qp.instance.return_value = mock_project
            result = service.snap_extent_to_background_tile_zoom(extent, MagicMock())

        self.assertIs(result, extent)

    def test_returns_unchanged_when_no_raster_background(self):
        service = BackgroundMapService()
        extent = self._make_extent(0, 0, 1_000_000, 1_000_000)
        mock_project = _make_project_mock()
        mock_project.crs.return_value.authid.return_value = "EPSG:3857"
        mock_project.mapLayers.return_value = {}

        canvas = MagicMock()
        canvas.width.return_value = 1024
        canvas.height.return_value = 768

        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp:
            qp.instance.return_value = mock_project
            result = service.snap_extent_to_background_tile_zoom(extent, canvas)

        self.assertIs(result, extent)

    def test_snaps_extent_when_raster_background_present(self):
        try:
            from qgis.core import QgsRasterLayer  # noqa: PLC0415
        except ImportError:  # pragma: no cover
            self.skipTest("QgsRasterLayer not importable")

        service = BackgroundMapService()
        extent = self._make_extent(700_000, 5_700_000, 1_100_000, 5_980_000)
        canvas = MagicMock()
        canvas.width.return_value = 1024
        canvas.height.return_value = 768

        raster_layer = MagicMock(spec=QgsRasterLayer)
        raster_layer.name.return_value = f"{BACKGROUND_LAYER_PREFIX} — Outdoor"

        mock_project = _make_project_mock({"bg-id": raster_layer})
        mock_project.crs.return_value.authid.return_value = "EPSG:3857"

        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp, \
             patch("qfit.visualization.infrastructure.background_map_service.QgsRectangle") as mock_rect:
            qp.instance.return_value = mock_project
            mock_rect.return_value = MagicMock()
            service.snap_extent_to_background_tile_zoom(extent, canvas)

        mock_rect.assert_called_once()
        self.assertEqual(len(mock_rect.call_args[0]), 4)


@unittest.skipUnless(QGIS_AVAILABLE, SKIP_REAL)
class RemoveBackgroundLayersTests(unittest.TestCase):
    def test_removes_background_layers_only(self):
        service = BackgroundMapService()
        bg_layer = MagicMock()
        bg_layer.name.return_value = f"{BACKGROUND_LAYER_PREFIX} — Outdoor"
        bg_layer.id.return_value = "bg-1"
        other_layer = MagicMock()
        other_layer.name.return_value = "qfit activities"
        other_layer.id.return_value = "act-1"

        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp:
            qp.instance.return_value = _make_project_mock({"bg-1": bg_layer, "act-1": other_layer})
            service._remove_background_layers()

        qp.instance.return_value.removeMapLayer.assert_called_once_with("bg-1")

    def test_no_removal_when_no_background_layers(self):
        service = BackgroundMapService()
        layer = MagicMock()
        layer.name.return_value = "qfit activities"

        with patch("qfit.visualization.infrastructure.background_map_service.QgsProject") as qp:
            qp.instance.return_value = _make_project_mock({"act-1": layer})
            service._remove_background_layers()

        qp.instance.return_value.removeMapLayer.assert_not_called()


# ===========================================================================
# Suite 2 — mock-QGIS (skipped when QGIS is installed)
# ===========================================================================

@unittest.skipIf(QGIS_AVAILABLE, SKIP_MOCK)
@unittest.skipIf(_mock_bms_cls is None, SKIP_MOCK_LOAD)
class EnsureBackgroundLayerMockTests(unittest.TestCase):
    """Covers ensure_background_layer paths without a real QGIS session."""

    def setUp(self):
        self._sys_patch = patch.dict(
            "sys.modules",
            {
                "qgis": _qstub,
                "qgis.core": _qstub,
                "qgis.PyQt": _qstub,
                "qgis.PyQt.QtGui": _qstub,
            },
        )
        self._sys_patch.start()
        self.service = _mock_bms_cls()
        self.mock_project = _make_project_mock()
        _mock_bms_mod.QgsProject.instance.return_value = self.mock_project

    def tearDown(self):
        self._sys_patch.stop()

    def test_disabled_returns_none_and_removes_layers(self):
        bg = MagicMock()
        bg.name.return_value = "qfit background — Outdoor"
        bg.id.return_value = "bg-1"
        self.mock_project.mapLayers.return_value = {"bg-1": bg}

        result = self.service.ensure_background_layer(
            enabled=False, preset_name="Outdoor", access_token="tok"
        )
        self.assertIsNone(result)
        self.mock_project.removeMapLayer.assert_called_once_with("bg-1")

    def test_enabled_raster_creates_and_adds_layer(self):
        # QgsRasterLayer is a real type subclassing MagicMock; calling it creates
        # an instance whose isValid() returns a truthy MagicMock by default.
        self.mock_project.mapLayers.return_value = {}
        self.mock_project.layerTreeRoot.return_value.children.return_value = []

        result = self.service.ensure_background_layer(
            enabled=True,
            preset_name="Outdoor",
            access_token="tok",
            tile_mode="Raster",
        )

        self.mock_project.addMapLayer.assert_called_once()
        self.assertIsNotNone(result)

    def test_enabled_raster_raises_when_invalid(self):
        # Patch QgsRasterLayer to return a layer whose isValid() returns False.
        invalid_layer = MagicMock()
        invalid_layer.isValid.return_value = False
        self.mock_project.mapLayers.return_value = {}

        from unittest.mock import patch as _patch  # noqa: PLC0415
        with _patch.object(_mock_bms_mod, "QgsRasterLayer", return_value=invalid_layer):
            with self.assertRaises(RuntimeError):
                self.service.ensure_background_layer(
                    enabled=True,
                    preset_name="Outdoor",
                    access_token="tok",
                    tile_mode="Raster",
                )

    def test_enabled_vector_passes_sprite_resources_to_style_conversion(self):
        vector_layer = MagicMock()
        vector_layer.isValid.return_value = True
        sprite_resources = SimpleNamespace(definitions={"marker": {"x": 0}}, image_bytes=b"png")

        style_definition = {"sources": {}, "sprite": "mapbox://sprites/shared-owner/shared-style"}

        with patch.object(_mock_bms_mod, "fetch_mapbox_style_definition", return_value=style_definition) as style_fetch, \
             patch.object(_mock_bms_mod, "simplify_mapbox_style_expressions", return_value={"layers": []}), \
             patch.object(_mock_bms_mod, "fetch_mapbox_sprite_resources", return_value=sprite_resources) as sprite_fetch, \
             patch.object(_mock_bms_mod, "extract_mapbox_vector_source_ids", return_value=["mapbox.mapbox-streets-v8"]), \
             patch.object(_mock_bms_mod, "build_vector_tile_layer_uri", return_value="vector://style") as uri_builder, \
             patch.object(_mock_bms_mod, "QgsVectorTileLayer", return_value=vector_layer), \
             patch.object(self.service, "_apply_mapbox_gl_style") as apply_style:
            result = self.service.ensure_background_layer(
                enabled=True,
                preset_name="Outdoor",
                access_token="tok",
                tile_mode="Vector",
            )

        self.assertIs(result, vector_layer)
        style_fetch.assert_called_once_with("tok", "mapbox", "outdoors-v12")
        sprite_fetch.assert_called_once_with(
            "tok",
            "mapbox",
            "outdoors-v12",
            sprite_url="mapbox://sprites/shared-owner/shared-style",
        )
        uri_builder.assert_called_once()
        apply_style.assert_called_once_with(vector_layer, {"layers": []}, sprite_resources=sprite_resources)

    def test_enabled_vector_continues_without_unavailable_sprite_resources(self):
        vector_layer = MagicMock()
        vector_layer.isValid.return_value = True

        with patch.object(_mock_bms_mod, "fetch_mapbox_style_definition", return_value={"sources": {}, "sprite": "mapbox://sprites/shared-owner/shared-style"}), \
             patch.object(_mock_bms_mod, "simplify_mapbox_style_expressions", return_value={"layers": []}), \
             patch.object(_mock_bms_mod, "fetch_mapbox_sprite_resources", side_effect=OSError("offline")), \
             patch.object(_mock_bms_mod, "extract_mapbox_vector_source_ids", return_value=["mapbox.mapbox-streets-v8"]), \
             patch.object(_mock_bms_mod, "build_vector_tile_layer_uri", return_value="vector://style"), \
             patch.object(_mock_bms_mod, "QgsVectorTileLayer", return_value=vector_layer), \
             patch.object(self.service, "_apply_mapbox_gl_style") as apply_style:
            result = self.service.ensure_background_layer(
                enabled=True,
                preset_name="Outdoor",
                access_token="tok",
                tile_mode="Vector",
            )

        self.assertIs(result, vector_layer)
        apply_style.assert_called_once_with(vector_layer, {"layers": []}, sprite_resources=None)


@unittest.skipIf(QGIS_AVAILABLE, SKIP_MOCK)
@unittest.skipIf(_mock_bms_cls is None, SKIP_MOCK_LOAD)
class MoveBackgroundLayersMockTests(unittest.TestCase):
    def setUp(self):
        self._sys_patch = patch.dict(
            "sys.modules", {"qgis": _qstub, "qgis.core": _qstub}
        )
        self._sys_patch.start()
        self.service = _mock_bms_cls()
        self.mock_project = _make_project_mock()
        _mock_bms_mod.QgsProject.instance.return_value = self.mock_project

    def tearDown(self):
        self._sys_patch.stop()

    def test_background_moved_to_end(self):
        bg_node, _ = _make_layer_node("qfit background — Outdoor")
        other_node, _ = _make_layer_node("qfit activities")
        mock_root = MagicMock()
        mock_root.children.return_value = [bg_node, other_node]
        self.mock_project.layerTreeRoot.return_value = mock_root

        self.service.move_background_layers_to_bottom()

        mock_root.reorderChildren.assert_called_once_with([other_node, bg_node])

    def test_no_reorder_when_already_at_bottom(self):
        other_node, _ = _make_layer_node("qfit activities")
        bg_node, _ = _make_layer_node("qfit background — Outdoor")
        mock_root = MagicMock()
        mock_root.children.return_value = [other_node, bg_node]
        self.mock_project.layerTreeRoot.return_value = mock_root

        self.service.move_background_layers_to_bottom()

        mock_root.reorderChildren.assert_not_called()


@unittest.skipIf(QGIS_AVAILABLE, SKIP_MOCK)
@unittest.skipIf(_mock_bms_cls is None, SKIP_MOCK_LOAD)
class RemoveBackgroundLayersMockTests(unittest.TestCase):
    def setUp(self):
        self._sys_patch = patch.dict(
            "sys.modules", {"qgis": _qstub, "qgis.core": _qstub}
        )
        self._sys_patch.start()
        self.service = _mock_bms_cls()
        self.mock_project = _make_project_mock()
        _mock_bms_mod.QgsProject.instance.return_value = self.mock_project

    def tearDown(self):
        self._sys_patch.stop()

    def test_removes_only_background_layers(self):
        bg = MagicMock()
        bg.name.return_value = "qfit background — Outdoor"
        bg.id.return_value = "bg-1"
        other = MagicMock()
        other.name.return_value = "qfit activities"
        self.mock_project.mapLayers.return_value = {"bg-1": bg, "a": other}

        self.service._remove_background_layers()

        self.mock_project.removeMapLayer.assert_called_once_with("bg-1")

    def test_no_removal_when_none_match(self):
        layer = MagicMock()
        layer.name.return_value = "qfit activities"
        self.mock_project.mapLayers.return_value = {"a": layer}

        self.service._remove_background_layers()

        self.mock_project.removeMapLayer.assert_not_called()


@unittest.skipIf(QGIS_AVAILABLE, SKIP_MOCK)
@unittest.skipIf(_mock_bms_cls is None, SKIP_MOCK_LOAD)
class SnapExtentMockTests(unittest.TestCase):
    def setUp(self):
        self._sys_patch = patch.dict(
            "sys.modules", {"qgis": _qstub, "qgis.core": _qstub}
        )
        self._sys_patch.start()
        self.service = _mock_bms_cls()
        self.mock_project = _make_project_mock()
        _mock_bms_mod.QgsProject.instance.return_value = self.mock_project

    def tearDown(self):
        self._sys_patch.stop()

    def _make_extent(self, xmin, ymin, xmax, ymax):
        e = MagicMock()
        e.isEmpty.return_value = False
        e.xMinimum.return_value = float(xmin)
        e.yMinimum.return_value = float(ymin)
        e.xMaximum.return_value = float(xmax)
        e.yMaximum.return_value = float(ymax)
        return e

    def test_returns_none_unchanged(self):
        result = self.service.snap_extent_to_background_tile_zoom(None, MagicMock())
        self.assertIsNone(result)

    def test_returns_empty_extent_unchanged(self):
        extent = MagicMock()
        extent.isEmpty.return_value = True
        result = self.service.snap_extent_to_background_tile_zoom(extent, MagicMock())
        self.assertIs(result, extent)

    def test_returns_unchanged_when_not_web_mercator(self):
        extent = self._make_extent(0, 0, 1000, 1000)
        self.mock_project.crs.return_value.authid.return_value = "EPSG:4326"
        result = self.service.snap_extent_to_background_tile_zoom(extent, MagicMock())
        self.assertIs(result, extent)

    def test_returns_unchanged_when_no_raster_background(self):
        extent = self._make_extent(0, 0, 1_000_000, 1_000_000)
        self.mock_project.crs.return_value.authid.return_value = "EPSG:3857"
        self.mock_project.mapLayers.return_value = {}
        canvas = MagicMock()
        canvas.width.return_value = 1024
        canvas.height.return_value = 768
        result = self.service.snap_extent_to_background_tile_zoom(extent, canvas)
        self.assertIs(result, extent)

    def test_snaps_when_raster_background_present(self):
        extent = self._make_extent(700_000, 5_700_000, 1_100_000, 5_980_000)
        self.mock_project.crs.return_value.authid.return_value = "EPSG:3857"

        # Create a raster layer as an instance of the stub QgsRasterLayer
        raster_layer = _qstub.QgsRasterLayer()
        raster_layer.name.return_value = "qfit background — Outdoor"
        self.mock_project.mapLayers.return_value = {"bg": raster_layer}

        canvas = MagicMock()
        canvas.width.return_value = 1024
        canvas.height.return_value = 768

        result = self.service.snap_extent_to_background_tile_zoom(extent, canvas)

        # QgsRectangle was called with 4 snapped coordinates
        _mock_bms_mod.QgsRectangle.assert_called()
        args = _mock_bms_mod.QgsRectangle.call_args[0]
        self.assertEqual(len(args), 4)


@unittest.skipUnless(QGIS_AVAILABLE, SKIP_REAL)
class ApplyLabelPriorityRealTests(unittest.TestCase):
    """Covers QGIS label-setting post-processing when real QGIS imports are available."""

    def setUp(self):
        from qgis.core import QgsApplication

        from tests.qgis_app import get_shared_qgis_app

        self.qgs = get_shared_qgis_app(QgsApplication)
        self.service = BackgroundMapService()

    def _make_style(self, layer_name, style_name=None):
        style = MagicMock()
        style.layerName.return_value = layer_name
        style.styleName.return_value = style_name if style_name is not None else layer_name
        settings = MagicMock()
        settings.dataDefinedProperties.return_value = MagicMock()
        style.labelSettings.return_value = settings
        return style, settings

    def test_outdoors_colored_shield_color_match_evaluates_in_qgis(self):
        from qgis.core import (
            QgsExpressionContext, QgsFeature, QgsField, QgsFields,
            QgsMapBoxGlStyleConverter, QgsPalLayerSettings,
        )
        from qfit.mapbox_config import simplify_mapbox_style_expressions

        fields = QgsFields()
        fields.append(QgsField("shield"))
        fields.append(QgsField("shield_beta"))
        for field_name, suffix in (("shield", ""), ("shield_beta", "beta-")):
            with self.subTest(field_name=field_name):
                source = {
                    "version": 8, "owner": "mapbox", "id": "outdoors-v12",
                    "sources": {"composite": {"type": "vector"}},
                    "layers": [{
                        "id": f"road-number-shield-2-{suffix}known-icons",
                        "type": "symbol", "source": "composite", "source-layer": "road",
                        "layout": {"text-field": ["get", "ref"], "text-font": ["Noto Sans"]},
                        "paint": {"text-color": "#222222"},
                    }],
                }
                converter = QgsMapBoxGlStyleConverter()
                converter.convert(simplify_mapbox_style_expressions(source))
                labeling = converter.labeling()
                self.assertIsNotNone(labeling)
                settings = labeling.styles()[0].labelSettings()
                prop = settings.dataDefinedProperties().property(QgsPalLayerSettings.Property.Color)
                self.assertTrue(prop.isActive())
                context = QgsExpressionContext()
                context.setFields(fields)
                for shield, expected in (
                    ("rectangle-blue", "#ffffff"), ("rectangle-red", "#ffffff"),
                    ("it-motorway", "#ffffff"),
                    ("rectangle-yellow", "#222222"), ("rectangle-green", "#222222"),
                    ("default", "#222222"), (None, "#222222"),
                ):
                    feature = QgsFeature(fields)
                    feature.setAttribute(field_name, shield)
                    context.setFeature(feature)
                    color, ok = prop.valueAsColor(context)
                    self.assertTrue(ok)
                    self.assertEqual(color.name(), expected)

    def test_priority_uses_qgis_style_name_not_tile_source_layer(self):
        labeling = MagicMock()
        style, settings = self._make_style("place_label", "settlement-major-label")
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.priority, 8)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_priority_uses_base_layer_for_split_style_name(self):
        labeling = MagicMock()
        style, settings = self._make_style("poi_label", "poi-label-z17-plus")
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.priority, 5)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_priority_persists_on_real_qgis_labeling_style_copies(self):
        from qgis.core import (
            QgsPalLayerSettings,
            QgsVectorTileBasicLabeling,
            QgsVectorTileBasicLabelingStyle,
        )

        labeling = QgsVectorTileBasicLabeling()
        style = QgsVectorTileBasicLabelingStyle()
        style.setLayerName("poi_label")
        style.setStyleName("poi-label-z17-plus")
        settings = QgsPalLayerSettings()
        settings.priority = 5
        style.setLabelSettings(settings)
        labeling.setStyles([style])

        self.service._apply_label_priority(labeling)

        updated_style = labeling.styles()[0]
        self.assertEqual(updated_style.styleName(), "poi-label-z17-plus")
        self.assertEqual(updated_style.layerName(), "poi_label")
        self.assertEqual(updated_style.labelSettings().priority, 5)

    def test_priority_promotes_z11_plus_road_shields_without_promoting_below_z11(self):
        labeling = MagicMock()
        swiss_style, swiss_settings = self._make_style(
            "road",
            "road-number-shield-2-beta-ch-motorway-icon-z11-plus",
        )
        swiss_settings.mergeLines = False
        other_style, other_settings = self._make_style(
            "road",
            "road-number-shield-2-beta-ch-motorway-icon-below-z11",
        )
        other_settings.mergeLines = False
        remaining_style, remaining_settings = self._make_style(
            "road",
            "road-number-shield-2-beta-remaining-icons-z11-plus",
        )
        remaining_settings.mergeLines = False
        labeling.styles.return_value = [swiss_style, other_style, remaining_style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(swiss_settings.priority, 6)
        self.assertEqual(remaining_settings.priority, 6)
        self.assertTrue(swiss_settings.mergeLines)
        self.assertFalse(other_settings.mergeLines)
        self.assertTrue(remaining_settings.mergeLines)
        swiss_style.setLabelSettings.assert_called_once_with(swiss_settings)
        other_style.setLabelSettings.assert_not_called()
        remaining_style.setLabelSettings.assert_called_once_with(remaining_settings)

    def test_road_number_shield_z11_plus_repeat_distance_uses_audited_spacing(self):
        from qgis.core import Qgis

        labeling = MagicMock()
        style, settings = self._make_style(
            "road",
            "road-number-shield-2-beta-remaining-icons-z11-plus",
        )
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertAlmostEqual(settings.repeatDistance, (1400.0 / 3.0) * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_high_zoom_road_label_repeat_distance_uses_audited_spacing(self):
        from qgis.core import Qgis

        labeling = MagicMock()
        style, settings = self._make_style("road", "road-label-z15-plus")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertEqual(settings.priority, 4)
        self.assertAlmostEqual(settings.repeatDistance, 400 * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_high_zoom_road_label_overrides_qgis_default_repeat_distance(self):
        from qgis.core import Qgis

        labeling = MagicMock()
        style, settings = self._make_style("road", "road-label-z15-plus")
        settings.mergeLines = False
        settings.repeatDistance = 250 * 25.4 / 96
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertAlmostEqual(settings.repeatDistance, 400 * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_low_zoom_road_label_repeat_distance_uses_audited_spacing(self):
        labeling = MagicMock()
        style, settings = self._make_style("road", "road-label-z12-to-z15")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertEqual(settings.priority, 4)
        self.assertAlmostEqual(settings.repeatDistance, 150 * 25.4 / 96)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_waterway_label_repeat_distance_uses_split_symbol_spacing(self):
        cases = [
            ("waterway-label-z13-to-z15", 250),
            ("waterway-label-z15-to-z17", 325),
            ("waterway-label-z17-plus", 600),
        ]

        for style_name, spacing_px in cases:
            with self.subTest(style_name=style_name):
                labeling = MagicMock()
                style, settings = self._make_style("natural_label", style_name)
                settings.mergeLines = False
                settings.repeatDistance = 0.0
                labeling.styles.return_value = [style]

                self.service._apply_label_priority(labeling)

                self.assertTrue(settings.mergeLines)
                self.assertAlmostEqual(settings.repeatDistance, spacing_px * 25.4 / 96)
                style.setLabelSettings.assert_called_once_with(settings)

    def test_line_center_labels_use_strict_center_line_anchor(self):
        from qgis.core import QgsLabelLineSettings, Qgis

        cases = [
            ("natural_label", "natural-line-label", 4),
            ("natural_label", "water-line-label-ocean-name", 6),
        ]
        for layer_name, style_name, priority in cases:
            with self.subTest(style_name=style_name):
                labeling = MagicMock()
                style, settings = self._make_style(layer_name, style_name)
                settings.placement = Qgis.LabelPlacement.OverPoint
                line_settings = _make_line_label_settings()
                settings.lineSettings.return_value = line_settings
                labeling.styles.return_value = [style]

                self.service._apply_label_priority(labeling)

                self.assertEqual(settings.priority, priority)
                self.assertEqual(settings.placement, Qgis.LabelPlacement.Line)
                style.setGeometryType.assert_called_once_with(Qgis.GeometryType.Line)
                line_settings.setLineAnchorPercent.assert_called_once_with(0.5)
                line_settings.setAnchorTextPoint.assert_called_once_with(
                    QgsLabelLineSettings.AnchorTextPoint.CenterOfText
                )
                line_settings.setAnchorType.assert_called_once_with(
                    QgsLabelLineSettings.AnchorType.Strict
                )
                line_settings.setAnchorClipping.assert_called_once_with(
                    QgsLabelLineSettings.AnchorClipping.UseEntireLine
                )
                settings.setLineSettings.assert_called_once_with(line_settings)
                style.setLabelSettings.assert_called_once_with(settings)

    def test_line_center_placement_change_does_not_reset_unchanged_anchor(self):
        from qgis.core import QgsLabelLineSettings, Qgis

        labeling = MagicMock()
        style, settings = self._make_style("natural_label", "natural-line-label")
        style.geometryType.return_value = Qgis.GeometryType.Line
        settings.priority = 4
        settings.placement = Qgis.LabelPlacement.OverPoint
        line_settings = _make_line_label_settings(
            line_anchor_percent=0.5,
            anchor_text_point=QgsLabelLineSettings.AnchorTextPoint.CenterOfText,
            anchor_type=QgsLabelLineSettings.AnchorType.Strict,
            anchor_clipping=QgsLabelLineSettings.AnchorClipping.UseEntireLine,
        )
        settings.lineSettings.return_value = line_settings
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.placement, Qgis.LabelPlacement.Line)
        line_settings.setLineAnchorPercent.assert_not_called()
        line_settings.setAnchorTextPoint.assert_not_called()
        line_settings.setAnchorType.assert_not_called()
        line_settings.setAnchorClipping.assert_not_called()
        settings.setLineSettings.assert_not_called()
        style.setGeometryType.assert_not_called()
        style.setLabelSettings.assert_called_once_with(settings)

    def test_ferry_aerialway_labels_merge_matching_line_segments(self):
        from qgis.core import Qgis

        labeling = MagicMock()
        style, settings = self._make_style("road", "ferry-aerialway-label")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertAlmostEqual(settings.repeatDistance, 250 * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_path_pedestrian_labels_merge_matching_line_segments(self):
        from qgis.core import Qgis

        labeling = MagicMock()
        style, settings = self._make_style("road", "path-pedestrian-label")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertAlmostEqual(settings.repeatDistance, 400 * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_contour_label_appends_metre_suffix_without_repeat_distance(self):
        labeling = MagicMock()
        style, settings = self._make_style("contour", "contour-label")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        settings.fieldName = '"ele"'
        settings.isExpression = True
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.fieldName, "concat(\"ele\", ' m')")
        self.assertTrue(settings.isExpression)
        self.assertTrue(settings.mergeLines)
        self.assertEqual(settings.repeatDistance, 0.0)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_contour_label_suffix_expression_is_left_unchanged(self):
        labeling = MagicMock()
        style, settings = self._make_style("contour", "contour-label")
        settings.mergeLines = True
        settings.repeatDistance = 0.0
        settings.fieldName = "concat(\"ele\", ' m')"
        settings.isExpression = True
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        style.setLabelSettings.assert_not_called()

    def test_contour_labels_merge_matching_line_segments_without_field_change(self):
        labeling = MagicMock()
        style, settings = self._make_style("contour", "contour-label")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        settings.fieldName = "concat(\"ele\", ' m')"
        settings.isExpression = True
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertEqual(settings.fieldName, "concat(\"ele\", ' m')")
        self.assertTrue(settings.isExpression)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_custom_contour_label_field_is_left_unchanged(self):
        labeling = MagicMock()
        style, settings = self._make_style("contour", "contour-label")
        settings.mergeLines = True
        settings.repeatDistance = 0.0
        settings.fieldName = '"height_ft"'
        settings.isExpression = True
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.fieldName, '"height_ft"')
        style.setLabelSettings.assert_not_called()


@unittest.skipIf(QGIS_AVAILABLE, SKIP_MOCK)
@unittest.skipIf(_mock_bms_cls is None, SKIP_MOCK_LOAD)
class ApplyLabelPriorityMockTests(unittest.TestCase):
    """Covers the label priority loop with mock styles."""

    def setUp(self):
        self._sys_patch = patch.dict(
            "sys.modules", {"qgis": _qstub, "qgis.core": _qstub}
        )
        self._sys_patch.start()
        self.service = _mock_bms_cls()

    def tearDown(self):
        self._sys_patch.stop()

    def _make_style(self, layer_name, style_name=None):
        style = MagicMock()
        style.layerName.return_value = layer_name
        style.styleName.return_value = style_name if style_name is not None else layer_name
        settings = MagicMock()
        settings.dataDefinedProperties.return_value = MagicMock()
        style.labelSettings.return_value = settings
        return style, settings

    def test_priority_set_for_known_layer(self):
        labeling = MagicMock()
        style, settings = self._make_style("country-label")
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.priority, 10)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_priority_set_for_split_poi_layer(self):
        labeling = MagicMock()
        style, settings = self._make_style("poi-label-z17-plus")
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.priority, 5)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_priority_uses_qgis_style_name_not_tile_source_layer(self):
        labeling = MagicMock()
        style, settings = self._make_style("place_label", "settlement-major-label")
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.priority, 8)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_priority_uses_base_layer_for_split_style_name(self):
        labeling = MagicMock()
        style, settings = self._make_style("poi_label", "poi-label-z17-plus")
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.priority, 5)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_priority_tuning_favors_airport_and_poi_over_roads(self):
        cases = [
            ("airport_label", "airport-label", 8),
            ("poi_label", "poi-label", 5),
            ("road", "road-label", 4),
            ("natural_label", "natural-point-label", 4),
        ]
        for layer_name, style_name, expected_priority in cases:
            with self.subTest(style_name=style_name):
                labeling = MagicMock()
                style, settings = self._make_style(layer_name, style_name)
                labeling.styles.return_value = [style]

                self.service._apply_label_priority(labeling)

                self.assertEqual(settings.priority, expected_priority)
                style.setLabelSettings.assert_called_once_with(settings)

    def test_priority_promotes_z11_plus_road_shields_without_promoting_below_z11(self):
        labeling = MagicMock()
        swiss_style, swiss_settings = self._make_style(
            "road",
            "road-number-shield-2-beta-ch-motorway-icon-z11-plus",
        )
        swiss_settings.mergeLines = False
        other_style, other_settings = self._make_style(
            "road",
            "road-number-shield-2-beta-ch-motorway-icon-below-z11",
        )
        other_settings.mergeLines = False
        remaining_style, remaining_settings = self._make_style(
            "road",
            "road-number-shield-2-beta-remaining-icons-z11-plus",
        )
        remaining_settings.mergeLines = False
        labeling.styles.return_value = [swiss_style, other_style, remaining_style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(swiss_settings.priority, 6)
        self.assertEqual(remaining_settings.priority, 6)
        self.assertTrue(swiss_settings.mergeLines)
        self.assertFalse(other_settings.mergeLines)
        self.assertTrue(remaining_settings.mergeLines)
        swiss_style.setLabelSettings.assert_called_once_with(swiss_settings)
        other_style.setLabelSettings.assert_not_called()
        remaining_style.setLabelSettings.assert_called_once_with(remaining_settings)

    def test_road_number_shield_z11_plus_repeat_distance_uses_audited_spacing(self):
        labeling = MagicMock()
        style, settings = self._make_style(
            "road",
            "road-number-shield-2-beta-remaining-icons-z11-plus",
        )
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertAlmostEqual(settings.repeatDistance, (1400.0 / 3.0) * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, _qstub.Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_high_zoom_road_label_repeat_distance_uses_audited_spacing(self):
        labeling = MagicMock()
        style, settings = self._make_style("road", "road-label-z15-plus")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertEqual(settings.priority, 4)
        self.assertAlmostEqual(settings.repeatDistance, 400 * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, _qstub.Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_high_zoom_road_label_overrides_qgis_default_repeat_distance(self):
        labeling = MagicMock()
        style, settings = self._make_style("road", "road-label-z15-plus")
        settings.mergeLines = False
        settings.repeatDistance = 250 * 25.4 / 96
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertAlmostEqual(settings.repeatDistance, 400 * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, _qstub.Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_low_zoom_road_label_repeat_distance_uses_audited_spacing(self):
        labeling = MagicMock()
        style, settings = self._make_style("road", "road-label-below-z12")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertEqual(settings.priority, 4)
        self.assertAlmostEqual(settings.repeatDistance, 150 * 25.4 / 96)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_waterway_label_repeat_distance_uses_split_symbol_spacing(self):
        cases = [
            ("waterway-label-z13-to-z15", 250),
            ("waterway-label-z15-to-z17", 325),
            ("waterway-label-z17-plus", 600),
        ]

        for style_name, spacing_px in cases:
            with self.subTest(style_name=style_name):
                labeling = MagicMock()
                style, settings = self._make_style("natural_label", style_name)
                settings.mergeLines = False
                settings.repeatDistance = 0.0
                labeling.styles.return_value = [style]

                self.service._apply_label_priority(labeling)

                self.assertTrue(settings.mergeLines)
                self.assertAlmostEqual(settings.repeatDistance, spacing_px * 25.4 / 96)
                style.setLabelSettings.assert_called_once_with(settings)

    def test_line_center_labels_use_strict_center_line_anchor(self):
        cases = [
            ("natural_label", "natural-line-label", 4),
            ("natural_label", "water-line-label-ocean-name", 6),
        ]
        for layer_name, style_name, priority in cases:
            with self.subTest(style_name=style_name):
                labeling = MagicMock()
                style, settings = self._make_style(layer_name, style_name)
                settings.placement = _qstub.Qgis.LabelPlacement.OverPoint
                line_settings = _make_line_label_settings()
                settings.lineSettings.return_value = line_settings
                labeling.styles.return_value = [style]

                self.service._apply_label_priority(labeling)

                self.assertEqual(settings.priority, priority)
                self.assertEqual(settings.placement, _qstub.Qgis.LabelPlacement.Line)
                style.setGeometryType.assert_called_once_with(_qstub.Qgis.GeometryType.Line)
                line_settings.setLineAnchorPercent.assert_called_once_with(0.5)
                line_settings.setAnchorTextPoint.assert_called_once_with(
                    _qstub.QgsLabelLineSettings.AnchorTextPoint.CenterOfText
                )
                line_settings.setAnchorType.assert_called_once_with(
                    _qstub.QgsLabelLineSettings.AnchorType.Strict
                )
                line_settings.setAnchorClipping.assert_called_once_with(
                    _qstub.QgsLabelLineSettings.AnchorClipping.UseEntireLine
                )
                settings.setLineSettings.assert_called_once_with(line_settings)
                style.setLabelSettings.assert_called_once_with(settings)

    def test_line_center_placement_change_does_not_reset_unchanged_anchor(self):
        labeling = MagicMock()
        style, settings = self._make_style("natural_label", "natural-line-label")
        style.geometryType.return_value = _qstub.Qgis.GeometryType.Line
        settings.priority = 4
        settings.placement = _qstub.Qgis.LabelPlacement.OverPoint
        line_settings = _make_line_label_settings(
            line_anchor_percent=0.5,
            anchor_text_point=_qstub.QgsLabelLineSettings.AnchorTextPoint.CenterOfText,
            anchor_type=_qstub.QgsLabelLineSettings.AnchorType.Strict,
            anchor_clipping=_qstub.QgsLabelLineSettings.AnchorClipping.UseEntireLine,
        )
        settings.lineSettings.return_value = line_settings
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.placement, _qstub.Qgis.LabelPlacement.Line)
        line_settings.setLineAnchorPercent.assert_not_called()
        line_settings.setAnchorTextPoint.assert_not_called()
        line_settings.setAnchorType.assert_not_called()
        line_settings.setAnchorClipping.assert_not_called()
        settings.setLineSettings.assert_not_called()
        style.setGeometryType.assert_not_called()
        style.setLabelSettings.assert_called_once_with(settings)

    def test_ferry_aerialway_labels_merge_matching_line_segments(self):
        labeling = MagicMock()
        style, settings = self._make_style("road", "ferry-aerialway-label")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertAlmostEqual(settings.repeatDistance, 250 * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, _qstub.Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_path_pedestrian_labels_merge_matching_line_segments(self):
        labeling = MagicMock()
        style, settings = self._make_style("road", "path-pedestrian-label")
        settings.mergeLines = False
        settings.repeatDistance = 0.0
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertAlmostEqual(settings.repeatDistance, 400 * 25.4 / 96)
        self.assertEqual(settings.repeatDistanceUnit, _qstub.Qgis.RenderUnit.Millimeters)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_existing_line_label_repeat_distance_is_preserved(self):
        labeling = MagicMock()
        style, settings = self._make_style("road", "road-label-z15-plus")
        settings.repeatDistance = 12.5
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.priority, 4)
        self.assertEqual(settings.repeatDistance, 12.5)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_contour_label_suffix_expression_is_left_unchanged(self):
        labeling = MagicMock()
        style, settings = self._make_style("contour", "contour-label")
        settings.mergeLines = True
        settings.repeatDistance = 0.0
        settings.fieldName = "concat(\"ele\", ' m')"
        settings.isExpression = True
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        style.setLabelSettings.assert_not_called()

    def test_data_defined_priority_for_settlement_layer(self):
        labeling = MagicMock()
        style, settings = self._make_style("settlement-major-label")
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        dd_props = settings.dataDefinedProperties.return_value
        dd_props.setProperty.assert_called_once()
        args = dd_props.setProperty.call_args[0]
        self.assertEqual(args[0], 87)  # QgsPalLayerSettings.Priority
        expression_arg = _qstub.QgsProperty.fromExpression.call_args[0][0]
        self.assertIn('to_int("symbolrank")', expression_arg)
        self.assertIn('to_int("sizerank")', expression_arg)
        self.assertLess(
            expression_arg.index('to_int("symbolrank")'),
            expression_arg.index('to_int("sizerank")'),
            "symbolrank must appear before sizerank in the coalesce expression",
        )

    def test_unknown_layer_is_skipped(self):
        labeling = MagicMock()
        style, settings = self._make_style("unknown-layer-xyz")
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        style.setLabelSettings.assert_not_called()

    def test_contour_label_appends_metre_suffix(self):
        labeling = MagicMock()
        style, settings = self._make_style("contour", "contour-label")
        settings.mergeLines = False
        settings.fieldName = '"ele"'
        settings.isExpression = True
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.fieldName, "concat(\"ele\", ' m')")
        self.assertTrue(settings.isExpression)
        self.assertTrue(settings.mergeLines)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_contour_labels_merge_matching_line_segments_without_field_change(self):
        labeling = MagicMock()
        style, settings = self._make_style("contour", "contour-label")
        settings.mergeLines = False
        settings.fieldName = "concat(\"ele\", ' m')"
        settings.isExpression = True
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertTrue(settings.mergeLines)
        self.assertEqual(settings.fieldName, "concat(\"ele\", ' m')")
        self.assertTrue(settings.isExpression)
        style.setLabelSettings.assert_called_once_with(settings)

    def test_contour_label_appends_high_zoom_bbox_edge_style(self):
        labeling = MagicMock()
        source_style, source_settings = self._make_style("contour", "contour-label")
        source_settings.fieldName = '"ele"'
        source_settings.isExpression = True
        source_settings.priority = 2
        labeling.styles.return_value = [source_style]
        copied_settings = SimpleNamespace()
        appended_style = MagicMock()
        _qstub.QgsPalLayerSettings.reset_mock(return_value=True, side_effect=True)
        _qstub.QgsPalLayerSettings.return_value = copied_settings
        _qstub.QgsVectorTileBasicLabelingStyle.reset_mock(return_value=True, side_effect=True)
        _qstub.QgsVectorTileBasicLabelingStyle.return_value = appended_style

        self.service._apply_label_priority(labeling)

        _qstub.QgsPalLayerSettings.assert_called_once_with(source_settings)
        appended_style.setStyleName.assert_called_once_with(
            _mock_bms_mod._CONTOUR_LABEL_BBOX_EDGE_DIFFERENCE_STYLE_NAME
        )
        appended_style.setLayerName.assert_called_once_with("contour")
        appended_style.setGeometryType.assert_called_once_with(_qstub.Qgis.GeometryType.Polygon)
        appended_style.setFilterExpression.assert_called_once_with(
            _mock_bms_mod._CONTOUR_LABEL_BBOX_EDGE_DIFFERENCE_FILTER
        )
        appended_style.setMinZoomLevel.assert_called_once_with(
            _mock_bms_mod._CONTOUR_LABEL_BBOX_EDGE_DIFFERENCE_MIN_ZOOM
        )
        appended_style.setLabelSettings.assert_called_once_with(copied_settings)
        source_style.setMaxZoomLevel.assert_called_once_with(
            _mock_bms_mod._CONTOUR_LABEL_MAX_ZOOM_BEFORE_BBOX_EDGE_DIFFERENCE
        )
        self.assertEqual(copied_settings.fieldName, "concat(\"ele\", ' m')")
        self.assertTrue(copied_settings.isExpression)
        self.assertEqual(copied_settings.placement, _qstub.QgsPalLayerSettings.Curved)
        self.assertEqual(copied_settings.priority, 3)
        self.assertEqual(
            copied_settings.geometryGenerator,
            _mock_bms_mod._CONTOUR_LABEL_BBOX_EDGE_DIFFERENCE_EXPRESSION,
        )
        self.assertTrue(copied_settings.geometryGeneratorEnabled)
        self.assertEqual(copied_settings.geometryGeneratorType, _qstub.Qgis.GeometryType.Line)
        labeling.setStyles.assert_called_once_with([source_style, appended_style])

    def test_contour_label_clone_failure_keeps_source_label_uncapped(self):
        labeling = MagicMock()
        source_style, source_settings = self._make_style("contour", "contour-label")
        source_style.maxZoomLevel.return_value = -1
        source_settings.mergeLines = True
        source_settings.fieldName = "concat(\"ele\", ' m')"
        source_settings.isExpression = True
        labeling.styles.return_value = [source_style]
        _qstub.QgsPalLayerSettings.reset_mock(return_value=True, side_effect=True)
        _qstub.QgsPalLayerSettings.side_effect = RuntimeError("copy failed")
        _qstub.QgsVectorTileBasicLabelingStyle.reset_mock(
            return_value=True,
            side_effect=True,
        )

        self.service._apply_label_priority(labeling)

        source_style.setMaxZoomLevel.assert_not_called()
        _qstub.QgsVectorTileBasicLabelingStyle.assert_not_called()
        labeling.setStyles.assert_not_called()

    def test_contour_label_cap_failure_still_appends_bbox_edge_style(self):
        labeling = MagicMock()
        source_style, source_settings = self._make_style("contour", "contour-label")
        source_style.setMaxZoomLevel.side_effect = RuntimeError("cap failed")
        source_settings.mergeLines = True
        source_settings.fieldName = "concat(\"ele\", ' m')"
        source_settings.isExpression = True
        labeling.styles.return_value = [source_style]
        copied_settings = SimpleNamespace()
        appended_style = MagicMock()
        _qstub.QgsPalLayerSettings.reset_mock(return_value=True, side_effect=True)
        _qstub.QgsPalLayerSettings.return_value = copied_settings
        _qstub.QgsVectorTileBasicLabelingStyle.reset_mock(
            return_value=True,
            side_effect=True,
        )
        _qstub.QgsVectorTileBasicLabelingStyle.return_value = appended_style

        self.service._apply_label_priority(labeling)

        source_style.setMaxZoomLevel.assert_called_once_with(
            _mock_bms_mod._CONTOUR_LABEL_MAX_ZOOM_BEFORE_BBOX_EDGE_DIFFERENCE
        )
        appended_style.setLabelSettings.assert_called_once_with(copied_settings)
        labeling.setStyles.assert_called_once_with([source_style, appended_style])

    def test_existing_high_zoom_bbox_edge_style_caps_uncapped_source_label(self):
        labeling = MagicMock()
        source_style, source_settings = self._make_style("contour", "contour-label")
        source_style.maxZoomLevel.return_value = -1
        source_settings.mergeLines = True
        source_settings.fieldName = "concat(\"ele\", ' m')"
        source_settings.isExpression = True
        existing_style, _existing_settings = self._make_style(
            "contour",
            _mock_bms_mod._CONTOUR_LABEL_BBOX_EDGE_DIFFERENCE_STYLE_NAME,
        )
        labeling.styles.return_value = [source_style, existing_style]
        _qstub.QgsPalLayerSettings.reset_mock(return_value=True, side_effect=True)
        _qstub.QgsVectorTileBasicLabelingStyle.reset_mock(
            return_value=True,
            side_effect=True,
        )

        self.service._apply_label_priority(labeling)

        source_style.setMaxZoomLevel.assert_called_once_with(
            _mock_bms_mod._CONTOUR_LABEL_MAX_ZOOM_BEFORE_BBOX_EDGE_DIFFERENCE
        )
        _qstub.QgsPalLayerSettings.assert_not_called()
        _qstub.QgsVectorTileBasicLabelingStyle.assert_not_called()
        labeling.setStyles.assert_called_once_with([source_style, existing_style])

    def test_custom_contour_label_field_does_not_append_high_zoom_bbox_edge_style(self):
        labeling = MagicMock()
        style, settings = self._make_style("contour", "contour-label")
        settings.mergeLines = True
        settings.fieldName = '"height_ft"'
        settings.isExpression = True
        labeling.styles.return_value = [style]
        _qstub.QgsPalLayerSettings.reset_mock(return_value=True, side_effect=True)
        _qstub.QgsVectorTileBasicLabelingStyle.reset_mock(return_value=True, side_effect=True)

        self.service._apply_label_priority(labeling)

        _qstub.QgsPalLayerSettings.assert_not_called()
        _qstub.QgsVectorTileBasicLabelingStyle.assert_not_called()
        style.setMaxZoomLevel.assert_not_called()
        labeling.setStyles.assert_not_called()

    def test_existing_high_zoom_bbox_edge_style_is_not_duplicated(self):
        labeling = MagicMock()
        source_style, source_settings = self._make_style("contour", "contour-label")
        source_style.maxZoomLevel.return_value = (
            _mock_bms_mod._CONTOUR_LABEL_MAX_ZOOM_BEFORE_BBOX_EDGE_DIFFERENCE
        )
        source_settings.mergeLines = True
        source_settings.fieldName = "concat(\"ele\", ' m')"
        source_settings.isExpression = True
        existing_style, _existing_settings = self._make_style(
            "contour",
            _mock_bms_mod._CONTOUR_LABEL_BBOX_EDGE_DIFFERENCE_STYLE_NAME,
        )
        labeling.styles.return_value = [source_style, existing_style]
        _qstub.QgsPalLayerSettings.reset_mock(return_value=True, side_effect=True)
        _qstub.QgsVectorTileBasicLabelingStyle.reset_mock(return_value=True, side_effect=True)

        self.service._apply_label_priority(labeling)

        _qstub.QgsPalLayerSettings.assert_not_called()
        _qstub.QgsVectorTileBasicLabelingStyle.assert_not_called()
        source_style.setMaxZoomLevel.assert_not_called()
        labeling.setStyles.assert_not_called()

    def test_custom_contour_label_field_is_left_unchanged(self):
        labeling = MagicMock()
        style, settings = self._make_style("contour", "contour-label")
        settings.mergeLines = True
        settings.fieldName = '"height_ft"'
        settings.isExpression = True
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        self.assertEqual(settings.fieldName, '"height_ft"')
        style.setLabelSettings.assert_not_called()

    def test_none_settings_is_skipped(self):
        labeling = MagicMock()
        style = MagicMock()
        style.layerName.return_value = "country-label"
        style.styleName.return_value = "country-label"
        style.labelSettings.return_value = None
        labeling.styles.return_value = [style]

        self.service._apply_label_priority(labeling)

        style.setLabelSettings.assert_not_called()

    def test_runtime_error_is_swallowed(self):
        labeling = MagicMock()
        labeling.styles.side_effect = RuntimeError("boom")
        # Should not raise
        self.service._apply_label_priority(labeling)


@unittest.skipIf(QGIS_AVAILABLE, SKIP_MOCK)
@unittest.skipIf(_mock_bms_cls is None, SKIP_MOCK_LOAD)
class ApplyMapboxGlStyleMockTests(unittest.TestCase):
    def setUp(self):
        self._sys_patch = patch.dict(
            "sys.modules", {"qgis": _qstub, "qgis.core": _qstub}
        )
        self._sys_patch.start()
        self.service = _mock_bms_cls()

        # Wire up converter to return Success so the renderer/labeling branch runs.
        success_sentinel = object()
        _qstub.QgsMapBoxGlStyleConverter.Success = success_sentinel
        _qstub.QgsMapBoxGlStyleConverter.return_value.convert.return_value = success_sentinel

        mock_renderer = MagicMock()
        mock_labeling = MagicMock()
        mock_style = MagicMock()
        mock_style.layerName.return_value = "unknown"
        mock_labeling.styles.return_value = [mock_style]
        _qstub.QgsMapBoxGlStyleConverter.return_value.renderer.return_value = mock_renderer
        _qstub.QgsMapBoxGlStyleConverter.return_value.labeling.return_value = mock_labeling

    def tearDown(self):
        self._sys_patch.stop()

    def test_applies_renderer_and_labeling_on_success(self):
        layer = MagicMock()
        self.service._apply_mapbox_gl_style(layer, {"layers": []})
        layer.setRenderer.assert_called_once()
        layer.setLabeling.assert_called_once()
        layer.setLabelsEnabled.assert_called_once_with(True)

    def test_applies_sprite_resources_to_conversion_context(self):
        layer = MagicMock()
        sprite_image = MagicMock()
        sprite_image.loadFromData.return_value = True
        converted_image = MagicMock()
        sprite_image.convertToFormat.return_value = converted_image
        fake_qt_gui = types.ModuleType("qgis.PyQt.QtGui")
        fake_qt_gui.QImage = MagicMock(return_value=sprite_image)
        fake_qt_gui.QImage.Format_ARGB32 = "argb32"
        sprite_resources = SimpleNamespace(definitions={"marker": {"x": 0}}, image_bytes=b"png-bytes")

        with patch.dict(sys.modules, {"qgis.PyQt.QtGui": fake_qt_gui}):
            self.service._apply_mapbox_gl_style(layer, {"layers": []}, sprite_resources=sprite_resources)

        ctx = _qstub.QgsMapBoxGlStyleConversionContext.return_value
        sprite_image.loadFromData.assert_called_once_with(b"png-bytes")
        sprite_image.convertToFormat.assert_called_once_with("argb32")
        ctx.setSprites.assert_called_once_with(converted_image, {"marker": {"x": 0}})

    def test_sprite_resources_skip_invalid_images(self):
        ctx = MagicMock()
        sprite_image = MagicMock()
        sprite_image.loadFromData.return_value = False
        fake_qt_gui = types.ModuleType("qgis.PyQt.QtGui")
        fake_qt_gui.QImage = MagicMock(return_value=sprite_image)
        sprite_resources = SimpleNamespace(definitions={"marker": {"x": 0}}, image_bytes=b"not-an-image")

        with patch.dict(sys.modules, {"qgis.PyQt.QtGui": fake_qt_gui}):
            self.service._apply_sprite_resources_to_context(ctx, sprite_resources)

        sprite_image.loadFromData.assert_called_once_with(b"not-an-image")
        ctx.setSprites.assert_not_called()

    def test_sprite_resources_skip_qimage_errors(self):
        ctx = MagicMock()
        fake_qt_gui = types.ModuleType("qgis.PyQt.QtGui")
        fake_qt_gui.QImage = MagicMock(side_effect=RuntimeError("qt image unavailable"))
        sprite_resources = SimpleNamespace(definitions={"marker": {"x": 0}}, image_bytes=b"png-bytes")

        with patch.dict(sys.modules, {"qgis.PyQt.QtGui": fake_qt_gui}):
            self.service._apply_sprite_resources_to_context(ctx, sprite_resources)

        ctx.setSprites.assert_not_called()

    def test_skips_when_renderer_is_none(self):
        _qstub.QgsMapBoxGlStyleConverter.return_value.renderer.return_value = None
        layer = MagicMock()
        self.service._apply_mapbox_gl_style(layer, {"layers": []})
        layer.setRenderer.assert_not_called()

    def test_skips_when_labeling_is_none(self):
        _qstub.QgsMapBoxGlStyleConverter.return_value.labeling.return_value = None
        layer = MagicMock()
        self.service._apply_mapbox_gl_style(layer, {"layers": []})
        layer.setLabeling.assert_not_called()
