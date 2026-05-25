import base64
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import _path  # noqa: F401

from qfit.validation.mapbox_outdoors_comparison import (
    CAMERAS,
    CONTACT_SHEET_COLUMN_GAP,
    CONTACT_SHEET_COLUMNS,
    CONTACT_SHEET_THUMBNAIL_WIDTH,
    ComparisonConfig,
    DEFAULT_OUTPUT_ROOT,
    MapboxComparisonCamera,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_EXPRESSION,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_FILTER,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_MIN_ZOOM,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_STYLE_NAME,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_EXPRESSION,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_FILTER,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_MIN_ZOOM,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_STYLE_NAME,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_HIGH_ZOOM_LABEL_PROBE_EXPRESSION,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_HIGH_ZOOM_LABEL_PROBE_FILTER,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_HIGH_ZOOM_LABEL_PROBE_MIN_ZOOM,
    QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_HIGH_ZOOM_LABEL_PROBE_STYLE_NAME,
    QGIS_CONTOUR_BOUNDARY_GENERATOR_LABEL_PROBE_EXPRESSION,
    QGIS_CONTOUR_BOUNDARY_GENERATOR_LABEL_PROBE_FILTER,
    QGIS_CONTOUR_BOUNDARY_GENERATOR_LABEL_PROBE_MIN_ZOOM,
    QGIS_CONTOUR_BOUNDARY_GENERATOR_LABEL_PROBE_STYLE_NAME,
    QGIS_CONTOUR_POLYGON_LABEL_PROBE_FILTER,
    QGIS_CONTOUR_POLYGON_LABEL_PROBE_MIN_ZOOM,
    QGIS_CONTOUR_POLYGON_LABEL_PROBE_STYLE_NAME,
    _append_qgis_contour_bbox_edge_difference_label_probe,
    _append_qgis_contour_bbox_edge_difference_source_style_label_probe,
    _append_qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe,
    _append_qgis_contour_boundary_generator_label_probe,
    _append_enabled_qgis_contour_label_probes,
    _append_qgis_contour_polygon_label_probe,
    _label_setting_value,
    _label_value,
    _load_optional_qgis_runtime_snapshot,
    build_all_cameras_contact_sheet,
    build_comparison_paths,
    build_image_diff,
    build_mapbox_gl_html,
    build_node_playwright_capture_script,
    build_parser,
    build_run_directory,
    camera_center_web_mercator,
    camera_extent_web_mercator,
    encode_browser_capture_html,
    is_valid_qgis_vector_tile_layer,
    list_cameras,
    load_style_definition,
    qgis_runtime_snapshot,
    qgis_label_styles_snapshot,
    redact_sensitive_text,
    render_browser_reference,
    render_qgis_vector,
    resolve_mapbox_token,
    run_comparison,
    write_qgis_label_styles_snapshot,
    write_qgis_core_runtime_snapshot,
    write_qgis_runtime_snapshot,
)


PNG_PLACEHOLDER = b"not-a-real-png-for-unit-tests"
SAMPLE_STYLE = {
    "version": 8,
    "sources": {
        "composite": {
            "type": "vector",
            "url": "mapbox://mapbox.mapbox-streets-v8",
        }
    },
    "layers": [{"id": "background", "type": "background"}],
}


