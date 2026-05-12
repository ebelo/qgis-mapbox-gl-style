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
    ComparisonConfig,
    DEFAULT_OUTPUT_ROOT,
    MapboxComparisonCamera,
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
    redact_sensitive_text,
    render_browser_reference,
    render_qgis_vector,
    resolve_mapbox_token,
    run_comparison,
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
        self.assertTrue(13.0 <= CAMERAS["chamonix-trails-z14-outdoors"].zoom <= 14.5)
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
        self.assertEqual(paths.manifest_json, Path("/tmp/run/manifest.json"))

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

        class FakeBackgroundMapService:
            def _apply_mapbox_gl_style(self, layer, style_definition, *, sprite_resources=None):
                layer.applied_style = style_definition
                layer.sprite_resources = sprite_resources

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
        fake_mapbox_config.simplify_mapbox_style_expressions = lambda style: style
        fake_mapbox_config.extract_mapbox_vector_source_ids = lambda _style: ["mapbox.mapbox-streets-v8"]
        fake_mapbox_config.build_vector_tile_layer_uri = lambda *_args, **_kwargs: "vector://style"
        fake_mapbox_config.fetch_mapbox_sprite_resources = lambda *_args, **_kwargs: "sprite-resources"

        fake_background_service = types.ModuleType("qfit.visualization.infrastructure.background_map_service")
        fake_background_service.BackgroundMapService = FakeBackgroundMapService

        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
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

            render_qgis_vector(
                camera=CAMERAS["valais-geneva-outdoors"],
                token="test-mapbox-token",
                output_path=output_path,
                style_definition=SAMPLE_STYLE,
            )

            self.assertEqual(output_path.read_bytes(), PNG_PLACEHOLDER)

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

    def test_run_comparison_writes_manifest_without_token(self):
        def fake_browser_renderer(*, output_path, **_kwargs):
            output_path.write_bytes(PNG_PLACEHOLDER)

        def fake_qgis_renderer(*, output_path, **_kwargs):
            output_path.write_bytes(PNG_PLACEHOLDER)

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

        self.assertTrue(result.browser_captured)
        self.assertTrue(result.qgis_captured)
        self.assertTrue(result.diff_captured)
        self.assertNotIn("test-mapbox-token", manifest_text)
        self.assertEqual(manifest["camera"]["name"], "valais-geneva-outdoors")
        self.assertEqual(manifest["style_url"], "mapbox://styles/mapbox/outdoors-v12")
        self.assertTrue(manifest["captured"]["browser_reference"])
        self.assertTrue(manifest["captured"]["qgis_vector_render"])
        self.assertTrue(manifest["captured"]["diff"])
        self.assertEqual(manifest["metrics"]["changed_pixel_ratio"], 0.25)
        self.assertEqual(metrics["changed_pixel_ratio"], 0.25)

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
            "--browser-timeout-ms",
            "5000",
        ])

        self.assertEqual(args.camera.name, "valais-geneva-outdoors")
        self.assertEqual(args.mapbox_token, "test-mapbox-token")
        self.assertEqual(args.output_root, "/tmp/qfit-mapbox")
        self.assertEqual(args.style_json, Path("/tmp/mapbox-outdoors-v12.json"))
        self.assertTrue(args.skip_qgis)
        self.assertEqual(args.browser_timeout_ms, 5000)

    def test_main_all_cameras_runs_full_inspection_matrix(self):
        from qfit.validation import mapbox_outdoors_comparison

        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            return types.SimpleNamespace(returncode=0, stdout=f"Camera: {command[2]}\n", stderr="")

        with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
            with patch("builtins.print"):
                result = mapbox_outdoors_comparison.main([
                    "--all-cameras",
                    "--mapbox-token",
                    "test-mapbox-token",
                    "--style-json",
                    "/tmp/mapbox-outdoors-v12.json",
                    "--output-root",
                    "/tmp/qfit-mapbox",
                    "--skip-browser",
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
            self.assertIn("--skip-browser", command)
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
                manifest_path.write_text(
                    json.dumps(
                        {
                            "metrics": {
                                "changed_pixel_ratio": 0.1 + camera_index / 10,
                                "normalized_mean_absolute_channel_delta": 0.01 + camera_index / 100,
                                "normalized_rms_channel_delta": 0.02 + camera_index / 100,
                                "ssim_status": "unavailable",
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                return types.SimpleNamespace(
                    returncode=0,
                    stdout=f"Camera: {camera_name}\nManifest: {manifest_path}\n",
                    stderr="",
                )

            with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
                with patch("builtins.print") as print_mock:
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
        self.assertEqual(summary["cameras"][0]["metrics"]["changed_pixel_ratio"], 0.1)
        self.assertIn(
            "| `switzerland-alps-z5-outdoors` | passed | `metrics_available` | 5.35 | 0.1000 |",
            summary_text,
        )
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
        self.assertIn("| `switzerland-alps-z5-outdoors` | passed | `manifest_missing` |", summary_text)
        self.assertIn("The Artifacts column distinguishes subprocess success", summary_text)

    def test_manifest_artifact_status_distinguishes_unreadable_and_metricless_manifests(self):
        from qfit.validation import mapbox_outdoors_comparison

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            invalid_manifest = tmp_path / "invalid.json"
            invalid_manifest.write_text("{", encoding="utf-8")
            metricless_manifest = tmp_path / "metricless.json"
            metricless_manifest.write_text(json.dumps({"metrics": {}}), encoding="utf-8")

            unreadable_status, unreadable_metrics = mapbox_outdoors_comparison._manifest_artifact_status_and_metrics(
                invalid_manifest
            )
            metricless_status, metricless_metrics = mapbox_outdoors_comparison._manifest_artifact_status_and_metrics(
                metricless_manifest
            )

        self.assertEqual(unreadable_status, "manifest_unreadable")
        self.assertEqual(unreadable_metrics, {})
        self.assertEqual(metricless_status, "metrics_unavailable")
        self.assertEqual(metricless_metrics, {})

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

        with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
            with patch("builtins.print") as print_mock:
                with patch("sys.stderr") as stderr_mock:
                    result = mapbox_outdoors_comparison.main([
                        "--all-cameras",
                        "--mapbox-token",
                        "test-mapbox-token",
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

        with patch("qfit.validation.mapbox_outdoors_comparison.subprocess.run", side_effect=fake_run):
            with patch("builtins.print") as print_mock:
                with patch("sys.stderr") as stderr_mock:
                    result = mapbox_outdoors_comparison.main([
                        "--all-cameras",
                        "--mapbox-token",
                        "test-mapbox-token",
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
