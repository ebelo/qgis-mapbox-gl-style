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
    write_report,
)


class FakeProperties:
    def __init__(self, keys):
        self._keys = keys

    def propertyKeys(self):
        return self._keys


class FakeStyle:
    def __init__(self, *, style_name, layer_name):
        self._style_name = style_name
        self._layer_name = layer_name

    def styleName(self):
        return self._style_name

    def layerName(self):
        return self._layer_name


class FakeLabelStyle(FakeStyle):
    def __init__(self, *, style_name, layer_name, settings):
        super().__init__(style_name=style_name, layer_name=layer_name)
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

    def dataDefinedProperties(self):
        return FakeProperties([87, 50])


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
        self.assertEqual(record["field_name"], '"name"')
        self.assertTrue(record["is_expression"])
        self.assertEqual(record["priority"], 7)
        self.assertEqual(record["placement"], "Line")
        self.assertAlmostEqual(record["repeat_distance"], 66.1458333333)
        self.assertEqual(record["repeat_distance_unit"], "Millimeters")
        self.assertFalse(record["display_all"])
        self.assertTrue(record["obstacle"])
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

    def test_label_settings_report_captures_summary_metadata(self):
        report = _label_settings_report(
            config=LabelSettingsConfig(token="token", output_root=Path("/tmp")),
            result="success",
            sprite_loaded=True,
            sprite_count=440,
            records=[{"style_name": "contour-label"}],
        )

        self.assertEqual(report["style_owner"], "mapbox")
        self.assertEqual(report["style_id"], "outdoors-v12")
        self.assertEqual(report["qgis_converter_result"], "success")
        self.assertTrue(report["sprite_context_loaded"])
        self.assertEqual(report["sprite_definition_count"], 440)
        self.assertEqual(report["label_count"], 1)

    def test_collect_label_settings_runs_with_fake_qgis_runtime(self):
        modules = {
            **_fake_qgis_modules(),
            "qfit.visualization.infrastructure.background_map_service": _fake_background_map_service_module(),
        }
        with mock.patch.dict(sys.modules, modules):
            with mock.patch(
                "qfit.mapbox_config.fetch_mapbox_style_definition",
                return_value={"version": 8, "layers": [], "sprite": "sprite-url"},
            ) as fetch_style:
                with mock.patch(
                    "qfit.mapbox_config.fetch_mapbox_sprite_resources",
                    return_value=SimpleNamespace(definitions={"road": {}}, image_bytes=b"not-an-image"),
                ) as fetch_sprites:
                    with mock.patch(
                        "qfit.mapbox_config.simplify_mapbox_style_expressions",
                        return_value={"version": 8, "layers": [{"id": "road-label-z15-plus"}]},
                    ):
                        report = collect_label_settings(LabelSettingsConfig(token="token", output_root=Path("/tmp")))

        fetch_style.assert_called_once_with("token", "mapbox", "outdoors-v12")
        fetch_sprites.assert_called_once()
        self.assertEqual(report["qgis_converter_result"], "success")
        self.assertEqual(report["sprite_definition_count"], 1)
        self.assertFalse(report["sprite_context_loaded"])
        self.assertEqual(report["label_count"], 1)
        self.assertEqual(report["labels"][0]["base_style_layer_id"], "road-label")
        self.assertIsNone(FakeQgsApplication.instance())

    def test_summary_markdown_lists_label_settings(self):
        report = {
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "generated": "2026-05-18T08:22:00+00:00",
            "sprite_context_loaded": True,
            "sprite_definition_count": 440,
            "label_count": 1,
            "labels": [
                {
                    "base_style_layer_id": "contour-label",
                    "style_name": "contour-label",
                    "source_layer": "contour",
                    "field_name": "concat(\"ele\", ' m')",
                    "is_expression": True,
                    "priority": 3,
                    "placement": "Line",
                    "repeat_distance": 0.0,
                    "repeat_distance_unit": "Millimeters",
                    "display_all": False,
                    "obstacle": True,
                    "data_defined_property_keys": ["pipe|key"],
                }
            ],
        }

        markdown = build_summary_markdown(report)

        self.assertIn("# Mapbox Outdoors QGIS label settings — mapbox/outdoors-v12", markdown)
        self.assertIn("Converted label styles: 1", markdown)
        self.assertIn("Sprite context loaded: yes", markdown)
        self.assertIn("contour-label", markdown)
        self.assertIn("concat", markdown)
        self.assertIn("Millimeters", markdown)
        self.assertIn("pipe\\|key", markdown)

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
