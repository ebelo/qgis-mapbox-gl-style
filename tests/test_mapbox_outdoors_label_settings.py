import datetime as dt
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import _path  # noqa: F401

from qfit.validation.mapbox_outdoors_label_settings import (
    LabelSettingsConfig,
    _convert_style_to_labeling,
    _ensure_qgis_application,
    _fetch_sprite_resources,
    _label_settings_report,
    _load_original_style,
    _postprocessed_label_records,
    build_label_settings_paths,
    build_run_directory,
    build_summary_markdown,
    collect_label_settings,
    label_settings_record,
    load_style_definition,
    main,
    resolve_mapbox_token,
    source_label_layer_records,
    write_report,
)


class FakeProperties:
    def __init__(self, keys):
        self._keys = keys

    def propertyKeys(self):
        return self._keys


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
        return SimpleNamespace(name="Millimeters")

    def color(self):
        return FakeColor("#dcdcd4")

    def opacity(self):
        return 0.75


class FakeTextFormat:
    def size(self):
        return 2.5135416667

    def sizeUnit(self):
        return SimpleNamespace(name="Millimeters")

    def color(self):
        return FakeColor("#626250")

    def opacity(self):
        return 0.9

    def buffer(self):
        return FakeTextBuffer()


class FakeStyle:
    def __init__(self, *, style_name, layer_name, geometry_type=None):
        self._style_name = style_name
        self._layer_name = layer_name
        self._geometry_type = geometry_type or SimpleNamespace(name="Line")

    def styleName(self):
        return self._style_name

    def layerName(self):
        return self._layer_name

    def geometryType(self):
        return self._geometry_type


class FakeLabelStyle(FakeStyle):
    def __init__(self, *, style_name, layer_name, settings, geometry_type=None):
        super().__init__(style_name=style_name, layer_name=layer_name, geometry_type=geometry_type)
        self._settings = settings

    def labelSettings(self):
        return self._settings


class FakeLabeling:
    def __init__(self, styles):
        self._styles = styles

    def styles(self):
        return self._styles


class FakeSettings:
    fieldName = '"name"'
    isExpression = True
    priority = 7
    placement = SimpleNamespace(name="Line")
    repeatDistance = 66.1458333333
    repeatDistanceUnit = SimpleNamespace(name="Millimeters")
    displayAll = False
    obstacle = True
    placementFlags = 1
    labelPerPart = False
    mergeLines = False
    maxCurvedCharAngleIn = 25.0
    maxCurvedCharAngleOut = -25.0
    overrunDistance = 0.0
    overrunDistanceUnit = SimpleNamespace(name="Millimeters")

    def dataDefinedProperties(self):
        return FakeProperties([87, 50])

    def format(self):
        return FakeTextFormat()


class FakeQgsApplication:
    _instance = None

    def __init__(self, args, enabled):
        self.args = args
        self.enabled = enabled
        self.initialized = False
        self.exited = False

    @classmethod
    def instance(cls):
        return cls._instance

    def initQgis(self):
        self.initialized = True
        type(self)._instance = self

    def exitQgis(self):
        self.exited = True
        type(self)._instance = None


class FakeConversionContext:
    last_instance = None

    def __init__(self):
        self.target_unit = None
        self.pixel_factor = None
        type(self).last_instance = self

    def setTargetUnit(self, unit):
        self.target_unit = unit

    def setPixelSizeConversionFactor(self, factor):
        self.pixel_factor = factor


class FakeConverter:
    Success = "success"
    last_style = None

    def __init__(self):
        self._labeling = FakeLabeling(
            [
                FakeLabelStyle(
                    style_name="road-label-z15-plus",
                    layer_name="road",
                    settings=FakeSettings(),
                )
            ]
        )

    def convert(self, style, context):
        type(self).last_style = style
        self.context = context
        return self.Success

    def labeling(self):
        return self._labeling


class FakeBackgroundMapService:
    applied_count = 0
    labeling = None


def fake_apply_label_priority(labeling):
    FakeBackgroundMapService.applied_count += 1
    FakeBackgroundMapService.labeling = labeling


def _fake_qgis_modules():
    qgis_module = types.ModuleType("qgis")
    core_module = types.ModuleType("qgis.core")
    core_module.QgsApplication = FakeQgsApplication
    core_module.QgsMapBoxGlStyleConversionContext = FakeConversionContext
    core_module.QgsMapBoxGlStyleConverter = FakeConverter
    core_module.Qgis = SimpleNamespace(RenderUnit=SimpleNamespace(Millimeters="millimeters"))
    qgis_module.core = core_module
    return {"qgis": qgis_module, "qgis.core": core_module}