class MapboxOutdoorsComparisonTests(unittest.TestCase):
    def test_default_camera_targets_mapbox_outdoors(self):
        camera = CAMERAS["valais-geneva-outdoors"]

        self.assertEqual(camera.style_owner, "mapbox")
        self.assertEqual(camera.style_id, "outdoors-v12")
        self.assertEqual(camera.style_url, "mapbox://styles/mapbox/outdoors-v12")
        self.assertGreater(camera.width, 0)
        self.assertGreater(camera.height, 0)

    def test_list_cameras_mentions_valais_geneva_camera(self):
        text = list_cameras()

        self.assertIn("valais-geneva-outdoors", text)
        self.assertIn("mapbox/outdoors-v12", text)

    def test_camera_matrix_covers_required_mapbox_outdoors_zoom_bands(self):
        self.assertTrue(5.0 <= CAMERAS["switzerland-alps-z5-outdoors"].zoom <= 5.5)
        self.assertTrue(7.0 <= CAMERAS["valais-geneva-outdoors"].zoom <= 8.5)
        self.assertTrue(9.0 <= CAMERAS["lausanne-lavaux-z10-outdoors"].zoom <= 11.0)
        self.assertTrue(14.0 <= CAMERAS["geneva-airport-motorway-z14-outdoors"].zoom <= 14.5)
        self.assertTrue(13.0 <= CAMERAS["chamonix-trails-z14-outdoors"].zoom <= 14.5)
        self.assertTrue(16.5 <= CAMERAS["zermatt-piste-z17-outdoors"].zoom <= 17.5)
        self.assertIn("piste", CAMERAS["zermatt-piste-z17-outdoors"].description)
        self.assertIn("cycleway", CAMERAS["zermatt-piste-z17-outdoors"].description)
        self.assertGreaterEqual(CAMERAS["zermatt-trails-z18-outdoors"].zoom, 18.0)

        listed = list_cameras()
        for camera_name in CAMERAS:
            self.assertIn(camera_name, listed)

    def test_build_run_directory_uses_timestamped_debug_layout(self):
        run_dir = build_run_directory(
            output_root=Path("/tmp/qfit-mapbox"),
            camera_name="valais-geneva-outdoors",
            now=dt.datetime(2026, 5, 10, 19, 45, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(run_dir, Path("/tmp/qfit-mapbox/valais-geneva-outdoors/20260510T194500Z"))

    def test_build_comparison_paths_are_predictable_png_outputs(self):
        paths = build_comparison_paths(run_dir=Path("/tmp/run"))

        self.assertEqual(paths.browser_png, Path("/tmp/run/mapbox-gl-reference.png"))
        self.assertEqual(paths.qgis_png, Path("/tmp/run/qgis-vector-render.png"))
        self.assertEqual(paths.diff_png, Path("/tmp/run/mapbox-gl-vs-qgis-diff.png"))
        self.assertEqual(paths.metrics_json, Path("/tmp/run/metrics.json"))
        self.assertEqual(paths.qgis_preprocessed_style_json, Path("/tmp/run/qgis-preprocessed-style.json"))
        self.assertEqual(paths.qgis_label_styles_json, Path("/tmp/run/qgis-label-styles.json"))
        self.assertEqual(paths.qgis_runtime_json, Path("/tmp/run/qgis-runtime.json"))
        self.assertEqual(paths.manifest_json, Path("/tmp/run/manifest.json"))

    def test_qgis_runtime_snapshot_records_version_metadata(self):
        fake_qgis = types.SimpleNamespace(
            QGIS_VERSION="3.44.0-Solothurn",
            QGIS_VERSION_INT=34400,
            QGIS_RELEASE_NAME="Solothurn",
        )

        self.assertEqual(
            qgis_runtime_snapshot(fake_qgis),
            {
                "qgis_version": "3.44.0-Solothurn",
                "qgis_version_int": 34400,
                "qgis_release_name": "Solothurn",
            },
        )

    def test_write_qgis_runtime_snapshot_writes_json_and_allows_noop(self):
        fake_qgis = types.SimpleNamespace(QGIS_VERSION="3.44.0-Solothurn")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "qgis-runtime.json"

            write_qgis_runtime_snapshot(qgis_api=fake_qgis, output_path=output_path)
            write_qgis_runtime_snapshot(qgis_api=fake_qgis, output_path=None)

            runtime = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(runtime["qgis_version"], "3.44.0-Solothurn")
        self.assertIsNone(runtime["qgis_version_int"])

    def test_write_qgis_core_runtime_snapshot_uses_loaded_qgis_module(self):
        fake_qgis = types.SimpleNamespace(
            QGIS_VERSION="3.44.0-Solothurn",
            QGIS_VERSION_INT=34400,
            QGIS_RELEASE_NAME="Solothurn",
        )
        fake_core = types.SimpleNamespace(Qgis=fake_qgis)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "qgis-runtime.json"
            with patch.dict(sys.modules, {"qgis.core": fake_core}):
                write_qgis_core_runtime_snapshot(output_path=output_path)
            runtime = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(runtime["qgis_release_name"], "Solothurn")

    def test_write_qgis_core_runtime_snapshot_ignores_write_failures(self):
        fake_qgis = types.SimpleNamespace(QGIS_VERSION="3.44.0-Solothurn")
        fake_core = types.SimpleNamespace(Qgis=fake_qgis)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(sys.modules, {"qgis.core": fake_core}), patch("sys.stderr") as stderr_mock:
                write_qgis_core_runtime_snapshot(output_path=Path(tmpdir))

        stderr_text = "".join(call.args[0] for call in stderr_mock.write.call_args_list)
        self.assertIn("QGIS runtime metadata was not written", stderr_text)

    def test_load_optional_qgis_runtime_snapshot_ignores_invalid_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            valid_path = root / "valid.json"
            invalid_path = root / "invalid.json"
            non_object_path = root / "non-object.json"
            missing_path = root / "missing.json"
            valid_path.write_text(json.dumps({"qgis_version": "3.44.0-Solothurn"}), encoding="utf-8")
            invalid_path.write_text("{", encoding="utf-8")
            non_object_path.write_text("[]", encoding="utf-8")

            self.assertEqual(
                _load_optional_qgis_runtime_snapshot(valid_path),
                {"qgis_version": "3.44.0-Solothurn"},
            )
            self.assertEqual(_load_optional_qgis_runtime_snapshot(invalid_path), {})
            self.assertEqual(_load_optional_qgis_runtime_snapshot(non_object_path), {})
            self.assertEqual(_load_optional_qgis_runtime_snapshot(missing_path), {})

    def test_append_enabled_qgis_contour_label_probes_routes_flags(self):
        layer = object()

        with patch(
            "qfit.validation.mapbox_outdoors_comparison._append_qgis_contour_polygon_label_probe"
        ) as polygon_probe, patch(
            "qfit.validation.mapbox_outdoors_comparison._append_qgis_contour_boundary_generator_label_probe"
        ) as boundary_probe, patch(
            "qfit.validation.mapbox_outdoors_comparison._append_qgis_contour_bbox_edge_difference_label_probe"
        ) as bbox_probe, patch(
            "qfit.validation.mapbox_outdoors_comparison."
            "_append_qgis_contour_bbox_edge_difference_source_style_label_probe"
        ) as source_probe, patch(
            "qfit.validation.mapbox_outdoors_comparison."
            "_append_qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe"
        ) as high_zoom_probe:
            _append_enabled_qgis_contour_label_probes(
                layer,
                qgis_contour_polygon_label_probe=True,
                qgis_contour_boundary_generator_label_probe=False,
                qgis_contour_bbox_edge_difference_label_probe=True,
                qgis_contour_bbox_edge_difference_source_style_label_probe=False,
                qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe=True,
            )

        polygon_probe.assert_called_once_with(layer)
        boundary_probe.assert_not_called()
        bbox_probe.assert_called_once_with(layer)
        source_probe.assert_not_called()
        high_zoom_probe.assert_called_once_with(layer)

    def test_resolve_token_prefers_argument_then_environment(self):
        self.assertEqual(
            resolve_mapbox_token(provided_token="arg-token", environ={"MAPBOX_ACCESS_TOKEN": "env-token"}),
            "arg-token",
        )
        self.assertEqual(
            resolve_mapbox_token(provided_token=None, environ={"MAPBOX_ACCESS_TOKEN": "env-token"}),
            "env-token",
        )
        self.assertEqual(
            resolve_mapbox_token(provided_token=None, environ={"QFIT_MAPBOX_ACCESS_TOKEN": "qfit-token"}),
            "qfit-token",
        )
        with self.assertRaises(ValueError):
            resolve_mapbox_token(provided_token=None, environ={})

    def test_camera_extent_is_web_mercator_bounds_around_center(self):
        camera = MapboxComparisonCamera(
            name="small",
            description="Small test camera",
            longitude=7.0,
            latitude=46.0,
            zoom=8.0,
            width=512,
            height=512,
        )

        center_x, center_y = camera_center_web_mercator(camera)
        xmin, ymin, xmax, ymax = camera_extent_web_mercator(camera)

        self.assertAlmostEqual((xmin + xmax) / 2, center_x)
        self.assertAlmostEqual((ymin + ymax) / 2, center_y)
        self.assertGreater(xmax - xmin, 0)
        self.assertGreater(ymax - ymin, 0)

    def test_build_mapbox_html_uses_camera_style_without_storing_token(self):
        camera = CAMERAS["valais-geneva-outdoors"]
        html = build_mapbox_gl_html(camera=camera)

        self.assertIn("mapbox://styles/mapbox/outdoors-v12", html)
        self.assertIn("startQfitMapboxComparison", html)
        self.assertIn("qfitMapboxReady", html)
        self.assertNotIn("test-mapbox-token", html)

    def test_build_mapbox_html_can_inline_downloaded_style_json(self):
        camera = CAMERAS["valais-geneva-outdoors"]
        html = build_mapbox_gl_html(camera=camera, style_definition=SAMPLE_STYLE)

        self.assertIn('"version": 8', html)
        self.assertIn('"mapbox://mapbox.mapbox-streets-v8"', html)
        self.assertNotIn("mapbox://styles/mapbox/outdoors-v12", html)
        self.assertNotIn("test-mapbox-token", html)

    def test_node_capture_script_uses_playwright_and_file_url_without_token(self):
        script = build_node_playwright_capture_script()

        self.assertIn("require('playwright')", script)
        self.assertIn("setContent", script)
        self.assertIn("startQfitMapboxComparison", script)
        self.assertIn("window.qfitMapboxReady", script)
        self.assertIn("JSON.parse", script)
        self.assertIn("readFileSync(0", script)
        self.assertNotIn("Buffer.from", script)
        self.assertNotIn("accessToken", script)
        self.assertNotIn("MAPBOX_ACCESS_TOKEN", script)
        self.assertNotIn("pk.", script)

    def test_encode_browser_capture_html_keeps_reference_page_token_free(self):
        encoded_html = encode_browser_capture_html(camera=CAMERAS["valais-geneva-outdoors"])
        html = base64.b64decode(encoded_html).decode("utf-8")

        self.assertIn("startQfitMapboxComparison", html)
        self.assertNotIn("test-mapbox-token", html)
        self.assertNotIn("accessToken", html)

    def test_render_browser_reference_passes_large_html_on_stdin_instead_of_argv(self):
        captured = {}
        large_style = {
            **SAMPLE_STYLE,
            "metadata": {"qfit-large-style-padding": "x" * 150_000},
        }

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["payload"] = json.loads(kwargs["input"])
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "reference.png"
            with patch("qfit.validation.mapbox_outdoors_comparison.shutil.which", return_value="/usr/bin/node"):
                with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
                    render_browser_reference(
                        camera=CAMERAS["valais-geneva-outdoors"],
                        token="test-mapbox-token",
                        output_path=output_path,
                        timeout_ms=5_000,
                        style_definition=large_style,
                    )

        self.assertEqual(captured["payload"]["credential"], "test-mapbox-token")
        self.assertLess(max(len(value) for value in captured["command"]), 1_000)
        self.assertIn("qfit-large-style-padding", captured["payload"]["html"])
        self.assertNotIn("test-mapbox-token", captured["payload"]["html"])

    def test_load_style_definition_requires_json_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "mapbox-outdoors.json"
            path.write_text(json.dumps(SAMPLE_STYLE), encoding="utf-8")

            self.assertEqual(load_style_definition(path), SAMPLE_STYLE)

            bad_path = Path(tmpdir) / "bad-style.json"
            bad_path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_style_definition(bad_path)

    def test_qgis_vector_tile_guard_rejects_raster_or_invalid_layers(self):
        class FakeVectorTileLayer:
            def __init__(self, valid=True):
                self._valid = valid

            def isValid(self):
                return self._valid

        class FakeRasterLayer:
            def isValid(self):
                return True

        self.assertTrue(
            is_valid_qgis_vector_tile_layer(
                layer=FakeVectorTileLayer(),
                vector_tile_layer_type=FakeVectorTileLayer,
            )
        )
        self.assertFalse(
            is_valid_qgis_vector_tile_layer(
                layer=FakeVectorTileLayer(valid=False),
                vector_tile_layer_type=FakeVectorTileLayer,
            )
        )
        self.assertFalse(
            is_valid_qgis_vector_tile_layer(
                layer=FakeRasterLayer(),
                vector_tile_layer_type=FakeVectorTileLayer,
            )
        )

    def test_render_qgis_vector_uses_isolated_vector_layer_without_project_clear(self):
        class FakeQgsApplication:
            @staticmethod
            def instance():
                return None

            def __init__(self, *_args):
                pass

            def initQgis(self):
                pass

            def exitQgis(self):
                pass

        class FakeQgsVectorTileLayer:
            def __init__(self, uri, name):
                self.uri = uri
                self.name = name

            def isValid(self):
                return True

        class FakeQgsMapSettings:
            def setLayers(self, layers):
                self.layers = layers

            def setDestinationCrs(self, crs):
                self.crs = crs

            def setExtent(self, extent):
                self.extent = extent

            def setOutputSize(self, size):
                self.size = size

            def setBackgroundColor(self, color):
                self.color = color

        class FakeRenderedImage:
            def isNull(self):
                return False

            def save(self, output_path, _format):
                Path(output_path).write_bytes(PNG_PLACEHOLDER)
                return True

        class FakeQgsMapRendererParallelJob:
            def __init__(self, settings):
                self.settings = settings

            def start(self):
                pass

            def waitForFinished(self):
                pass

            def renderedImage(self):
                return FakeRenderedImage()

        captured_style = {}

        class FakeBackgroundMapService:
            def _apply_mapbox_gl_style(self, layer, style_definition, *, sprite_resources=None):
                layer.applied_style = style_definition
                layer.sprite_resources = sprite_resources
                captured_style["style_definition"] = style_definition

        fake_core = types.ModuleType("qgis.core")
        fake_core.QgsApplication = FakeQgsApplication
        fake_core.QgsCoordinateReferenceSystem = lambda value: value
        fake_core.QgsMapRendererParallelJob = FakeQgsMapRendererParallelJob
        fake_core.QgsMapSettings = FakeQgsMapSettings
        fake_core.QgsRectangle = lambda *values: values
        fake_core.QgsVectorTileLayer = FakeQgsVectorTileLayer

        fake_qt_core = types.ModuleType("qgis.PyQt.QtCore")
        fake_qt_core.QSize = lambda width, height: (width, height)
        fake_qt_gui = types.ModuleType("qgis.PyQt.QtGui")
        fake_qt_gui.QColor = lambda *values: values

        fake_mapbox_config = types.ModuleType("qfit.mapbox_config")

        def fail_fetch_style_definition(*_args):
            raise AssertionError("style should be loaded from the provided style JSON")

        fake_mapbox_config.fetch_mapbox_style_definition = fail_fetch_style_definition
        fake_mapbox_config.simplify_mapbox_style_expressions = lambda style: {
            **style,
            "metadata": {"qfit-preprocessed": True, "token": "test-mapbox-token"},
        }
        fake_mapbox_config.extract_mapbox_vector_source_ids = lambda _style: ["mapbox.mapbox-streets-v8"]
        fake_mapbox_config.build_vector_tile_layer_uri = lambda *_args, **_kwargs: "vector://style"
        fake_mapbox_config.fetch_mapbox_sprite_resources = lambda *_args, **_kwargs: "sprite-resources"

        fake_background_service = types.ModuleType("qfit.visualization.infrastructure.background_map_service")
        fake_background_service.BackgroundMapService = FakeBackgroundMapService

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(os.environ, {}, clear=False), patch.dict(
            sys.modules,
            {
                "qgis": types.ModuleType("qgis"),
                "qgis.core": fake_core,
                "qgis.PyQt": types.ModuleType("qgis.PyQt"),
                "qgis.PyQt.QtCore": fake_qt_core,
                "qgis.PyQt.QtGui": fake_qt_gui,
                "qfit.mapbox_config": fake_mapbox_config,
                "qfit.visualization.infrastructure.background_map_service": fake_background_service,
            },
        ):
            output_path = Path(tmpdir) / "qgis-vector.png"
            preprocessed_style_path = Path(tmpdir) / "qgis-preprocessed-style.json"

            render_qgis_vector(
                camera=CAMERAS["valais-geneva-outdoors"],
                token="test-mapbox-token",
                output_path=output_path,
                style_definition=SAMPLE_STYLE,
                qgis_preprocessed_style_path=preprocessed_style_path,
            )

            self.assertEqual(output_path.read_bytes(), PNG_PLACEHOLDER)
            preprocessed_style_text = preprocessed_style_path.read_text(encoding="utf-8")
            preprocessed_style = json.loads(preprocessed_style_text)

            self.assertTrue(captured_style["style_definition"]["metadata"]["qfit-preprocessed"])
            self.assertTrue(preprocessed_style["metadata"]["qfit-preprocessed"])
            self.assertNotIn("test-mapbox-token", preprocessed_style_text)

    def test_append_qgis_contour_polygon_label_probe_adds_polygon_perimeter_style(self):
        class FakeQgis:
            class GeometryType:
                Polygon = "Polygon"

            class RenderUnit:
                Millimeters = "Millimeters"

        class FakePalLayerSettings:
            PerimeterCurved = "PerimeterCurved"
            constructed_sources = []

            def __init__(self, source=None):
                self.constructed_sources.append(source)
                self.fieldName = getattr(source, "fieldName", "")
                self.isExpression = getattr(source, "isExpression", False)
                self.priority = getattr(source, "priority", 0)
                self.repeatDistance = getattr(source, "repeatDistance", 0.0)
                self.repeatDistanceUnit = getattr(source, "repeatDistanceUnit", None)

        class FakeLabelingStyle:
            def setStyleName(self, value):
                self._style_name = value

            def styleName(self):
                return self._style_name

            def setLayerName(self, value):
                self._layer_name = value

            def layerName(self):
                return self._layer_name

            def setGeometryType(self, value):
                self._geometry_type = value

            def geometryType(self):
                return self._geometry_type

            def setFilterExpression(self, value):
                self._filter_expression = value

            def filterExpression(self):
                return self._filter_expression

            def setMinZoomLevel(self, value):
                self._min_zoom = value

            def minZoomLevel(self):
                return self._min_zoom

            def setLabelSettings(self, value):
                self._settings = value

            def labelSettings(self):
                return self._settings

        class FakeLabeling:
            def __init__(self, styles=None):
                self._styles = list(styles or [])

            def styles(self):
                return self._styles

            def setStyles(self, styles):
                self._styles = list(styles)

        class FakeLayer:
            def __init__(self, labeling):
                self._labeling = labeling
                self.labels_enabled = False

            def labeling(self):
                return self._labeling

            def setLabeling(self, labeling):
                self._labeling = labeling

            def setLabelsEnabled(self, value):
                self.labels_enabled = value

        source_settings = FakePalLayerSettings()
        source_settings.priority = 7
        source_style = FakeLabelingStyle()
        source_style.setStyleName("contour-label")
        source_style.setLayerName("contour")
        source_style.setLabelSettings(source_settings)
        layer = FakeLayer(FakeLabeling([source_style]))
        FakePalLayerSettings.constructed_sources = []

        fake_core = types.ModuleType("qgis.core")
        fake_core.Qgis = FakeQgis
        fake_core.QgsPalLayerSettings = FakePalLayerSettings
        fake_core.QgsVectorTileBasicLabeling = FakeLabeling
        fake_core.QgsVectorTileBasicLabelingStyle = FakeLabelingStyle

        with patch.dict(sys.modules, {"qgis": types.ModuleType("qgis"), "qgis.core": fake_core}):
            _append_qgis_contour_polygon_label_probe(layer)
            _append_qgis_contour_polygon_label_probe(layer)

        self.assertEqual(FakePalLayerSettings.constructed_sources, [None])
        styles = layer.labeling().styles()
        self.assertEqual(len(styles), 2)
        self.assertTrue(layer.labels_enabled)
        probe_style = styles[1]
        probe_settings = probe_style.labelSettings()
        self.assertEqual(probe_style.styleName(), QGIS_CONTOUR_POLYGON_LABEL_PROBE_STYLE_NAME)
        self.assertEqual(probe_style.layerName(), "contour")
        self.assertEqual(probe_style.geometryType(), FakeQgis.GeometryType.Polygon)
        self.assertEqual(probe_style.filterExpression(), QGIS_CONTOUR_POLYGON_LABEL_PROBE_FILTER)
        self.assertEqual(probe_style.minZoomLevel(), QGIS_CONTOUR_POLYGON_LABEL_PROBE_MIN_ZOOM)
        self.assertEqual(probe_settings.fieldName, 'concat("ele", \' m\')')
        self.assertTrue(probe_settings.isExpression)
        self.assertEqual(probe_settings.placement, FakePalLayerSettings.PerimeterCurved)
        self.assertEqual(probe_settings.priority, 7)
        self.assertEqual(probe_settings.repeatDistance, 0.0)
        self.assertIsNone(probe_settings.repeatDistanceUnit)

    def test_append_qgis_contour_line_generator_label_probes_add_line_generator_style(self):
        class FakeQgis:
            class GeometryType:
                Line = "Line"
                Polygon = "Polygon"

        class FakePalLayerSettings:
            Curved = "Curved"
            Line = "Line"
            constructed_sources = []

            def __init__(self, source=None):
                self.constructed_sources.append(source)
                self.fieldName = getattr(source, "fieldName", "")
                self.isExpression = getattr(source, "isExpression", False)
                self.priority = getattr(source, "priority", 0)
                self.sourceMarker = getattr(source, "sourceMarker", "fresh")
                self.placement = getattr(source, "placement", None)
                self.geometryGenerator = ""
                self.geometryGeneratorEnabled = False
                self.geometryGeneratorType = None

        class FakeLabelingStyle:
            def setStyleName(self, value):
                self._style_name = value

            def styleName(self):
                return self._style_name

            def setLayerName(self, value):
                self._layer_name = value

            def layerName(self):
                return self._layer_name

            def setGeometryType(self, value):
                self._geometry_type = value

            def geometryType(self):
                return self._geometry_type

            def setFilterExpression(self, value):
                self._filter_expression = value

            def filterExpression(self):
                return self._filter_expression

            def setMinZoomLevel(self, value):
                self._min_zoom = value

            def minZoomLevel(self):
                return self._min_zoom

            def setLabelSettings(self, value):
                self._settings = value

            def labelSettings(self):
                return self._settings

        class FakeLabeling:
            def __init__(self, styles=None):
                self._styles = list(styles or [])

            def styles(self):
                return self._styles

            def setStyles(self, styles):
                self._styles = list(styles)

        class FakeLayer:
            def __init__(self, labeling):
                self._labeling = labeling
                self.labels_enabled = False

            def labeling(self):
                return self._labeling

            def setLabeling(self, labeling):
                self._labeling = labeling

            def setLabelsEnabled(self, value):
                self.labels_enabled = value

        fake_core = types.ModuleType("qgis.core")
        fake_core.Qgis = FakeQgis
        fake_core.QgsPalLayerSettings = FakePalLayerSettings
        fake_core.QgsVectorTileBasicLabeling = FakeLabeling
        fake_core.QgsVectorTileBasicLabelingStyle = FakeLabelingStyle
        cases = [
            (
                _append_qgis_contour_boundary_generator_label_probe,
                QGIS_CONTOUR_BOUNDARY_GENERATOR_LABEL_PROBE_STYLE_NAME,
                QGIS_CONTOUR_BOUNDARY_GENERATOR_LABEL_PROBE_FILTER,
                QGIS_CONTOUR_BOUNDARY_GENERATOR_LABEL_PROBE_MIN_ZOOM,
                QGIS_CONTOUR_BOUNDARY_GENERATOR_LABEL_PROBE_EXPRESSION,
                False,
            ),
            (
                _append_qgis_contour_bbox_edge_difference_label_probe,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_STYLE_NAME,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_FILTER,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_MIN_ZOOM,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_EXPRESSION,
                False,
            ),
            (
                _append_qgis_contour_bbox_edge_difference_source_style_label_probe,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_STYLE_NAME,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_FILTER,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_MIN_ZOOM,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_EXPRESSION,
                True,
            ),
            (
                _append_qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_HIGH_ZOOM_LABEL_PROBE_STYLE_NAME,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_HIGH_ZOOM_LABEL_PROBE_FILTER,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_HIGH_ZOOM_LABEL_PROBE_MIN_ZOOM,
                QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_HIGH_ZOOM_LABEL_PROBE_EXPRESSION,
                True,
            ),
        ]

        for append_probe, style_name, filter_expression, min_zoom, geometry_generator, copies_source in cases:
            with self.subTest(style_name=style_name):
                source_settings = FakePalLayerSettings()
                source_settings.priority = 6
                source_settings.fieldName = '"ele"'
                source_settings.isExpression = False
                source_settings.placement = "SourceCurved"
                source_settings.sourceMarker = "copied"
                source_style = FakeLabelingStyle()
                source_style.setStyleName("contour-label")
                source_style.setLayerName("contour")
                source_style.setLabelSettings(source_settings)
                layer = FakeLayer(FakeLabeling([source_style]))
                FakePalLayerSettings.constructed_sources = []

                with patch.dict(sys.modules, {"qgis": types.ModuleType("qgis"), "qgis.core": fake_core}):
                    append_probe(layer)
                    append_probe(layer)

                self.assertEqual(
                    FakePalLayerSettings.constructed_sources,
                    [source_settings] if copies_source else [None],
                )
                styles = layer.labeling().styles()
                self.assertEqual(len(styles), 2)
                self.assertTrue(layer.labels_enabled)
                probe_style = styles[1]
                probe_settings = probe_style.labelSettings()
                self.assertEqual(probe_style.styleName(), style_name)
                self.assertEqual(probe_style.layerName(), "contour")
                self.assertEqual(probe_style.geometryType(), FakeQgis.GeometryType.Polygon)
                self.assertEqual(probe_style.filterExpression(), filter_expression)
                self.assertEqual(probe_style.minZoomLevel(), min_zoom)
                self.assertEqual(probe_settings.fieldName, 'concat("ele", \' m\')')
                self.assertTrue(probe_settings.isExpression)
                self.assertEqual(probe_settings.placement, FakePalLayerSettings.Curved)
                self.assertEqual(probe_settings.priority, 6)
                self.assertEqual(probe_settings.sourceMarker, "copied" if copies_source else "fresh")
                self.assertEqual(probe_settings.geometryGenerator, geometry_generator)
                self.assertTrue(probe_settings.geometryGeneratorEnabled)
                self.assertEqual(probe_settings.geometryGeneratorType, FakeQgis.GeometryType.Line)

    def test_append_qgis_contour_bbox_edge_difference_source_style_probe_copies_source_settings(self):
        class FakeQgis:
            class GeometryType:
                Line = "Line"
                Polygon = "Polygon"

        class FakePalLayerSettings:
            Curved = "Curved"
            Line = "Line"
            constructed_sources = []

            def __init__(self, source=None):
                self.constructed_sources.append(source)
                self.fieldName = getattr(source, "fieldName", "")
                self.isExpression = getattr(source, "isExpression", False)
                self.priority = getattr(source, "priority", 0)
                self.placement = getattr(source, "placement", None)
                self.repeatDistance = getattr(source, "repeatDistance", 0.0)
                self.geometryGenerator = getattr(source, "geometryGenerator", "")
                self.geometryGeneratorEnabled = getattr(source, "geometryGeneratorEnabled", False)
                self.geometryGeneratorType = getattr(source, "geometryGeneratorType", None)

        class FakeLabelingStyle:
            def setStyleName(self, value):
                self._style_name = value

            def styleName(self):
                return self._style_name

            def setLayerName(self, value):
                self._layer_name = value

            def layerName(self):
                return self._layer_name

            def setGeometryType(self, value):
                self._geometry_type = value

            def geometryType(self):
                return self._geometry_type

            def setFilterExpression(self, value):
                self._filter_expression = value

            def filterExpression(self):
                return self._filter_expression

            def setMinZoomLevel(self, value):
                self._min_zoom = value

            def minZoomLevel(self):
                return self._min_zoom

            def setLabelSettings(self, value):
                self._settings = value

            def labelSettings(self):
                return self._settings

        class FakeLabeling:
            def __init__(self, styles=None):
                self._styles = list(styles or [])

            def styles(self):
                return self._styles

            def setStyles(self, styles):
                self._styles = list(styles)

        class FakeLayer:
            def __init__(self, labeling):
                self._labeling = labeling
                self.labels_enabled = False

            def labeling(self):
                return self._labeling

            def setLabeling(self, labeling):
                self._labeling = labeling

            def setLabelsEnabled(self, value):
                self.labels_enabled = value

        source_settings = FakePalLayerSettings()
        source_settings.fieldName = '"ele"'
        source_settings.isExpression = False
        source_settings.priority = 2
        source_settings.placement = FakePalLayerSettings.Curved
        source_settings.repeatDistance = 66.1458333333
        source_style = FakeLabelingStyle()
        source_style.setStyleName("contour-label")
        source_style.setLayerName("contour")
        source_style.setLabelSettings(source_settings)
        layer = FakeLayer(FakeLabeling([source_style]))
        FakePalLayerSettings.constructed_sources = []

        fake_core = types.ModuleType("qgis.core")
        fake_core.Qgis = FakeQgis
        fake_core.QgsPalLayerSettings = FakePalLayerSettings
        fake_core.QgsVectorTileBasicLabeling = FakeLabeling
        fake_core.QgsVectorTileBasicLabelingStyle = FakeLabelingStyle

        with patch.dict(sys.modules, {"qgis": types.ModuleType("qgis"), "qgis.core": fake_core}):
            _append_qgis_contour_bbox_edge_difference_source_style_label_probe(layer)
            _append_qgis_contour_bbox_edge_difference_source_style_label_probe(layer)

        self.assertEqual(FakePalLayerSettings.constructed_sources, [source_settings])
        styles = layer.labeling().styles()
        self.assertEqual(len(styles), 2)
        self.assertTrue(layer.labels_enabled)
        probe_style = styles[1]
        probe_settings = probe_style.labelSettings()
        self.assertEqual(probe_style.styleName(), QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_STYLE_NAME)
        self.assertEqual(probe_style.layerName(), "contour")
        self.assertEqual(probe_style.geometryType(), FakeQgis.GeometryType.Polygon)
        self.assertEqual(
            probe_style.filterExpression(),
            QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_FILTER,
        )
        self.assertEqual(probe_style.minZoomLevel(), QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_MIN_ZOOM)
        self.assertEqual(probe_settings.fieldName, 'concat("ele", \' m\')')
        self.assertTrue(probe_settings.isExpression)
        self.assertEqual(probe_settings.placement, FakePalLayerSettings.Curved)
        self.assertEqual(probe_settings.priority, 3)
        self.assertAlmostEqual(probe_settings.repeatDistance, 66.1458333333)
        self.assertEqual(
            probe_settings.geometryGenerator,
            QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_SOURCE_STYLE_LABEL_PROBE_EXPRESSION,
        )
        self.assertTrue(probe_settings.geometryGeneratorEnabled)
        self.assertEqual(probe_settings.geometryGeneratorType, FakeQgis.GeometryType.Line)

    def test_qgis_label_styles_snapshot_captures_probe_settings(self):
        class FakeColor:
            def __init__(self, name):
                self._name = name

            def name(self):
                return self._name

        class FakeTextBuffer:
            def enabled(self):
                return True

            def size(self):
                return 0.5291666667

            def sizeUnit(self):
                return "Millimeters"

            def color(self):
                return FakeColor("#dcdcd4")

            def opacity(self):
                return 0.75

        class FakeTextFormat:
            def size(self):
                return 2.5135416667

            def sizeUnit(self):
                return "Millimeters"

            def color(self):
                return FakeColor("#626250")

            def opacity(self):
                return 0.9

            def buffer(self):
                return FakeTextBuffer()

        class FakeSettings:
            fieldName = 'concat("ele", \' m\')'
            isExpression = True
            placement = "Curved"
            priority = 6
            repeatDistance = 0.0
            repeatDistanceUnit = None
            labelPerPart = False
            mergeLines = True
            geometryGenerator = QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_EXPRESSION
            geometryGeneratorEnabled = True
            geometryGeneratorType = "Line"

            def format(self):
                return FakeTextFormat()

        class FakeStyle:
            def styleName(self):
                return QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_STYLE_NAME

            def layerName(self):
                return "contour"

            def geometryType(self):
                return "Polygon"

            def filterExpression(self):
                return QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_FILTER

            def minZoomLevel(self):
                return QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_MIN_ZOOM

            def maxZoomLevel(self):
                return None

            def labelSettings(self):
                return FakeSettings()

        class FakeLabeling:
            def styles(self):
                return [FakeStyle()]

        class FakeLayer:
            def labeling(self):
                return FakeLabeling()

        snapshot = qgis_label_styles_snapshot(FakeLayer())

        self.assertEqual(snapshot, [{
            "style_name": QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_STYLE_NAME,
            "layer_name": "contour",
            "geometry_type": "Polygon",
            "filter_expression": QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_FILTER,
            "min_zoom_level": QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_MIN_ZOOM,
            "max_zoom_level": None,
            "label_settings": {
                "field_name": 'concat("ele", \' m\')',
                "is_expression": True,
                "placement": "Curved",
                "priority": 6,
                "repeat_distance": 0.0,
                "repeat_distance_unit": None,
                "label_per_part": False,
                "merge_lines": True,
                "geometry_generator": QGIS_CONTOUR_BBOX_EDGE_DIFFERENCE_LABEL_PROBE_EXPRESSION,
                "geometry_generator_enabled": True,
                "geometry_generator_type": "Line",
                "text_size": 2.5135416667,
                "text_size_unit": "Millimeters",
                "text_color": "#626250",
                "text_opacity": 0.9,
                "buffer_enabled": True,
                "buffer_size": 0.5291666667,
                "buffer_size_unit": "Millimeters",
                "buffer_color": "#dcdcd4",
                "buffer_opacity": 0.75,
            },
        }])

    def test_qgis_label_styles_snapshot_captures_label_thinning_settings(self):
        class FakeThinningSettings:
            def limitNumberOfLabelsEnabled(self):
                return False

            def maximumNumberLabels(self):
                return 0

            def minimumFeatureSize(self):
                return 1.25

            def allowDuplicateRemoval(self):
                return True

            def minimumDistanceToDuplicate(self):
                return 20.0

            def minimumDistanceToDuplicateUnit(self):
                return "Millimeters"

            def labelMarginDistance(self):
                return 1.5

            def labelMarginDistanceUnit(self):
                return "Millimeters"

        class FakeSettings:
            def thinningSettings(self):
                return FakeThinningSettings()

        class FakeStyle:
            def styleName(self):
                return "path-pedestrian-label"

            def layerName(self):
                return "road"

            def labelSettings(self):
                return FakeSettings()

        class FakeLabeling:
            def styles(self):
                return [FakeStyle()]

        class FakeLayer:
            def labeling(self):
                return FakeLabeling()

        [snapshot] = qgis_label_styles_snapshot(FakeLayer())

        self.assertEqual(
            snapshot["label_settings"]["thinning_settings"],
            {
                "limit_number_of_labels_enabled": False,
                "maximum_number_labels": 0,
                "minimum_feature_size": 1.25,
                "allow_duplicate_removal": True,
                "minimum_distance_to_duplicate": 20.0,
                "minimum_distance_to_duplicate_unit": "Millimeters",
                "label_margin_distance": 1.5,
                "label_margin_distance_unit": "Millimeters",
            },
        )

    def test_qgis_label_styles_snapshot_keeps_missing_thinning_methods_as_none(self):
        class FakePartialThinningSettings:
            def limitNumberOfLabelsEnabled(self):
                return False

            def maximumNumberLabels(self):
                return 0

            def minimumFeatureSize(self):
                return 1.25

        class FakeSettings:
            def thinningSettings(self):
                return FakePartialThinningSettings()

        class FakeStyle:
            def styleName(self):
                return "path-pedestrian-label"

            def layerName(self):
                return "road"

            def labelSettings(self):
                return FakeSettings()

        class FakeLabeling:
            def styles(self):
                return [FakeStyle()]

        class FakeLayer:
            def labeling(self):
                return FakeLabeling()

        [snapshot] = qgis_label_styles_snapshot(FakeLayer())

        self.assertEqual(
            snapshot["label_settings"]["thinning_settings"],
            {
                "limit_number_of_labels_enabled": False,
                "maximum_number_labels": 0,
                "minimum_feature_size": 1.25,
                "allow_duplicate_removal": None,
                "minimum_distance_to_duplicate": None,
                "minimum_distance_to_duplicate_unit": None,
                "label_margin_distance": None,
                "label_margin_distance_unit": None,
            },
        )

    def test_label_value_normalizes_named_sequence_and_opaque_values(self):
        class NamedValue:
            name = "Line"

        class OpaqueValue:
            def __str__(self):
                return "opaque-value"

        self.assertEqual(
            _label_value([NamedValue(), OpaqueValue(), None]),
            ["Line", "opaque-value", None],
        )

    def test_label_setting_value_handles_missing_and_runtime_errors(self):
        class RuntimeErrorSettings:
            @property
            def priority(self):
                raise RuntimeError("unavailable")

        class ValueErrorSettings:
            @property
            def placement(self):
                raise ValueError("unavailable")

        self.assertIsNone(_label_setting_value(None, "fieldName"))
        self.assertIsNone(_label_setting_value(object(), "fieldName"))
        self.assertIsNone(_label_setting_value(RuntimeErrorSettings(), "priority"))
        self.assertIsNone(_label_setting_value(ValueErrorSettings(), "placement"))

    def test_write_qgis_label_styles_snapshot_redacts_token(self):
        class FakeSettings:
            fieldName = "mapbox-token-secret"

        class FakeStyle:
            def styleName(self):
                return "sensitive-label"

            def labelSettings(self):
                return FakeSettings()

        class FakeLabeling:
            def styles(self):
                return [FakeStyle()]

        class FakeLayer:
            def labeling(self):
                return FakeLabeling()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "qgis-label-styles.json"

            write_qgis_label_styles_snapshot(
                layer=FakeLayer(),
                output_path=output_path,
                token="mapbox-token-secret",
            )

            snapshot_text = output_path.read_text(encoding="utf-8")

        self.assertIn("<redacted>", snapshot_text)
        self.assertNotIn("mapbox-token-secret", snapshot_text)

    def test_headless_qt_platform_defaults_to_offscreen_without_overriding_callers(self):
        from qfit.validation import mapbox_outdoors_comparison

        empty_env = {}
        mapbox_outdoors_comparison._ensure_headless_qt_platform(empty_env)

        custom_env = {"QT_QPA_PLATFORM": "minimal"}
        mapbox_outdoors_comparison._ensure_headless_qt_platform(custom_env)

        self.assertEqual(empty_env["QT_QPA_PLATFORM"], "offscreen")
        self.assertEqual(custom_env["QT_QPA_PLATFORM"], "minimal")

    def test_all_camera_child_environment_defaults_qgis_to_offscreen(self):
        from qfit.validation import mapbox_outdoors_comparison

        with patch.dict(os.environ, {}, clear=True):
            env = mapbox_outdoors_comparison._single_camera_subprocess_environment(token="test-mapbox-token")

        self.assertEqual(env["MAPBOX_ACCESS_TOKEN"], "test-mapbox-token")
        self.assertEqual(env["QT_QPA_PLATFORM"], "offscreen")

    def test_all_camera_child_environment_preserves_qt_platform_override(self):
        from qfit.validation import mapbox_outdoors_comparison

        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "minimal"}, clear=True):
            env = mapbox_outdoors_comparison._single_camera_subprocess_environment(token="test-mapbox-token")

        self.assertEqual(env["QT_QPA_PLATFORM"], "minimal")

    def test_build_image_diff_writes_enhanced_diff_and_metrics_for_same_size_images(self):
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - local dependency guard
            self.skipTest("Pillow is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reference_path = root / "reference.png"
            candidate_path = root / "candidate.png"
            diff_path = root / "diff.png"
            Image.new("RGBA", (2, 1), (0, 0, 0, 255)).save(reference_path)
            Image.new("RGBA", (2, 1), (0, 0, 0, 255)).save(candidate_path)
            candidate = Image.open(candidate_path)
            candidate.putpixel((1, 0), (1, 0, 0, 255))
            candidate.save(candidate_path)
            candidate.close()

            metrics = build_image_diff(
                reference_path=reference_path,
                candidate_path=candidate_path,
                output_path=diff_path,
            )

            self.assertTrue(diff_path.exists())
            with Image.open(diff_path).convert("RGB") as diff_image:
                self.assertEqual(diff_image.getpixel((1, 0)), (8, 0, 0))
            self.assertEqual(metrics["pixel_count"], 2)
            self.assertEqual(metrics["changed_pixel_count"], 1)
            self.assertEqual(metrics["changed_pixel_ratio"], 0.5)
            self.assertIn("ssim_status", metrics)

    def test_build_all_cameras_contact_sheet_writes_preview_grid(self):
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - local dependency guard
            self.skipTest("Pillow is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            browser_path = root / "mapbox-gl-reference.png"
            qgis_path = root / "qgis-vector-render.png"
            diff_path = root / "mapbox-gl-vs-qgis-diff.png"
            Image.new("RGB", (16, 9), (255, 255, 255)).save(browser_path)
            Image.new("RGB", (16, 9), (128, 128, 128)).save(qgis_path)
            Image.new("RGB", (16, 9), (255, 0, 255)).save(diff_path)
            output_path = root / "contact-sheet.jpg"

            result = build_all_cameras_contact_sheet(
                entries=[
                    {
                        "camera": "test-camera",
                        "outputs": {
                            "browser_reference": str(browser_path),
                            "qgis_vector_render": str(qgis_path),
                            "diff": str(diff_path),
                        },
                    }
                ],
                output_path=output_path,
            )

            self.assertEqual(result, output_path)
            self.assertTrue(output_path.exists())
            expected_width = (
                len(CONTACT_SHEET_COLUMNS) * CONTACT_SHEET_THUMBNAIL_WIDTH
                + (len(CONTACT_SHEET_COLUMNS) - 1) * CONTACT_SHEET_COLUMN_GAP
            )
            with Image.open(output_path) as contact_sheet:
                self.assertEqual(contact_sheet.width, expected_width)
                self.assertGreater(contact_sheet.height, 180)

    def test_build_all_cameras_contact_sheet_uses_optional_pillow_api(self):
        fallback_lanczos = object()
        default_font = object()
        drawn_fonts = []
        resize_filters = []

        class FakeImage:
            def __init__(self, width, height):
                self.width = width
                self.height = height

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def close(self):
                pass

            def convert(self, _mode):
                return FakeImage(self.width, self.height)

            def paste(self, _image, _position):
                pass

            def resize(self, size, _resampling):
                resize_filters.append(_resampling)
                return FakeImage(*size)

            def save(self, output_path, **_kwargs):
                Path(output_path).write_bytes(PNG_PLACEHOLDER)

        fake_pil = types.ModuleType("PIL")
        fake_image = types.ModuleType("PIL.Image")
        fake_image.LANCZOS = fallback_lanczos
        fake_image.new = lambda _mode, size, _color: FakeImage(*size)

        def fake_open(path):
            if "missing" in str(path):
                raise OSError("missing image")
            return FakeImage(16, 9)

        fake_image.open = fake_open
        fake_draw = types.ModuleType("PIL.ImageDraw")
        fake_draw.Draw = lambda _image: types.SimpleNamespace(
            text=lambda *_args, **kwargs: drawn_fonts.append(kwargs.get("font"))
        )
        fake_font = types.ModuleType("PIL.ImageFont")

        def fake_truetype(*_args, **_kwargs):
            raise ImportError("_imagingft")

        fake_font.truetype = fake_truetype
        fake_font.load_default = lambda: default_font
        fake_pil.Image = fake_image
        fake_pil.ImageDraw = fake_draw
        fake_pil.ImageFont = fake_font

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "contact-sheet.jpg"
            with patch.dict(
                sys.modules,
                {
                    "PIL": fake_pil,
                    "PIL.Image": fake_image,
                    "PIL.ImageDraw": fake_draw,
                    "PIL.ImageFont": fake_font,
                },
            ):
                result = build_all_cameras_contact_sheet(
                    entries=[
                        {
                            "camera": "test-camera",
                            "outputs": {
                                "browser_reference": "loaded.png",
                                "qgis_vector_render": "missing.png",
                            },
                        },
                        {"camera": "empty-camera"},
                    ],
                    output_path=output_path,
                )

            self.assertEqual(result, output_path)
            self.assertEqual(output_path.read_bytes(), PNG_PLACEHOLDER)
            self.assertIn(default_font, drawn_fonts)
            self.assertIn(fallback_lanczos, resize_filters)

    def test_build_all_cameras_contact_sheet_skips_placeholder_only_sheet(self):
        try:
            from PIL import Image as _Image  # noqa: F401
        except ImportError:  # pragma: no cover - local dependency guard
            self.skipTest("Pillow is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "contact-sheet.jpg"

            result = build_all_cameras_contact_sheet(
                entries=[
                    {
                        "camera": "missing-camera",
                        "outputs": {"browser_reference": str(Path(tmpdir) / "missing.png")},
                    }
                ],
                output_path=output_path,
            )

            self.assertIsNone(result)
            self.assertFalse(output_path.exists())

    def test_run_comparison_writes_manifest_without_token(self):
        def fake_browser_renderer(*, output_path, **_kwargs):
            output_path.write_bytes(PNG_PLACEHOLDER)

        def fake_qgis_renderer(
            *,
            output_path,
            qgis_preprocessed_style_path,
            qgis_label_styles_path,
            qgis_runtime_path,
            **_kwargs,
        ):
            output_path.write_bytes(PNG_PLACEHOLDER)
            qgis_preprocessed_style_path.write_text(json.dumps(SAMPLE_STYLE), encoding="utf-8")
            qgis_label_styles_path.write_text(json.dumps([{"style_name": "contour-label"}]), encoding="utf-8")
            qgis_runtime_path.write_text(
                json.dumps(
                    {
                        "qgis_version": "3.44.0-Solothurn",
                        "qgis_version_int": 34400,
                        "qgis_release_name": "Solothurn",
                    }
                ),
                encoding="utf-8",
            )

        def fake_diff_builder(*, output_path, **_kwargs):
            output_path.write_bytes(PNG_PLACEHOLDER)
            return {"changed_pixel_ratio": 0.25, "ssim_status": "unavailable"}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_comparison(
                ComparisonConfig(
                    camera=CAMERAS["valais-geneva-outdoors"],
                    token="test-mapbox-token",
                    output_root=Path(tmpdir),
                    now=dt.datetime(2026, 5, 10, 19, 45, tzinfo=dt.timezone.utc),
                ),
                browser_renderer=fake_browser_renderer,
                qgis_renderer=fake_qgis_renderer,
                diff_builder=fake_diff_builder,
            )

            manifest_text = result.paths.manifest_json.read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            metrics = json.loads(result.paths.metrics_json.read_text(encoding="utf-8"))
            preprocessed_style = json.loads(result.paths.qgis_preprocessed_style_json.read_text(encoding="utf-8"))
            label_styles = json.loads(result.paths.qgis_label_styles_json.read_text(encoding="utf-8"))
            qgis_runtime = json.loads(result.paths.qgis_runtime_json.read_text(encoding="utf-8"))

        self.assertTrue(result.browser_captured)
        self.assertTrue(result.qgis_captured)
        self.assertTrue(result.diff_captured)
        self.assertTrue(result.qgis_preprocessed_style_captured)
        self.assertTrue(result.qgis_label_styles_captured)
        self.assertTrue(result.qgis_runtime_captured)
        self.assertNotIn("test-mapbox-token", manifest_text)
        self.assertEqual(manifest["camera"]["name"], "valais-geneva-outdoors")
        self.assertEqual(manifest["style_url"], "mapbox://styles/mapbox/outdoors-v12")
        self.assertTrue(manifest["captured"]["browser_reference"])
        self.assertTrue(manifest["captured"]["qgis_vector_render"])
        self.assertTrue(manifest["captured"]["diff"])
        self.assertTrue(manifest["captured"]["qgis_preprocessed_style"])
        self.assertTrue(manifest["captured"]["qgis_label_styles"])
        self.assertTrue(manifest["captured"]["qgis_runtime"])
        self.assertTrue(manifest["outputs"]["qgis_preprocessed_style"].endswith("qgis-preprocessed-style.json"))
        self.assertTrue(manifest["outputs"]["qgis_label_styles"].endswith("qgis-label-styles.json"))
        self.assertTrue(manifest["outputs"]["qgis_runtime"].endswith("qgis-runtime.json"))
        self.assertEqual(manifest["metrics"]["changed_pixel_ratio"], 0.25)
        self.assertEqual(manifest["qgis_runtime"]["qgis_version"], "3.44.0-Solothurn")
        self.assertEqual(metrics["changed_pixel_ratio"], 0.25)
        self.assertEqual(preprocessed_style, SAMPLE_STYLE)
        self.assertEqual(label_styles, [{"style_name": "contour-label"}])
        self.assertEqual(qgis_runtime["qgis_version_int"], 34400)

    def test_run_comparison_records_qgis_contour_label_probe_options(self):
        captured = {}

        def fake_qgis_renderer(
            *,
            output_path,
            qgis_contour_polygon_label_probe,
            qgis_contour_boundary_generator_label_probe,
            qgis_contour_bbox_edge_difference_label_probe,
            qgis_contour_bbox_edge_difference_source_style_label_probe,
            qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe,
            **_kwargs,
        ):
            captured["polygon_probe"] = qgis_contour_polygon_label_probe
            captured["boundary_generator_probe"] = qgis_contour_boundary_generator_label_probe
            captured["bbox_edge_difference_probe"] = qgis_contour_bbox_edge_difference_label_probe
            captured["bbox_edge_difference_source_style_probe"] = (
                qgis_contour_bbox_edge_difference_source_style_label_probe
            )
            captured["bbox_edge_difference_source_style_high_zoom_probe"] = (
                qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe
            )
            output_path.write_bytes(PNG_PLACEHOLDER)

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_comparison(
                ComparisonConfig(
                    camera=CAMERAS["zermatt-piste-z17-outdoors"],
                    token="test-mapbox-token",
                    output_root=Path(tmpdir),
                    qgis_contour_polygon_label_probe=True,
                    qgis_contour_boundary_generator_label_probe=True,
                    qgis_contour_bbox_edge_difference_label_probe=True,
                    qgis_contour_bbox_edge_difference_source_style_label_probe=True,
                    qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe=True,
                    browser=False,
                    diff=False,
                    now=dt.datetime(2026, 5, 10, 19, 45, tzinfo=dt.timezone.utc),
                ),
                qgis_renderer=fake_qgis_renderer,
            )

            manifest = json.loads(result.paths.manifest_json.read_text(encoding="utf-8"))

        self.assertTrue(captured["polygon_probe"])
        self.assertTrue(captured["boundary_generator_probe"])
        self.assertTrue(captured["bbox_edge_difference_probe"])
        self.assertTrue(captured["bbox_edge_difference_source_style_probe"])
        self.assertTrue(captured["bbox_edge_difference_source_style_high_zoom_probe"])
        self.assertTrue(result.qgis_contour_polygon_label_probe)
        self.assertTrue(result.qgis_contour_boundary_generator_label_probe)
        self.assertTrue(result.qgis_contour_bbox_edge_difference_label_probe)
        self.assertTrue(result.qgis_contour_bbox_edge_difference_source_style_label_probe)
        self.assertTrue(result.qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe)
        self.assertTrue(manifest["qgis_contour_polygon_label_probe"])
        self.assertTrue(manifest["qgis_contour_boundary_generator_label_probe"])
        self.assertTrue(manifest["qgis_contour_bbox_edge_difference_label_probe"])
        self.assertTrue(manifest["qgis_contour_bbox_edge_difference_source_style_label_probe"])
        self.assertTrue(manifest["qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe"])

    def test_run_comparison_passes_downloaded_style_json_to_renderers(self):
        captured_style_definitions = []

        def fake_browser_renderer(*, output_path, style_definition, **_kwargs):
            captured_style_definitions.append(("browser", style_definition))
            output_path.write_bytes(PNG_PLACEHOLDER)

        def fake_qgis_renderer(*, output_path, style_definition, **_kwargs):
            captured_style_definitions.append(("qgis", style_definition))
            output_path.write_bytes(PNG_PLACEHOLDER)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            style_path = root / "mapbox-outdoors-v12.json"
            style_path.write_text(json.dumps(SAMPLE_STYLE), encoding="utf-8")

            result = run_comparison(
                ComparisonConfig(
                    camera=CAMERAS["valais-geneva-outdoors"],
                    token="test-mapbox-token",
                    output_root=root,
                    style_json_path=style_path,
                    diff=False,
                    now=dt.datetime(2026, 5, 10, 19, 45, tzinfo=dt.timezone.utc),
                ),
                browser_renderer=fake_browser_renderer,
                qgis_renderer=fake_qgis_renderer,
            )

            manifest = json.loads(result.paths.manifest_json.read_text(encoding="utf-8"))

        self.assertEqual(
            captured_style_definitions,
            [("browser", SAMPLE_STYLE), ("qgis", SAMPLE_STYLE)],
        )
        self.assertEqual(result.style_json_path, str(style_path))
        self.assertEqual(manifest["style_json_path"], str(style_path))
        self.assertIsNone(manifest["style_url"])

    def test_run_comparison_skips_diff_when_one_capture_is_disabled(self):
        def fake_qgis_renderer(*, output_path, **_kwargs):
            output_path.write_bytes(PNG_PLACEHOLDER)

        diff_called = False

        def fake_diff_builder(**_kwargs):
            nonlocal diff_called
            diff_called = True

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_comparison(
                ComparisonConfig(
                    camera=CAMERAS["valais-geneva-outdoors"],
                    token="test-mapbox-token",
                    output_root=Path(tmpdir),
                    browser=False,
                    qgis=True,
                    now=dt.datetime(2026, 5, 10, 19, 45, tzinfo=dt.timezone.utc),
                ),
                qgis_renderer=fake_qgis_renderer,
                diff_builder=fake_diff_builder,
            )

        self.assertFalse(result.browser_captured)
        self.assertTrue(result.qgis_captured)
        self.assertFalse(result.diff_captured)
        self.assertFalse(diff_called)

    def test_parser_accepts_manual_capture_controls(self):
        parser = build_parser()
        args = parser.parse_args([
            "valais-geneva-outdoors",
            "--mapbox-token",
            "test-mapbox-token",
            "--output-root",
            "/tmp/qfit-mapbox",
            "--style-json",
            "/tmp/mapbox-outdoors-v12.json",
            "--skip-qgis",
            "--qgis-contour-polygon-label-probe",
            "--qgis-contour-boundary-generator-label-probe",
            "--qgis-contour-bbox-edge-difference-label-probe",
            "--qgis-contour-bbox-edge-difference-source-style-label-probe",
            "--qgis-contour-bbox-edge-difference-source-style-high-zoom-label-probe",
            "--browser-timeout-ms",
            "5000",
        ])

        self.assertEqual(args.camera.name, "valais-geneva-outdoors")
        self.assertEqual(args.mapbox_token, "test-mapbox-token")
        self.assertEqual(args.output_root, "/tmp/qfit-mapbox")
        self.assertEqual(args.style_json, Path("/tmp/mapbox-outdoors-v12.json"))
        self.assertTrue(args.skip_qgis)
        self.assertTrue(args.qgis_contour_polygon_label_probe)
        self.assertTrue(args.qgis_contour_boundary_generator_label_probe)
        self.assertTrue(args.qgis_contour_bbox_edge_difference_label_probe)
        self.assertTrue(args.qgis_contour_bbox_edge_difference_source_style_label_probe)
        self.assertTrue(args.qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe)
        self.assertEqual(args.browser_timeout_ms, 5000)

    def test_main_all_cameras_runs_full_inspection_matrix(self):
        from qfit.validation import mapbox_outdoors_comparison

        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return types.SimpleNamespace(returncode=0, stdout=f"Camera: {command[2]}\n", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir) / "qfit-mapbox"
            with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
                with patch("builtins.print"):
                    result = mapbox_outdoors_comparison.main([
                        "--all-cameras",
                        "--mapbox-token",
                        "test-mapbox-token",
                        "--style-json",
                        "/tmp/mapbox-outdoors-v12.json",
                        "--output-root",
                        str(output_root),
                        "--skip-browser",
                        "--qgis-contour-polygon-label-probe",
                        "--qgis-contour-boundary-generator-label-probe",
                        "--qgis-contour-bbox-edge-difference-label-probe",
                        "--qgis-contour-bbox-edge-difference-source-style-label-probe",
                        "--qgis-contour-bbox-edge-difference-source-style-high-zoom-label-probe",
                        "--skip-diff",
                        "--browser-timeout-ms",
                        "5000",
                    ])

        self.assertEqual(result, 0)
        self.assertEqual([command[2] for command, _kwargs in calls], list(CAMERAS))
        for command, kwargs in calls:
            self.assertNotIn("test-mapbox-token", command)
            self.assertNotIn("--mapbox-token", command)
            self.assertNotIn("--all-cameras", command)
            self.assertIn("--style-json", command)
            self.assertIn("/tmp/mapbox-outdoors-v12.json", command)
            self.assertIn(str(output_root), command)
            self.assertIn("--skip-browser", command)
            self.assertIn("--qgis-contour-polygon-label-probe", command)
            self.assertIn("--qgis-contour-boundary-generator-label-probe", command)
            self.assertIn("--qgis-contour-bbox-edge-difference-label-probe", command)
            self.assertIn("--qgis-contour-bbox-edge-difference-source-style-label-probe", command)
            self.assertIn("--qgis-contour-bbox-edge-difference-source-style-high-zoom-label-probe", command)
            self.assertIn("--skip-diff", command)
            self.assertEqual(kwargs["env"]["MAPBOX_ACCESS_TOKEN"], "test-mapbox-token")
            self.assertEqual(kwargs["cwd"], mapbox_outdoors_comparison.REPO_ROOT)
            self.assertEqual(kwargs["timeout"], 65)

    def test_main_all_cameras_resolves_relative_style_json_before_spawning_children(self):
        from qfit.validation import mapbox_outdoors_comparison

        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            style_json = Path(tmpdir) / "snapshots" / "mapbox-outdoors-v12.json"
            output_root = Path(tmpdir) / "mapbox-output"
            expected_style_json = style_json.resolve()
            os.chdir(tmpdir)
            try:
                with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
                    result = mapbox_outdoors_comparison.main([
                        "--all-cameras",
                        "--mapbox-token",
                        "test-mapbox-token",
                        "--style-json",
                        "snapshots/mapbox-outdoors-v12.json",
                        "--output-root",
                        str(output_root),
                        "--skip-browser",
                        "--skip-qgis",
                        "--skip-diff",
                    ])
            finally:
                os.chdir(original_cwd)

        self.assertEqual(result, 0)
        for command, kwargs in calls:
            self.assertIn(str(expected_style_json), command)
            self.assertEqual(kwargs["cwd"], mapbox_outdoors_comparison.REPO_ROOT)

    def test_main_all_cameras_writes_matrix_summary_with_manifest_metrics(self):
        from qfit.validation import mapbox_outdoors_comparison

        with tempfile.TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir) / "test-mapbox-token-output"

            def fake_run(command, **_kwargs):
                camera_name = command[2]
                camera_index = list(CAMERAS).index(camera_name)
                run_dir = output_root / camera_name / "20260512T030000Z"
                run_dir.mkdir(parents=True)
                manifest_path = run_dir / "manifest.json"
                browser_png = run_dir / "mapbox-gl-reference.png"
                qgis_png = run_dir / "qgis-vector-render.png"
                diff_png = run_dir / "mapbox-gl-vs-qgis-diff.png"
                manifest_path.write_text(
                    json.dumps(
                        {
                            "outputs": {
                                "browser_reference": str(browser_png),
                                "qgis_vector_render": str(qgis_png),
                                "diff": str(diff_png),
                            },
                            "captured": {
                                "browser_reference": True,
                                "qgis_vector_render": True,
                                "diff": True,
                            },
                            "metrics": {
                                "changed_pixel_ratio": 0.1 + camera_index / 10,
                                "normalized_mean_absolute_channel_delta": 0.01 + camera_index / 100,
                                "normalized_rms_channel_delta": 0.02 + camera_index / 100,
                                "ssim_status": "unavailable",
                            },
                            "qgis_runtime": {
                                "qgis_version": "3.44.0-Solothurn",
                                "qgis_version_int": 34400,
                                "qgis_release_name": "Solothurn",
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return types.SimpleNamespace(
                    returncode=0,
                    stdout=f"Camera: {camera_name}\nManifest: {manifest_path}\n",
                    stderr="",
                )

            contact_sheet_calls = []

            def fake_contact_sheet(*, entries, output_path, **_kwargs):
                contact_sheet_calls.append(entries)
                output_path.write_bytes(PNG_PLACEHOLDER)
                return output_path

            with (
                patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run),
                patch(
                    "qfit.validation.mapbox_outdoors_comparison.build_all_cameras_contact_sheet",
                    side_effect=fake_contact_sheet,
                ),
                patch("builtins.print") as print_mock,
            ):
                result = mapbox_outdoors_comparison.main([
                    "--all-cameras",
                    "--mapbox-token",
                    "test-mapbox-token",
                    "--output-root",
                    str(output_root),
                    "--skip-browser",
                    "--skip-qgis",
                    "--skip-diff",
                ])

            summary_json = next((output_root / "all-cameras").glob("*/summary.json"), None)
            self.assertIsNotNone(summary_json, "all-cameras summary.json should be written")
            summary_markdown = summary_json.with_name("summary.md")
            summary_json_text = summary_json.read_text(encoding="utf-8")
            summary = json.loads(summary_json_text)
            summary_text = summary_markdown.read_text(encoding="utf-8")

        self.assertEqual(result, 0)
        self.assertEqual(summary["counts"], {"passed": len(CAMERAS), "failed": 0, "timeout": 0})
        self.assertEqual([entry["camera"] for entry in summary["cameras"]], list(CAMERAS))
        self.assertEqual(summary["cameras"][0]["artifact_status"], "metrics_available")
        self.assertEqual(summary["cameras"][0]["qgis_runtime"]["qgis_version"], "3.44.0-Solothurn")
        self.assertEqual(summary["cameras"][0]["metrics"]["changed_pixel_ratio"], 0.1)
        self.assertTrue(summary["cameras"][0]["outputs"]["browser_reference"].endswith("mapbox-gl-reference.png"))
        self.assertTrue(summary["cameras"][0]["outputs"]["qgis_vector_render"].endswith("qgis-vector-render.png"))
        self.assertTrue(summary["cameras"][0]["outputs"]["diff"].endswith("mapbox-gl-vs-qgis-diff.png"))
        self.assertTrue(summary["contact_sheet"].endswith("contact-sheet.jpg"))
        self.assertIn("test-mapbox-token", contact_sheet_calls[0][0]["outputs"]["browser_reference"])
        self.assertIn(
            "| `switzerland-alps-z5-outdoors` | passed | `metrics_available` | 3.44.0-Solothurn | 5.35 | 0.1000 |",
            summary_text,
        )
        self.assertIn("## Image artifacts", summary_text)
        self.assertIn("Contact sheet:", summary_text)
        self.assertIn("Contact sheet: `contact-sheet.jpg`", summary_text)
        self.assertIn(
            "`../../switzerland-alps-z5-outdoors/20260512T030000Z/manifest.json`",
            summary_text,
        )
        self.assertIn(
            "`../../switzerland-alps-z5-outdoors/20260512T030000Z/mapbox-gl-reference.png`",
            summary_text,
        )
        self.assertIn("mapbox-gl-reference.png", summary_text)
        self.assertIn("qgis-vector-render.png", summary_text)
        self.assertIn("mapbox-gl-vs-qgis-diff.png", summary_text)
        self.assertNotIn(str(output_root.parent), summary_text)
        self.assertNotIn("test-mapbox-token", summary_json_text)
        self.assertNotIn("test-mapbox-token", summary_text)
        for call in print_mock.call_args_list:
            if call.args:
                self.assertNotIn("test-mapbox-token", call.args[0])
        self.assertTrue(any(call.args[0].startswith("Matrix summary:") for call in print_mock.call_args_list))

    def test_main_all_cameras_flags_passed_camera_without_manifest_as_missing_artifacts(self):
        from qfit.validation import mapbox_outdoors_comparison

        with tempfile.TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir) / "test-mapbox-token-output"

            def fake_run(command, **_kwargs):
                return types.SimpleNamespace(returncode=0, stdout=f"Camera: {command[2]}\n", stderr="")

            with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
                result = mapbox_outdoors_comparison.main([
                    "--all-cameras",
                    "--mapbox-token",
                    "test-mapbox-token",
                    "--output-root",
                    str(output_root),
                    "--skip-browser",
                    "--skip-qgis",
                    "--skip-diff",
                ])

            summary_json = next((output_root / "all-cameras").glob("*/summary.json"), None)
            self.assertIsNotNone(summary_json, "all-cameras summary.json should be written")
            summary = json.loads(summary_json.read_text(encoding="utf-8"))
            summary_text = summary_json.with_name("summary.md").read_text(encoding="utf-8")

        self.assertEqual(result, 0)
        self.assertEqual(summary["cameras"][0]["status"], "passed")
        self.assertEqual(summary["cameras"][0]["artifact_status"], "manifest_missing")
        self.assertEqual(summary["cameras"][0]["metrics"], {})
        self.assertEqual(summary["cameras"][0]["outputs"], {})
        self.assertIn("| `switzerland-alps-z5-outdoors` | passed | `manifest_missing` |", summary_text)
        self.assertIn("| `switzerland-alps-z5-outdoors` | `—` | `—` | `—` |", summary_text)
        self.assertIn("The Artifacts column distinguishes subprocess success", summary_text)

    def test_manifest_artifact_status_distinguishes_unreadable_and_metricless_manifests(self):
        from qfit.validation import mapbox_outdoors_comparison

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            invalid_manifest = tmp_path / "invalid.json"
            invalid_manifest.write_text("{", encoding="utf-8")
            metricless_manifest = tmp_path / "metricless.json"
            diff_path = tmp_path / "mapbox-gl-vs-qgis-diff.png"
            metricless_manifest.write_text(
                json.dumps(
                    {
                        "outputs": {"diff": str(diff_path), "browser_reference": str(tmp_path / "missing.png")},
                        "captured": {"diff": True, "browser_reference": False},
                        "metrics": {},
                        "qgis_runtime": {"qgis_version": "3.44.0-Solothurn"},
                    }
                ),
                encoding="utf-8",
            )

            unreadable_status, unreadable_metrics = mapbox_outdoors_comparison._manifest_artifact_status_and_metrics(
                invalid_manifest
            )
            metricless_status, metricless_metrics = mapbox_outdoors_comparison._manifest_artifact_status_and_metrics(
                metricless_manifest
            )
            metricless_full = mapbox_outdoors_comparison._manifest_artifact_status_metrics_and_outputs(
                metricless_manifest,
                token="",
            )

        self.assertEqual(unreadable_status, "manifest_unreadable")
        self.assertEqual(unreadable_metrics, {})
        self.assertEqual(metricless_status, "metrics_unavailable")
        self.assertEqual(metricless_metrics, {})
        self.assertEqual(metricless_full[2], {"diff": str(diff_path)})
        self.assertEqual(metricless_full[3], {"qgis_version": "3.44.0-Solothurn"})

    def test_main_rejects_single_camera_with_all_cameras(self):
        from qfit.validation import mapbox_outdoors_comparison

        with patch("qfit.validation.mapbox_outdoors_comparison.run_comparison") as run_mock:
            with patch("sys.stderr") as stderr_mock:
                result = mapbox_outdoors_comparison.main([
                    "valais-geneva-outdoors",
                    "--all-cameras",
                ])

        self.assertEqual(result, 2)
        run_mock.assert_not_called()
        stderr_text = "".join(call.args[0] for call in stderr_mock.write.call_args_list)
        self.assertIn("either a single camera or --all-cameras", stderr_text)

    def test_main_all_cameras_prints_finished_camera_before_later_failure(self):
        from qfit.validation import mapbox_outdoors_comparison

        calls = []

        def fake_run(command, **_kwargs):
            calls.append(command)
            if len(calls) == 2:
                return types.SimpleNamespace(returncode=-11, stdout="", stderr="crashed for test-mapbox-token\n")
            return types.SimpleNamespace(returncode=0, stdout=f"Camera: {command[2]}\n", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir) / "test-mapbox-token-output"
            with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
                with patch("builtins.print") as print_mock:
                    with patch("sys.stderr") as stderr_mock:
                        result = mapbox_outdoors_comparison.main([
                            "--all-cameras",
                            "--mapbox-token",
                            "test-mapbox-token",
                            "--output-root",
                            str(output_root),
                            "--skip-browser",
                            "--skip-qgis",
                            "--skip-diff",
                        ])

        self.assertEqual(result, 2)
        self.assertEqual([command[2] for command in calls], list(CAMERAS))
        print_mock.assert_any_call("Camera: switzerland-alps-z5-outdoors\n", end="")
        stderr_text = "".join(
            call.args[0] for call in print_mock.call_args_list if call.kwargs.get("file") is stderr_mock
        )
        self.assertIn("camera valais-geneva-outdoors failed with exit code -11", stderr_text)
        self.assertIn("comparison capture failed for: valais-geneva-outdoors (exit -11)", stderr_text)
        self.assertNotIn("test-mapbox-token", stderr_text)

    def test_main_all_cameras_continues_after_timed_out_camera(self):
        from qfit.validation import mapbox_outdoors_comparison

        calls = []

        def fake_run(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(
                    command,
                    timeout=kwargs["timeout"],
                    output="partial output for test-mapbox-token\n",
                    stderr="partial error for test-mapbox-token\n",
                )
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir) / "test-mapbox-token-output"
            with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
                with patch("builtins.print") as print_mock:
                    with patch("sys.stderr") as stderr_mock:
                        result = mapbox_outdoors_comparison.main([
                            "--all-cameras",
                            "--mapbox-token",
                            "test-mapbox-token",
                            "--output-root",
                            str(output_root),
                            "--skip-browser",
                            "--skip-qgis",
                            "--skip-diff",
                            "--browser-timeout-ms",
                            "5000",
                        ])

        self.assertEqual(result, 2)
        self.assertEqual([command[2] for command in calls], list(CAMERAS))
        stderr_text = "".join(
            call.args[0] for call in print_mock.call_args_list if call.kwargs.get("file") is stderr_mock
        )
        self.assertIn("camera switzerland-alps-z5-outdoors timed out after 65s", stderr_text)
        self.assertIn(
            "comparison capture failed for: switzerland-alps-z5-outdoors (timeout after 65s)",
            stderr_text,
        )
        self.assertIn("partial error for <redacted>", stderr_text)
        self.assertNotIn("test-mapbox-token", stderr_text)

    def test_redact_sensitive_text_removes_token_from_errors(self):
        self.assertEqual(
            redact_sensitive_text("failed for test-mapbox-token", "test-mapbox-token"),
            "failed for <redacted>",
        )

    def test_main_lists_cameras_without_requiring_token(self):
        with patch("os.write") as write_mock:
            from qfit.validation import mapbox_outdoors_comparison

            result = mapbox_outdoors_comparison.main(["--list-cameras"])

        self.assertEqual(result, 0)
        written = b"".join(call.args[1] for call in write_mock.call_args_list).decode("utf-8")
        self.assertIn("valais-geneva-outdoors", written)

    def test_main_returns_error_when_token_is_missing(self):
        from qfit.validation import mapbox_outdoors_comparison

        with patch.dict("os.environ", {}, clear=True), patch("sys.stderr") as stderr_mock:
            result = mapbox_outdoors_comparison.main(["valais-geneva-outdoors"])

        self.assertEqual(result, 2)
        self.assertIn("Mapbox token required", "".join(call.args[0] for call in stderr_mock.write.call_args_list))

    def test_main_returns_targeted_error_when_style_json_is_missing(self):
        from qfit.validation import mapbox_outdoors_comparison

        with tempfile.TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "missing-style.json"
            with patch.dict("os.environ", {"MAPBOX_ACCESS_TOKEN": "test-mapbox-token"}, clear=True):
                with patch("sys.stderr") as stderr_mock:
                    result = mapbox_outdoors_comparison.main([
                        "valais-geneva-outdoors",
                        "--style-json",
                        str(missing_path),
                        "--skip-browser",
                        "--skip-qgis",
                        "--skip-diff",
                    ])

        stderr_text = "".join(call.args[0] for call in stderr_mock.write.call_args_list)
        self.assertEqual(result, 2)
        self.assertIn("style JSON not found", stderr_text)
        self.assertIn(str(missing_path), stderr_text)
        self.assertNotIn("comparison capture failed", stderr_text)

    def test_main_uses_generic_error_for_runtime_failures(self):
        from qfit.validation import mapbox_outdoors_comparison

        with patch("qfit.validation.mapbox_outdoors_comparison.run_comparison") as run_mock:
            run_mock.side_effect = RuntimeError("failed for test-mapbox-token")
            with patch("sys.stderr") as stderr_mock:
                result = mapbox_outdoors_comparison.main([
                    "valais-geneva-outdoors",
                    "--mapbox-token",
                    "test-mapbox-token",
                ])

        stderr_text = "".join(call.args[0] for call in stderr_mock.write.call_args_list)
        self.assertEqual(result, 2)
        self.assertIn("comparison capture failed", stderr_text)
        self.assertNotIn("test-mapbox-token", stderr_text)

    def test_main_uses_generic_error_for_network_failures(self):
        from qfit.validation import mapbox_outdoors_comparison

        with patch("qfit.validation.mapbox_outdoors_comparison.run_comparison") as run_mock:
            run_mock.side_effect = OSError("style fetch failed for test-mapbox-token")
            with patch("sys.stderr") as stderr_mock:
                result = mapbox_outdoors_comparison.main([
                    "valais-geneva-outdoors",
                    "--mapbox-token",
                    "test-mapbox-token",
                ])

        stderr_text = "".join(call.args[0] for call in stderr_mock.write.call_args_list)
        self.assertEqual(result, 2)
        self.assertIn("comparison capture failed", stderr_text)
        self.assertNotIn("test-mapbox-token", stderr_text)

    def test_default_output_root_stays_under_ignored_debug_directory(self):
        self.assertEqual(DEFAULT_OUTPUT_ROOT, Path(__file__).resolve().parents[1] / "debug" / "mapbox-outdoors-comparison")


if __name__ == "__main__":
    unittest.main()