def _fake_background_map_service_module():
    module = types.ModuleType("qfit.visualization.infrastructure.background_map_service")
    module.apply_mapbox_label_priority = fake_apply_label_priority
    return module


class MapboxOutdoorsLabelSettingsTests(unittest.TestCase):
    def setUp(self):
        FakeQgsApplication._instance = None
        FakeBackgroundMapService.applied_count = 0
        FakeBackgroundMapService.labeling = None
        FakeConversionContext.last_instance = None
        FakeConverter.last_style = None

    def test_resolve_mapbox_token_prefers_argument_then_environment(self):
        self.assertEqual(
            resolve_mapbox_token(
                provided_token="arg-token",
                environ={"MAPBOX_ACCESS_TOKEN": "env-token", "QFIT_MAPBOX_ACCESS_TOKEN": "qfit-token"},
            ),
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
        self.assertIsNone(resolve_mapbox_token(provided_token=None, environ={}))

    def test_build_run_directory_uses_style_slug_and_timestamp(self):
        run_dir = build_run_directory(
            output_root=Path("/tmp/qfit-labels"),
            style_owner="mapbox",
            style_id="outdoors-v12",
            now=dt.datetime(2026, 5, 18, 8, 22, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(run_dir, Path("/tmp/qfit-labels/mapbox-outdoors-v12/20260518T082200Z"))

    def test_build_label_settings_paths_are_predictable(self):
        paths = build_label_settings_paths(Path("/tmp/run"))

        self.assertEqual(paths.json_path, Path("/tmp/run/label-settings.json"))
        self.assertEqual(paths.summary_path, Path("/tmp/run/summary.md"))

    def test_load_style_definition_requires_json_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            good_path = Path(tmpdir) / "style.json"
            good_path.write_text(json.dumps({"version": 8, "layers": []}), encoding="utf-8")
            self.assertEqual(load_style_definition(good_path), {"version": 8, "layers": []})

            bad_path = Path(tmpdir) / "bad.json"
            bad_path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_style_definition(bad_path)

    def test_label_settings_record_captures_qgis_fields_and_base_layer(self):
        record = label_settings_record(
            FakeStyle(style_name="road-label-z15-plus", layer_name="road"),
            FakeSettings(),
        )

        self.assertEqual(record["style_name"], "road-label-z15-plus")
        self.assertEqual(record["base_style_layer_id"], "road-label")
        self.assertEqual(record["source_layer"], "road")
        self.assertEqual(record["geometry_type"], "Line")
        self.assertEqual(record["field_name"], '"name"')
        self.assertTrue(record["is_expression"])
        self.assertEqual(record["priority"], 7)
        self.assertEqual(record["placement"], "Line")
        self.assertAlmostEqual(record["repeat_distance"], 66.1458333333)
        self.assertEqual(record["repeat_distance_unit"], "Millimeters")
        self.assertFalse(record["display_all"])
        self.assertTrue(record["obstacle"])
        self.assertEqual(record["placement_flags"], 1)
        self.assertFalse(record["label_per_part"])
        self.assertFalse(record["merge_lines"])
        self.assertEqual(record["max_curved_char_angle_in"], 25.0)
        self.assertEqual(record["max_curved_char_angle_out"], -25.0)
        self.assertEqual(record["overrun_distance"], 0.0)
        self.assertEqual(record["overrun_distance_unit"], "Millimeters")
        self.assertAlmostEqual(record["text_size"], 2.5135416667)
        self.assertEqual(record["text_size_unit"], "Millimeters")
        self.assertEqual(record["text_color"], "#626250")
        self.assertEqual(record["text_opacity"], 0.9)
        self.assertTrue(record["buffer_enabled"])
        self.assertAlmostEqual(record["buffer_size"], 0.5291666667)
        self.assertEqual(record["buffer_size_unit"], "Millimeters")
        self.assertEqual(record["buffer_color"], "#dcdcd4")
        self.assertEqual(record["buffer_opacity"], 0.75)
        self.assertEqual(record["data_defined_property_keys"], [50, 87])

    def test_ensure_qgis_application_reuses_or_creates_application(self):
        existing_app = FakeQgsApplication([], False)
        FakeQgsApplication._instance = existing_app

        app, created = _ensure_qgis_application(FakeQgsApplication)

        self.assertIs(app, existing_app)
        self.assertFalse(created)

        FakeQgsApplication._instance = None
        app, created = _ensure_qgis_application(FakeQgsApplication)

        self.assertTrue(created)
        self.assertTrue(app.initialized)
        self.assertIs(FakeQgsApplication.instance(), app)

    def test_load_original_style_uses_fixture_or_live_fetcher(self):
        config = LabelSettingsConfig(token=None, output_root=Path("/tmp"))
        with self.assertRaises(ValueError):
            _load_original_style(config, lambda *_args: {})

        with tempfile.TemporaryDirectory() as tmpdir:
            style_path = Path(tmpdir) / "style.json"
            style_path.write_text(json.dumps({"version": 8, "layers": []}), encoding="utf-8")
            self.assertEqual(
                _load_original_style(
                    LabelSettingsConfig(token=None, output_root=Path("/tmp"), style_json_path=style_path),
                    lambda *_args: {"unexpected": True},
                ),
                {"version": 8, "layers": []},
            )

        calls = []

        def fetcher(token, owner, style_id):
            calls.append((token, owner, style_id))
            return {"version": 8}

        self.assertEqual(
            _load_original_style(LabelSettingsConfig(token="token", output_root=Path("/tmp")), fetcher),
            {"version": 8},
        )
        self.assertEqual(calls, [("token", "mapbox", "outdoors-v12")])

    def test_fetch_sprite_resources_handles_disabled_success_and_failures(self):
        config = LabelSettingsConfig(token="token", output_root=Path("/tmp"), include_sprite_context=False)

        self.assertEqual(_fetch_sprite_resources(config, {"sprite": "sprite-url"}, lambda *_args, **_kwargs: None), (None, 0))

        resources = SimpleNamespace(definitions={"poi": {}, "water": {}})
        calls = []

        def fetcher(token, owner, style_id, *, sprite_url):
            calls.append((token, owner, style_id, sprite_url))
            return resources

        loaded, count = _fetch_sprite_resources(
            LabelSettingsConfig(token="token", output_root=Path("/tmp")),
            {"sprite": "sprite-url"},
            fetcher,
        )

        self.assertIs(loaded, resources)
        self.assertEqual(count, 2)
        self.assertEqual(calls, [("token", "mapbox", "outdoors-v12", "sprite-url")])

        def failing_fetcher(*_args, **_kwargs):
            raise RuntimeError("unavailable")

        self.assertEqual(
            _fetch_sprite_resources(
                LabelSettingsConfig(token="token", output_root=Path("/tmp")),
                {"sprite": "sprite-url"},
                failing_fetcher,
            ),
            (None, 0),
        )

    def test_convert_style_to_labeling_uses_qgis_converter_context(self):
        result, labeling, sprite_loaded = _convert_style_to_labeling(
            {"version": 8},
            None,
            (
                FakeConversionContext,
                FakeConverter,
                SimpleNamespace(RenderUnit=SimpleNamespace(Millimeters="millimeters")),
            ),
        )

        self.assertEqual(result, "success")
        self.assertIsInstance(labeling, FakeLabeling)
        self.assertFalse(sprite_loaded)
        self.assertEqual(FakeConverter.last_style, {"version": 8})
        self.assertEqual(FakeConversionContext.last_instance.target_unit, "millimeters")
        self.assertAlmostEqual(FakeConversionContext.last_instance.pixel_factor, 25.4 / 96.0)

    def test_postprocessed_label_records_sorts_and_applies_runtime_priorities(self):
        records = _postprocessed_label_records(
            FakeLabeling(
                [
                    FakeLabelStyle(style_name="waterway-label-z17-plus", layer_name="waterway", settings=FakeSettings()),
                    FakeLabelStyle(style_name="road-label-z15-plus", layer_name="road", settings=FakeSettings()),
                ]
            ),
            fake_apply_label_priority,
        )

        self.assertEqual(FakeBackgroundMapService.applied_count, 1)
        self.assertIsInstance(FakeBackgroundMapService.labeling, FakeLabeling)
        self.assertEqual([record["base_style_layer_id"] for record in records], ["road-label", "waterway-label"])
        self.assertEqual(_postprocessed_label_records(None, fake_apply_label_priority), [])

    def test_source_label_layer_records_align_original_and_qfit_controls(self):
        original_style = {
            "version": 8,
            "layers": [
                {
                    "id": "contour-label",
                    "type": "symbol",
                    "source-layer": "contour",
                    "minzoom": 12,
                    "filter": ["==", ["get", "index"], 5],
                    "layout": {
                        "icon-image": "mountain",
                        "icon-size": 0.8,
                        "symbol-placement": "line",
                        "text-field": ["concat", ["get", "ele"], " m"],
                        "text-letter-spacing": ["match", ["get", "class"], "ocean", 0.25, 0.01],
                        "text-max-width": ["match", ["get", "class"], "ocean", 4, 10],
                        "text-size": ["interpolate", ["linear"], ["zoom"], 12, 10, 16, 12],
                        "text-max-angle": 25,
                    },
                    "paint": {
                        "icon-opacity": 0.75,
                        "text-color": "#626250",
                        "text-halo-color": "#dcdcd4",
                        "text-halo-width": 2,
                    },
                }
            ],
        }
        qfit_style = {
            "version": 8,
            "layers": [
                {
                    "id": "contour-label",
                    "type": "symbol",
                    "source-layer": "contour",
                    "minzoom": 12,
                    "layout": {
                        "icon-image": "mountain",
                        "icon-size": 0.8,
                        "symbol-placement": "line",
                        "text-field": ["concat", ["get", "ele"], " m"],
                        "text-letter-spacing": 0.25,
                        "text-max-width": 4,
                        "text-size": 9,
                    },
                    "paint": {
                        "icon-opacity": 0.75,
                        "text-color": "#626250",
                        "text-halo-color": "#dcdcd4",
                    },
                }
            ],
        }

        records = source_label_layer_records(
            original_style,
            qfit_style,
            [{"base_style_layer_id": "contour-label", "style_name": "contour-label"}],
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["base_style_layer_id"], "contour-label")
        self.assertEqual(record["qfit_style_layer_id"], "contour-label")
        self.assertEqual(record["source_layer"], "contour")
        self.assertEqual(record["filter"], ["==", ["get", "index"], 5])
        self.assertEqual(record["layout"]["icon-image"], "mountain")
        self.assertEqual(record["layout"]["icon-size"], 0.8)
        self.assertEqual(record["layout"]["symbol-placement"], "line")
        self.assertEqual(record["layout"]["text-letter-spacing"], ["match", ["get", "class"], "ocean", 0.25, 0.01])
        self.assertEqual(record["layout"]["text-max-width"], ["match", ["get", "class"], "ocean", 4, 10])
        self.assertEqual(record["layout"]["text-size"], ["interpolate", ["linear"], ["zoom"], 12, 10, 16, 12])
        self.assertEqual(record["paint"]["icon-opacity"], 0.75)
        self.assertEqual(record["paint"]["text-halo-width"], 2)
        self.assertEqual(record["qfit_layout"]["icon-image"], "mountain")
        self.assertEqual(record["qfit_layout"]["text-letter-spacing"], 0.25)
        self.assertEqual(record["qfit_layout"]["text-max-width"], 4)
        self.assertEqual(record["qfit_layout"]["text-size"], 9)

    def test_source_label_layer_records_marks_missing_qfit_layer(self):
        records = source_label_layer_records(
            {
                "version": 8,
                "layers": [
                    {
                        "id": "contour-label",
                        "type": "symbol",
                        "source-layer": "contour",
                        "layout": {"text-field": ["get", "ele"]},
                        "paint": {"text-color": "#626250"},
                    }
                ],
            },
            {"version": 8, "layers": []},
            [{"base_style_layer_id": "contour-label", "style_name": "contour-label"}],
        )

        self.assertIsNone(records[0]["qfit_style_layer_id"])
        self.assertIsNone(records[0]["qfit_filter"])
        self.assertEqual(records[0]["qfit_layout"], {})
        self.assertEqual(records[0]["qfit_paint"], {})

    def test_label_settings_report_captures_summary_metadata(self):
        report = _label_settings_report(
            config=LabelSettingsConfig(token="token", output_root=Path("/tmp")),
            result="success",
            sprite_loaded=True,
            sprite_count=440,
            records=[{"style_name": "contour-label"}],
            source_label_layers=[{"base_style_layer_id": "contour-label"}],
        )

        self.assertEqual(report["style_owner"], "mapbox")
        self.assertEqual(report["style_id"], "outdoors-v12")
        self.assertEqual(report["qgis_converter_result"], "success")
        self.assertTrue(report["sprite_context_loaded"])
        self.assertEqual(report["sprite_definition_count"], 440)
        self.assertEqual(report["label_count"], 1)
        self.assertEqual(report["source_label_layer_count"], 1)

    def test_collect_label_settings_runs_with_fake_qgis_runtime(self):
        modules = {
            **_fake_qgis_modules(),
            "qfit.visualization.infrastructure.background_map_service": _fake_background_map_service_module(),
        }
        with mock.patch.dict(sys.modules, modules):
            with mock.patch(
                "qfit.mapbox_config.fetch_mapbox_style_definition",
                return_value={
                    "version": 8,
                    "layers": [
                        {
                            "id": "road-label",
                            "type": "symbol",
                            "source-layer": "road",
                            "layout": {"text-field": ["get", "name"], "text-size": 11},
                            "paint": {"text-color": "#111111"},
                        }
                    ],
                    "sprite": "sprite-url",
                },
            ) as fetch_style:
                with mock.patch(
                    "qfit.mapbox_config.fetch_mapbox_sprite_resources",
                    return_value=SimpleNamespace(definitions={"road": {}}, image_bytes=b"not-an-image"),
                ) as fetch_sprites:
                    with mock.patch(
                        "qfit.mapbox_config.simplify_mapbox_style_expressions",
                        return_value={
                            "version": 8,
                            "layers": [
                                {
                                    "id": "road-label-z15-plus",
                                    "type": "symbol",
                                    "source-layer": "road",
                                    "layout": {"text-field": ["get", "name"], "text-size": 11},
                                    "paint": {"text-color": "#111111"},
                                }
                            ],
                        },
                    ):
                        report = collect_label_settings(LabelSettingsConfig(token="token", output_root=Path("/tmp")))

        fetch_style.assert_called_once_with("token", "mapbox", "outdoors-v12")
        fetch_sprites.assert_called_once()
        self.assertEqual(report["qgis_converter_result"], "success")
        self.assertEqual(report["sprite_definition_count"], 1)
        self.assertFalse(report["sprite_context_loaded"])
        self.assertEqual(report["label_count"], 1)
        self.assertEqual(report["labels"][0]["base_style_layer_id"], "road-label")
        self.assertEqual(report["source_label_layer_count"], 1)
        self.assertEqual(report["source_label_layers"][0]["qfit_style_layer_id"], "road-label-z15-plus")
        self.assertIsNone(FakeQgsApplication.instance())

    def test_summary_markdown_lists_label_settings(self):
        report = {
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "generated": "2026-05-18T08:22:00+00:00",
            "sprite_context_loaded": True,
            "sprite_definition_count": 440,
            "label_count": 1,
            "source_label_layer_count": 1,
            "labels": [
                {
                    "base_style_layer_id": "contour-label",
                    "style_name": "contour-label",
                    "source_layer": "contour",
                    "geometry_type": "Line",
                    "field_name": "concat(\"ele\", ' m')",
                    "is_expression": True,
                    "priority": 3,
                    "placement": "Line",
                    "repeat_distance": 0.0,
                    "repeat_distance_unit": "Millimeters",
                    "display_all": False,
                    "obstacle": True,
                    "placement_flags": 1,
                    "label_per_part": False,
                    "merge_lines": False,
                    "max_curved_char_angle_in": 25.0,
                    "max_curved_char_angle_out": -25.0,
                    "overrun_distance": 0.0,
                    "overrun_distance_unit": "Millimeters",
                    "text_size": 2.5135416667,
                    "text_size_unit": "Millimeters",
                    "text_color": "#626250",
                    "text_opacity": 0.9,
                    "buffer_enabled": True,
                    "buffer_size": 0.5291666667,
                    "buffer_size_unit": "Millimeters",
                    "buffer_color": "#dcdcd4",
                    "buffer_opacity": 0.75,
                    "data_defined_property_keys": ["pipe|key"],
                }
            ],
            "source_label_layers": [
                {
                    "base_style_layer_id": "contour-label",
                    "style_name": "contour-label",
                    "qfit_style_layer_id": "contour-label",
                    "source_layer": "contour",
                    "minzoom": 12,
                    "maxzoom": None,
                    "qfit_minzoom": 12,
                    "qfit_maxzoom": None,
                    "filter": ["==", ["get", "index"], 5],
                    "qfit_filter": ["==", ["get", "index"], 5],
                    "layout": {"symbol-placement": "line", "text-field": ["concat", ["get", "ele"], " m"]},
                    "paint": {"text-color": "#626250", "text-halo-color": "#dcdcd4"},
                    "qfit_layout": {"symbol-placement": "line", "text-field": ["concat", ["get", "ele"], " m"]},
                    "qfit_paint": {"text-color": "#626250", "text-halo-color": "#dcdcd4"},
                }
            ],
        }

        markdown = build_summary_markdown(report)

        self.assertIn("# Mapbox Outdoors QGIS label settings — mapbox/outdoors-v12", markdown)
        self.assertIn("Converted label styles: 1", markdown)
        self.assertIn("Sprite context loaded: yes", markdown)
        self.assertIn("contour-label", markdown)
        self.assertIn("| contour-label | contour-label | contour | Line |", markdown)
        self.assertIn("concat", markdown)
        self.assertIn("Millimeters", markdown)
        self.assertIn("#626250", markdown)
        self.assertIn("#dcdcd4", markdown)
        self.assertIn("25/-25", markdown)
        self.assertIn("pipe\\|key", markdown)
        self.assertIn("## Source Mapbox label controls", markdown)
        self.assertIn("Source label layers: 1", markdown)
        self.assertIn("| contour-label | contour-label | contour-label | contour | 12+", markdown)
        self.assertIn("\"symbol-placement\":\"line\"", markdown)
        self.assertIn("\"text-halo-color\":\"#dcdcd4\"", markdown)

    def test_summary_markdown_collapses_empty_compound_cells(self):
        report = {
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "generated": "2026-05-18T10:02:00+00:00",
            "sprite_context_loaded": False,
            "sprite_definition_count": 0,
            "label_count": 1,
            "labels": [{"style_name": "sparse-label"}],
            "source_label_layer_count": 1,
            "source_label_layers": [
                {
                    "base_style_layer_id": "sparse-label",
                    "style_name": "sparse-label",
                    "qfit_style_layer_id": None,
                    "source_layer": "sparse",
                    "filter": [],
                    "qfit_filter": [],
                    "layout": {},
                    "paint": {},
                    "qfit_layout": {},
                    "qfit_paint": {},
                }
            ],
        }

        markdown = build_summary_markdown(report)

        self.assertIn("sparse-label", markdown)
        self.assertNotIn("— —", markdown)
        self.assertNotIn("—/—", markdown)
        self.assertNotIn("[]", markdown)
        self.assertIn("| sparse-label | sparse-label | — | sparse | all | — | — | — |", markdown)

    def test_write_report_writes_json_and_summary(self):
        report = {
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "generated": "2026-05-18T08:22:00+00:00",
            "label_count": 0,
            "labels": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = build_label_settings_paths(Path(tmpdir) / "run")

            write_report(report, paths)

            self.assertEqual(json.loads(paths.json_path.read_text(encoding="utf-8"))["label_count"], 0)
            self.assertIn("Converted label styles: 0", paths.summary_path.read_text(encoding="utf-8"))

    def test_main_writes_report_from_arguments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            style_path = Path(tmpdir) / "style.json"
            output_root = Path(tmpdir) / "labels"
            style_path.write_text(json.dumps({"version": 8, "layers": []}), encoding="utf-8")

            with mock.patch(
                "qfit.validation.mapbox_outdoors_label_settings.collect_label_settings",
                return_value={
                    "style_owner": "mapbox",
                    "style_id": "outdoors-v12",
                    "generated": "2026-05-18T08:22:00+00:00",
                    "label_count": 0,
                    "labels": [],
                },
            ) as collect:
                exit_code = main(
                    [
                        "--style-json",
                        str(style_path),
                        "--output-root",
                        str(output_root),
                        "--no-sprite-context",
                    ]
                )

            self.assertEqual(exit_code, 0)
            collect.assert_called_once()
            self.assertEqual(collect.call_args.args[0].style_json_path, style_path)
            self.assertFalse(collect.call_args.args[0].include_sprite_context)
            self.assertEqual(len(list(output_root.glob("mapbox-outdoors-v12/*/summary.md"))), 1)


if __name__ == "__main__":
    unittest.main()
