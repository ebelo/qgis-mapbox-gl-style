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
    _append_qgis_contour_bbox_edge_difference_label_probe,
    _append_qgis_contour_bbox_edge_difference_source_style_label_probe,
    _append_qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe,
    _apply_labeling_probes,
    _convert_style_to_labeling,
    _data_defined_expression_issue_rows,
    _ensure_qgis_application,
    _fetch_sprite_resources,
    _geometry_generator_markdown_value,
    _label_settings_report,
    _label_style_summary_rows,
    _line_center_label_conversion_rows,
    _line_label_conversion_rows,
    _line_label_repeat_spacing_rows,
    _load_original_style,
    _postprocessed_label_records,
    _qgis_duplicate_label_controls,
    _qgis_pal_layer_property_names_by_value,
    _qgis_property_type_names_by_value,
    _qgis_text_background_enum_names_by_field,
    _road_shield_label_placement_rows,
    _source_label_control_omission_summary_rows,
    _source_label_control_summary_rows,
    _source_label_fanout_summary_rows,
    _source_label_unresolved_control_summary_rows,
    _symbol_placement_includes_line_center,
    _symbol_placement_includes_line,
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
    def __init__(self, keys, properties=None):
        self._keys = keys
        self._properties = properties or {}

    def propertyKeys(self):
        return self._keys

    def property(self, key):
        return self._properties.get(key)


class FakeProperty:
    def __init__(
        self,
        *,
        property_type,
        active=True,
        expression="",
        field="",
        static_value=None,
    ):
        self._property_type = property_type
        self._active = active
        self._expression = expression
        self._field = field
        self._static_value = static_value

    def propertyType(self):
        return self._property_type

    def isActive(self):
        return self._active

    def expressionString(self):
        return self._expression

    def field(self):
        return self._field

    def staticValue(self):
        return self._static_value


class FakeColor:
    def __init__(self, name):
        self._name = name

    def name(self):
        return self._name


class FakeSize:
    def __init__(self, width, height):
        self._width = width
        self._height = height

    def width(self):
        return self._width

    def height(self):
        return self._height


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


class FakeTextBackground:
    def __init__(self, *, type_value=None, size_type_value=None):
        self._type_value = type_value if type_value is not None else SimpleNamespace(name="ShapeRectangle")
        self._size_type_value = (
            size_type_value if size_type_value is not None else SimpleNamespace(name="SizeFixed")
        )

    def enabled(self):
        return True

    def type(self):
        return self._type_value

    def size(self):
        return FakeSize(4.4979166667, 2.6458333333)

    def sizeType(self):
        return self._size_type_value

    def sizeUnit(self):
        return SimpleNamespace(name="Millimeters")

    def fillColor(self):
        return FakeColor("#ffffff")

    def strokeColor(self):
        return FakeColor("#1d1f25")

    def strokeWidth(self):
        return 0.2

    def strokeWidthUnit(self):
        return SimpleNamespace(name="Millimeters")

    def opacity(self):
        return 1.0


class FakeTextFormat:
    def __init__(self, *, background=None):
        self._background = background if background is not None else FakeTextBackground()

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

    def background(self):
        return self._background


class FakePlacementSettings:
    def allowDegradedPlacement(self):
        return False

    def overlapHandling(self):
        return SimpleNamespace(name="PreventOverlap")


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
    geometryGenerator = "boundary($geometry)"
    geometryGeneratorEnabled = True
    geometryGeneratorType = SimpleNamespace(name="Line")
    maxCurvedCharAngleIn = 25.0
    maxCurvedCharAngleOut = -25.0
    overrunDistance = 0.0
    overrunDistanceUnit = SimpleNamespace(name="Millimeters")

    def dataDefinedProperties(self):
        return FakeProperties(
            [87, 50],
            {
                50: FakeProperty(
                    property_type=3,
                    expression='CASE WHEN length("ref") > 3 THEN 8 ELSE 5 END',
                ),
                87: FakeProperty(property_type=1, static_value=6),
            },
        )

    def format(self):
        return FakeTextFormat()

    def placementSettings(self):
        return FakePlacementSettings()


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


class FakeQgsPalLayerSettings:
    class Property(int):
        pass

    ShapeSizeX = Property(50)
    Priority = Property(87)


class FakeQgsTextBackgroundSettings:
    ShapeRectangle = 0
    ShapeMarkerSymbol = 5
    ShapeType = object()
    SizeBuffer = 0
    SizeFixed = 1
    SizeType = object()


class FakeQgsProperty:
    InvalidProperty = 0
    StaticProperty = 1
    FieldBasedProperty = 2
    ExpressionBasedProperty = 3


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
    core_module.QgsPalLayerSettings = FakeQgsPalLayerSettings
    core_module.QgsProperty = FakeQgsProperty
    core_module.QgsTextBackgroundSettings = FakeQgsTextBackgroundSettings
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
            {50: "ShapeSizeX"},
            property_type_names_by_value={
                1: "StaticProperty",
                3: "ExpressionBasedProperty",
            },
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
        self.assertEqual(record["geometry_generator"], "boundary($geometry)")
        self.assertTrue(record["geometry_generator_enabled"])
        self.assertEqual(record["geometry_generator_type"], "Line")
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
        self.assertTrue(record["background_enabled"])
        self.assertEqual(record["background_type"], "ShapeRectangle")
        self.assertEqual(record["background_size"], {"width": 4.4979166667, "height": 2.6458333333})
        self.assertEqual(record["background_size_type"], "SizeFixed")
        self.assertEqual(record["background_size_unit"], "Millimeters")
        self.assertEqual(record["background_fill_color"], "#ffffff")
        self.assertEqual(record["background_stroke_color"], "#1d1f25")
        self.assertEqual(record["background_stroke_width"], 0.2)
        self.assertEqual(record["background_stroke_width_unit"], "Millimeters")
        self.assertEqual(record["background_opacity"], 1.0)
        self.assertFalse(record["allow_degraded_placement"])
        self.assertEqual(record["overlap_handling"], "PreventOverlap")
        self.assertEqual(record["data_defined_property_keys"], [50, 87])
        self.assertEqual(record["data_defined_property_names"], ["ShapeSizeX", "87"])
        self.assertEqual(record["data_defined_property_labels"], ["ShapeSizeX (50)", "87"])
        self.assertEqual(
            record["data_defined_property_details"],
            [
                {
                    "key": 50,
                    "name": "ShapeSizeX",
                    "label": "ShapeSizeX (50)",
                    "active": True,
                    "property_type": "ExpressionBasedProperty",
                    "expression": 'CASE WHEN length("ref") > 3 THEN 8 ELSE 5 END',
                    "field": None,
                    "static_value": None,
                },
                {
                    "key": 87,
                    "name": "87",
                    "label": "87",
                    "active": True,
                    "property_type": "StaticProperty",
                    "expression": None,
                    "field": None,
                    "static_value": 6,
                },
            ],
        )

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

    def test_qgis_duplicate_label_controls_report_runtime_support(self):
        class FakeQgis:
            QGIS_VERSION = "3.44.0"

        class FakePalLayerSettings:
            RemoveDuplicateLabels = 1
            RemoveDuplicateLabelDistance = 2

        controls = _qgis_duplicate_label_controls(FakePalLayerSettings, FakeQgis)

        self.assertEqual(controls["qgis_version"], "3.44.0")
        self.assertTrue(controls["remove_duplicate_labels"])
        self.assertTrue(controls["remove_duplicate_label_distance"])
        self.assertFalse(controls["label_margin_distance"])

    def test_qgis_pal_layer_property_names_by_value_reports_enum_names(self):
        names = _qgis_pal_layer_property_names_by_value(FakeQgsPalLayerSettings)

        self.assertEqual(names[50], "ShapeSizeX")
        self.assertEqual(names[87], "Priority")

    def test_qgis_text_background_enum_names_by_field_reports_runtime_enum_names(self):
        names = _qgis_text_background_enum_names_by_field(FakeQgsTextBackgroundSettings)

        self.assertEqual(names["type"][0], "ShapeRectangle")
        self.assertEqual(names["type"][5], "ShapeMarkerSymbol")
        self.assertEqual(names["size_type"][0], "SizeBuffer")
        self.assertEqual(names["size_type"][1], "SizeFixed")

    def test_qgis_property_type_names_by_value_reports_enum_names(self):
        names = _qgis_property_type_names_by_value(FakeQgsProperty)

        self.assertEqual(names[0], "InvalidProperty")
        self.assertEqual(names[1], "StaticProperty")
        self.assertEqual(names[2], "FieldBasedProperty")
        self.assertEqual(names[3], "ExpressionBasedProperty")

    def test_label_settings_record_decodes_numeric_background_enums(self):
        class NumericBackgroundSettings(FakeSettings):
            def format(self):
                return FakeTextFormat(
                    background=FakeTextBackground(
                        type_value=5,
                        size_type_value=1,
                    )
                )

        record = label_settings_record(
            FakeStyle(style_name="road-number-shield-2-remaining-icons-z11-plus", layer_name="road"),
            NumericBackgroundSettings(),
            {50: "ShapeSizeX"},
            {
                "type": {5: "ShapeMarkerSymbol"},
                "size_type": {1: "SizeFixed"},
            },
        )

        self.assertEqual(record["background_type"], "ShapeMarkerSymbol")
        self.assertEqual(record["background_size_type"], "SizeFixed")

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
            {50: "ShapeSizeX", 87: "Priority"},
        )

        self.assertEqual(FakeBackgroundMapService.applied_count, 1)
        self.assertIsInstance(FakeBackgroundMapService.labeling, FakeLabeling)
        self.assertEqual([record["base_style_layer_id"] for record in records], ["road-label", "waterway-label"])
        self.assertEqual(records[0]["data_defined_property_names"], ["ShapeSizeX", "Priority"])
        self.assertEqual(_postprocessed_label_records(None, fake_apply_label_priority), [])

    def test_postprocessed_label_records_forwards_background_enum_names(self):
        class NumericBackgroundSettings(FakeSettings):
            def format(self):
                return FakeTextFormat(background=FakeTextBackground(type_value=5, size_type_value=1))

        records = _postprocessed_label_records(
            FakeLabeling(
                [
                    FakeLabelStyle(
                        style_name="road-number-shield-2-remaining-icons-z11-plus",
                        layer_name="road",
                        settings=NumericBackgroundSettings(),
                    ),
                ]
            ),
            fake_apply_label_priority,
            {50: "ShapeSizeX", 87: "Priority"},
            {
                "type": {5: "ShapeMarkerSymbol"},
                "size_type": {1: "SizeFixed"},
            },
        )

        self.assertEqual(records[0]["background_type"], "ShapeMarkerSymbol")
        self.assertEqual(records[0]["background_size_type"], "SizeFixed")

    def test_apply_labeling_probes_appends_bbox_edge_probes_when_requested(self):
        original_labeling = FakeLabeling([])
        first_updated_labeling = FakeLabeling([])
        second_updated_labeling = FakeLabeling([])
        third_updated_labeling = FakeLabeling([])
        seen = {}

        def fake_append_probe(layer):
            seen["before"] = layer.labeling()
            layer.setLabeling(first_updated_labeling)
            layer.setLabelsEnabled(True)
            seen["labels_enabled_called"] = True

        def fake_append_source_style_probe(layer):
            seen["source_before"] = layer.labeling()
            layer.setLabeling(second_updated_labeling)

        def fake_append_source_style_high_zoom_probe(layer):
            seen["source_high_zoom_before"] = layer.labeling()
            layer.setLabeling(third_updated_labeling)

        with mock.patch(
            "qfit.validation.mapbox_outdoors_label_settings._append_qgis_contour_bbox_edge_difference_label_probe",
            side_effect=fake_append_probe,
        ) as append_probe:
            with mock.patch(
                "qfit.validation.mapbox_outdoors_label_settings."
                "_append_qgis_contour_bbox_edge_difference_source_style_label_probe",
                side_effect=fake_append_source_style_probe,
            ) as append_source_style_probe:
                with mock.patch(
                    "qfit.validation.mapbox_outdoors_label_settings."
                    "_append_qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe",
                    side_effect=fake_append_source_style_high_zoom_probe,
                ) as append_source_style_high_zoom_probe:
                    result = _apply_labeling_probes(
                        original_labeling,
                        LabelSettingsConfig(
                            token="token",
                            output_root=Path("/tmp"),
                            qgis_contour_bbox_edge_difference_label_probe=True,
                            qgis_contour_bbox_edge_difference_source_style_label_probe=True,
                            qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe=True,
                        ),
                    )

        append_probe.assert_called_once()
        append_source_style_probe.assert_called_once()
        append_source_style_high_zoom_probe.assert_called_once()
        self.assertIs(seen["before"], original_labeling)
        self.assertIs(seen["source_before"], first_updated_labeling)
        self.assertIs(seen["source_high_zoom_before"], second_updated_labeling)
        self.assertTrue(seen["labels_enabled_called"])
        self.assertIs(result, third_updated_labeling)

    def test_apply_labeling_probes_skips_when_disabled_or_missing_labeling(self):
        labeling = FakeLabeling([])
        with mock.patch(
            "qfit.validation.mapbox_outdoors_label_settings._append_qgis_contour_bbox_edge_difference_label_probe"
        ) as append_probe:
            with mock.patch(
                "qfit.validation.mapbox_outdoors_label_settings."
                "_append_qgis_contour_bbox_edge_difference_source_style_label_probe"
            ) as append_source_style_probe:
                with mock.patch(
                    "qfit.validation.mapbox_outdoors_label_settings."
                    "_append_qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe"
                ) as append_source_style_high_zoom_probe:
                    self.assertIs(
                        _apply_labeling_probes(
                            labeling,
                            LabelSettingsConfig(token="token", output_root=Path("/tmp")),
                        ),
                        labeling,
                    )
                    self.assertIs(
                        _apply_labeling_probes(
                            None,
                            LabelSettingsConfig(
                                token="token",
                                output_root=Path("/tmp"),
                                qgis_contour_bbox_edge_difference_label_probe=True,
                                qgis_contour_bbox_edge_difference_source_style_label_probe=True,
                                qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe=True,
                            ),
                        ),
                        None,
                    )

        append_probe.assert_not_called()
        append_source_style_probe.assert_not_called()
        append_source_style_high_zoom_probe.assert_not_called()

    def test_append_qgis_contour_bbox_edge_difference_label_probe_delegates_to_comparison_helper(self):
        layer = object()
        with mock.patch(
            "qfit.validation.mapbox_outdoors_comparison._append_qgis_contour_bbox_edge_difference_label_probe"
        ) as append_probe:
            _append_qgis_contour_bbox_edge_difference_label_probe(layer)

        append_probe.assert_called_once_with(layer)

    def test_append_qgis_contour_bbox_edge_difference_source_style_probe_delegates_to_comparison_helper(self):
        layer = object()
        with mock.patch(
            "qfit.validation.mapbox_outdoors_comparison."
            "_append_qgis_contour_bbox_edge_difference_source_style_label_probe"
        ) as append_probe:
            _append_qgis_contour_bbox_edge_difference_source_style_label_probe(layer)

        append_probe.assert_called_once_with(layer)

    def test_append_qgis_contour_bbox_edge_difference_source_style_high_zoom_probe_delegates_to_comparison_helper(self):
        layer = object()
        with mock.patch(
            "qfit.validation.mapbox_outdoors_comparison."
            "_append_qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe"
        ) as append_probe:
            _append_qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe(layer)

        append_probe.assert_called_once_with(layer)

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
            records=[
                {
                    "base_style_layer_id": "contour-label",
                    "style_name": "contour-label",
                    "data_defined_property_details": [
                        {
                            "label": "ShapeSizeX (50)",
                            "property_type": "ExpressionBasedProperty",
                            "expression": 'CASE WHEN "shield" IN () THEN 5 ELSE 5 END',
                        }
                    ],
                }
            ],
            source_label_layers=[{"base_style_layer_id": "contour-label"}],
        )

        self.assertEqual(report["style_owner"], "mapbox")
        self.assertEqual(report["style_id"], "outdoors-v12")
        self.assertEqual(report["qgis_converter_result"], "success")
        self.assertTrue(report["sprite_context_loaded"])
        self.assertEqual(report["sprite_definition_count"], 440)
        self.assertEqual(report["qgis_duplicate_label_controls"], {})
        self.assertEqual(report["label_count"], 1)
        self.assertEqual(report["label_style_summary_by_base_layer"][0]["base_style_layer_id"], "contour-label")
        self.assertEqual(report["label_style_summary_by_base_layer"][0]["count"], 1)
        self.assertEqual(report["line_label_repeat_spacing_by_base_layer"], [])
        self.assertEqual(
            report["data_defined_expression_issue_rows"],
            [
                {
                    "base_style_layer_id": "contour-label",
                    "style_name": "contour-label",
                    "property": "ShapeSizeX (50)",
                    "property_type": "ExpressionBasedProperty",
                    "empty_in_clause_count": 1,
                    "expression_length": 42,
                }
            ],
        )
        self.assertEqual(report["source_label_fanout_by_base_layer"][0]["base_style_layer_id"], "contour-label")
        self.assertEqual(report["source_label_fanout_by_base_layer"][0]["converted_label_styles"], 1)
        self.assertEqual(report["source_label_control_summary_by_base_layer"][0]["base_style_layer_id"], "contour-label")
        self.assertEqual(report["source_label_control_summary_by_base_layer"][0]["source_label_rows"], 1)
        self.assertEqual(report["source_label_control_omission_summary_by_base_layer"], [])
        self.assertEqual(report["source_label_unresolved_control_summary_by_base_layer"], [])
        self.assertEqual(report["source_label_layer_count"], 1)

    def test_data_defined_expression_issue_rows_count_empty_in_clauses(self):
        empty_expression = (
            'CASE WHEN "shield" IN () THEN 5 '
            'WHEN "shield" in() THEN 6 '
            'WHEN "shield" IN (   ) THEN 7 ELSE 5 END'
        )
        rows = _data_defined_expression_issue_rows(
            [
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-2-remaining-icons-z11-plus",
                    "data_defined_property_details": [
                        {
                            "label": "ShapeSizeX (50)",
                            "property_type": "ExpressionBasedProperty",
                            "expression": empty_expression,
                        },
                        {
                            "label": "Priority (87)",
                            "property_type": "ExpressionBasedProperty",
                            "expression": 'CASE WHEN "class" IN (\'primary\') THEN 6 ELSE 3 END',
                        },
                    ],
                },
                {
                    "base_style_layer_id": "road-label",
                    "style_name": "road-label-z15-plus",
                    "data_defined_property_details": [{"label": "Priority (87)", "static_value": 0}],
                },
            ]
        )

        self.assertEqual(
            rows,
            [
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-2-remaining-icons-z11-plus",
                    "property": "ShapeSizeX (50)",
                    "property_type": "ExpressionBasedProperty",
                    "empty_in_clause_count": 3,
                    "expression_length": len(empty_expression),
                }
            ],
        )

    def test_label_style_summary_groups_density_relevant_settings(self):
        rows = _label_style_summary_rows(
            [
                {
                    "base_style_layer_id": "road-label",
                    "source_layer": "road",
                    "geometry_type": "Line",
                    "priority": 4,
                    "placement": "Curved",
                    "repeat_distance": 39.6875,
                    "display_all": False,
                    "obstacle": True,
                    "allow_degraded_placement": False,
                    "overlap_handling": "PreventOverlap",
                    "label_per_part": False,
                    "merge_lines": False,
                },
                {
                    "base_style_layer_id": "road-label",
                    "source_layer": "road",
                    "geometry_type": "Line",
                    "priority": 4,
                    "placement": "Curved",
                    "repeat_distance": 66.1458333333,
                    "display_all": False,
                    "obstacle": True,
                    "allow_degraded_placement": False,
                    "overlap_handling": "PreventOverlap",
                    "label_per_part": False,
                    "merge_lines": False,
                },
                {
                    "base_style_layer_id": "poi-label",
                    "source_layer": "poi_label",
                    "geometry_type": "Point",
                    "priority": 5,
                    "placement": "OverPoint",
                    "repeat_distance": 0.0,
                    "display_all": False,
                    "obstacle": True,
                    "allow_degraded_placement": False,
                    "overlap_handling": "PreventOverlap",
                    "label_per_part": False,
                    "merge_lines": False,
                },
            ]
        )

        self.assertEqual([row["base_style_layer_id"] for row in rows], ["road-label", "poi-label"])
        road_row = rows[0]
        self.assertEqual(road_row["count"], 2)
        self.assertEqual(road_row["geometry_types"], {"Line": 2})
        self.assertEqual(road_row["priorities"], {"4": 2})
        self.assertEqual(road_row["repeat_distances"], {"39.6875": 1, "66.1458": 1})
        self.assertEqual(road_row["display_all"], {"false": 2})
        self.assertEqual(road_row["allow_degraded_placement"], {"false": 2})
        self.assertEqual(road_row["overlap_handling"], {"PreventOverlap": 2})

    def test_line_label_repeat_spacing_summary_highlights_missing_spacing_and_zero_repeat(self):
        rows = _line_label_repeat_spacing_rows(
            [
                {
                    "base_style_layer_id": "contour-label",
                    "style_name": "contour-label",
                    "qfit_style_layer_id": "contour-label",
                    "layout": {"symbol-placement": "line"},
                    "qfit_layout": {"symbol-placement": "line"},
                },
                {
                    "base_style_layer_id": "waterway-label",
                    "style_name": "waterway-label-z17-plus",
                    "qfit_style_layer_id": "waterway-label-z17-plus",
                    "layout": {"symbol-placement": "line", "symbol-spacing": ["interpolate"]},
                    "qfit_layout": {"symbol-placement": "line", "symbol-spacing": 400.0},
                },
                {
                    "base_style_layer_id": "water-line-label",
                    "style_name": "water-line-label",
                    "qfit_style_layer_id": "water-line-label",
                    "layout": {"symbol-placement": "line-center"},
                    "qfit_layout": {"symbol-placement": "line-center"},
                },
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-z11-plus",
                    "qfit_style_layer_id": "road-number-shield-z11-plus",
                    "layout": {"symbol-placement": "line", "icon-image": ["get", "shield"]},
                    "qfit_layout": {"symbol-placement": "line", "icon-image": "default-2"},
                },
            ],
            [
                {
                    "base_style_layer_id": "contour-label",
                    "style_name": "contour-label",
                    "geometry_type": "Line",
                    "placement": "Curved",
                    "repeat_distance": 0.0,
                },
                {
                    "base_style_layer_id": "waterway-label",
                    "style_name": "waterway-label-z17-plus",
                    "geometry_type": "Line",
                    "placement": "Curved",
                    "repeat_distance": 66.1458333333,
                },
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-z11-plus",
                    "geometry_type": "Line",
                    "placement": "Horizontal",
                    "repeat_distance": 0.0,
                },
            ],
        )

        self.assertEqual([row["base_style_layer_id"] for row in rows], ["contour-label", "waterway-label"])
        contour_row = rows[0]
        self.assertEqual(contour_row["missing_qfit_symbol_spacing"], 1)
        self.assertEqual(contour_row["zero_repeat_distance_count"], 1)
        self.assertEqual(contour_row["qfit_symbol_spacings"], {"(missing)": 1})
        self.assertEqual(contour_row["repeat_distances"], {"0": 1})
        waterway_row = rows[1]
        self.assertEqual(waterway_row["missing_qfit_symbol_spacing"], 0)
        self.assertEqual(waterway_row["zero_repeat_distance_count"], 0)
        self.assertEqual(waterway_row["qfit_symbol_spacings"], {"400": 1})
        self.assertEqual(waterway_row["repeat_distances"], {"66.1458": 1})
        self.assertEqual(waterway_row["qfit_symbol_spacing_to_repeat_distances"], {"400 -> 66.1458": 1})

    def test_line_label_repeat_spacing_summary_deduplicates_shared_label_records(self):
        rows = _line_label_repeat_spacing_rows(
            [
                {
                    "base_style_layer_id": "road-label",
                    "style_name": "road-label-alias",
                    "qfit_style_layer_id": "road-label-z15-plus",
                    "qfit_layout": {"symbol-placement": "line"},
                },
                {
                    "base_style_layer_id": "road-label",
                    "style_name": "road-label-z15-plus",
                    "qfit_style_layer_id": "road-label-z15-plus",
                    "qfit_layout": {"symbol-placement": "line"},
                },
            ],
            [
                {
                    "base_style_layer_id": "road-label",
                    "style_name": "road-label-z15-plus",
                    "geometry_type": "Line",
                    "placement": "Curved",
                    "repeat_distance": 66.1458333333,
                },
            ],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_label_rows"], 2)
        self.assertEqual(rows[0]["converted_line_label_styles"], 1)
        self.assertEqual(rows[0]["repeat_distances"], {"66.1458": 1})
        self.assertEqual(rows[0]["qfit_symbol_spacing_to_repeat_distances"], {"(missing) -> 66.1458": 2})

    def test_line_label_conversion_summary_includes_shields_and_line_center_labels(self):
        rows = _line_label_conversion_rows(
            [
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-z11-plus",
                    "qfit_style_layer_id": "road-number-shield-z11-plus",
                    "layout": {"symbol-placement": "line"},
                    "qfit_layout": {"symbol-placement": "line", "icon-image": "default-2"},
                },
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-below-z11",
                    "qfit_style_layer_id": "road-number-shield-below-z11",
                    "layout": {"symbol-placement": ["step", ["zoom"], "point", 11, "line"]},
                    "qfit_layout": {"symbol-placement": "point", "icon-image": "default-2"},
                },
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-low-zoom",
                    "qfit_style_layer_id": "road-number-shield-low-zoom",
                    "maxzoom": 11,
                    "layout": {"symbol-placement": ["step", ["zoom"], "point", 11, "line"]},
                    "qfit_layout": {"symbol-placement": "point", "icon-image": "default-2"},
                },
                {
                    "base_style_layer_id": "ferry-aerialway-label",
                    "style_name": "ferry-aerialway-label",
                    "qfit_style_layer_id": "ferry-aerialway-label",
                    "layout": {"symbol-placement": ["literal", "line"]},
                    "qfit_layout": {"symbol-placement": "point"},
                },
                {
                    "base_style_layer_id": "water-line-label",
                    "style_name": "water-line-label",
                    "qfit_style_layer_id": "water-line-label",
                    "layout": {"symbol-placement": "line-center"},
                    "qfit_layout": {"symbol-placement": "line-center"},
                },
                {
                    "base_style_layer_id": "poi-label",
                    "style_name": "poi-label",
                    "qfit_style_layer_id": "poi-label",
                    "layout": {
                        "symbol-placement": [
                            "match",
                            ["get", "class"],
                            "line",
                            "point",
                            "point",
                        ]
                    },
                    "qfit_layout": {"symbol-placement": "point"},
                },
            ],
            [
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-z11-plus",
                    "geometry_type": "Line",
                    "placement": "Horizontal",
                    "repeat_distance": 0.0,
                    "label_per_part": False,
                    "merge_lines": True,
                },
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-below-z11",
                    "geometry_type": "Point",
                    "placement": "OverPoint",
                    "repeat_distance": 0.0,
                    "label_per_part": False,
                    "merge_lines": False,
                },
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-low-zoom",
                    "geometry_type": "Point",
                    "placement": "OverPoint",
                    "repeat_distance": 0.0,
                    "label_per_part": False,
                    "merge_lines": False,
                },
                {
                    "base_style_layer_id": "ferry-aerialway-label",
                    "style_name": "ferry-aerialway-label",
                    "geometry_type": "Line",
                    "placement": "Curved",
                    "repeat_distance": 66.1458333333,
                    "label_per_part": False,
                    "merge_lines": True,
                },
                {
                    "base_style_layer_id": "water-line-label",
                    "style_name": "water-line-label",
                    "geometry_type": "Line",
                    "placement": "Line",
                    "repeat_distance": 0.0,
                    "label_per_part": False,
                    "merge_lines": False,
                },
                {
                    "base_style_layer_id": "poi-label",
                    "style_name": "poi-label",
                    "geometry_type": "Point",
                    "placement": "OverPoint",
                    "repeat_distance": 0.0,
                    "label_per_part": False,
                    "merge_lines": False,
                },
            ],
        )

        self.assertEqual(
            [row["base_style_layer_id"] for row in rows],
            ["road-number-shield", "ferry-aerialway-label", "water-line-label"],
        )
        shield_row = rows[0]
        self.assertEqual(shield_row["source_label_rows"], 2)
        self.assertEqual(shield_row["converted_label_styles"], 2)
        self.assertEqual(shield_row["qfit_symbol_placements"], {"line": 1, "point": 1})
        self.assertEqual(shield_row["converted_geometry_types"], {"Line": 1, "Point": 1})
        self.assertEqual(shield_row["converted_placements"], {"Horizontal": 1, "OverPoint": 1})
        self.assertEqual(shield_row["repeat_distances"], {"0": 1})
        self.assertEqual(shield_row["merge_lines"], {"false": 1, "true": 1})
        ferry_row = rows[1]
        self.assertEqual(ferry_row["source_symbol_placements"], {'["literal","line"]': 1})
        self.assertEqual(ferry_row["repeat_distances"], {"66.1458": 1})
        water_row = rows[2]
        self.assertEqual(water_row["source_symbol_placements"], {"line-center": 1})
        self.assertEqual(water_row["converted_geometry_types"], {"Line": 1})
        self.assertEqual(water_row["converted_placements"], {"Line": 1})
        self.assertEqual(water_row["repeat_distances"], {"0": 1})
        self.assertEqual(water_row["merge_lines"], {"false": 1})

    def test_road_shield_label_placement_rows_focus_converted_shields(self):
        rows = _road_shield_label_placement_rows(
            [
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-2-remaining-icons-z11-plus",
                    "qfit_style_layer_id": "road-number-shield-2-remaining-icons-z11-plus",
                    "minzoom": 6,
                    "qfit_minzoom": 11,
                    "layout": {"symbol-placement": ["step", ["zoom"], "point", 11, "line"]},
                    "qfit_layout": {"symbol-placement": "line", "symbol-spacing": 466.6666666667},
                },
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-2-remaining-icons-below-z11",
                    "qfit_style_layer_id": "road-number-shield-2-remaining-icons-below-z11",
                    "minzoom": 6,
                    "qfit_minzoom": 6,
                    "qfit_maxzoom": 11,
                    "layout": {"symbol-placement": ["step", ["zoom"], "point", 11, "line"]},
                    "qfit_layout": {"symbol-placement": "point"},
                },
                {
                    "base_style_layer_id": "road-label",
                    "style_name": "road-label-z15-plus",
                    "qfit_style_layer_id": "road-label-z15-plus",
                    "qfit_layout": {"symbol-placement": "line"},
                },
            ],
            [
                {
                    "style_name": "road-number-shield-2-remaining-icons-z11-plus",
                    "geometry_type": "Line",
                    "placement": "Horizontal",
                    "priority": 6,
                    "repeat_distance": 123.4722222222,
                    "display_all": False,
                    "obstacle": True,
                    "allow_degraded_placement": False,
                    "overlap_handling": "PreventOverlap",
                    "label_per_part": False,
                    "merge_lines": True,
                    "text_size": 2.38125,
                    "text_size_unit": "Millimeters",
                    "text_color": "#1d1f25",
                    "background_enabled": True,
                    "background_type": "ShapeRectangle",
                    "background_size": {"width": 4.4979166667, "height": 2.6458333333},
                    "background_size_type": "SizeFixed",
                    "background_size_unit": "Millimeters",
                    "background_fill_color": "#ffffff",
                    "background_stroke_color": "#1d1f25",
                    "background_stroke_width": 0.2,
                    "background_stroke_width_unit": "Millimeters",
                    "background_opacity": 1.0,
                    "data_defined_property_keys": [50],
                    "data_defined_property_labels": ["ShapeSizeX (50)"],
                    "data_defined_property_details": [
                        {
                            "key": 50,
                            "name": "ShapeSizeX",
                            "label": "ShapeSizeX (50)",
                            "active": True,
                            "property_type": "ExpressionBasedProperty",
                            "expression": 'CASE WHEN length("ref") > 3 THEN 8 ELSE 5 END',
                            "field": None,
                            "static_value": None,
                        }
                    ],
                },
                {
                    "style_name": "road-number-shield-2-remaining-icons-below-z11",
                    "geometry_type": "Point",
                    "placement": "OverPoint",
                    "priority": 3,
                    "repeat_distance": 0.0,
                    "display_all": False,
                    "obstacle": True,
                    "allow_degraded_placement": False,
                    "overlap_handling": "PreventOverlap",
                    "label_per_part": False,
                    "merge_lines": False,
                    "text_color": "#1d1f25",
                },
                {"style_name": "road-label-z15-plus"},
            ],
        )

        self.assertEqual(
            [row["style_name"] for row in rows],
            [
                "road-number-shield-2-remaining-icons-below-z11",
                "road-number-shield-2-remaining-icons-z11-plus",
            ],
        )
        below, z11_plus = rows
        self.assertEqual(below["qfit_zoom"], "6 to 11")
        self.assertEqual(below["source_zoom"], "6+")
        self.assertEqual(below["qfit_symbol_placement"], "point")
        self.assertEqual(below["priority"], 3)
        self.assertFalse(below["display_all"])
        self.assertFalse(below["allow_degraded_placement"])
        self.assertEqual(below["overlap_handling"], "PreventOverlap")
        self.assertFalse(below["label_per_part"])
        self.assertEqual(z11_plus["qfit_zoom"], "11+")
        self.assertEqual(z11_plus["qfit_symbol_placement"], "line")
        self.assertAlmostEqual(z11_plus["qfit_symbol_spacing"], 466.6666666667)
        self.assertEqual(z11_plus["priority"], 6)
        self.assertFalse(z11_plus["display_all"])
        self.assertFalse(z11_plus["allow_degraded_placement"])
        self.assertEqual(z11_plus["overlap_handling"], "PreventOverlap")
        self.assertFalse(z11_plus["label_per_part"])
        self.assertTrue(z11_plus["merge_lines"])
        self.assertAlmostEqual(z11_plus["text_size"], 2.38125)
        self.assertEqual(z11_plus["text_size_unit"], "Millimeters")
        self.assertEqual(z11_plus["background_type"], "ShapeRectangle")
        self.assertEqual(z11_plus["background_size"], {"width": 4.4979166667, "height": 2.6458333333})
        self.assertEqual(z11_plus["background_fill_color"], "#ffffff")
        self.assertEqual(z11_plus["background_stroke_color"], "#1d1f25")
        self.assertEqual(z11_plus["background_stroke_width"], 0.2)
        self.assertEqual(z11_plus["data_defined_property_keys"], [50])
        self.assertEqual(z11_plus["data_defined_property_labels"], ["ShapeSizeX (50)"])
        self.assertEqual(
            z11_plus["data_defined_property_details"][0]["expression"],
            'CASE WHEN length("ref") > 3 THEN 8 ELSE 5 END',
        )

    def test_line_center_label_conversion_summary_isolates_line_center_labels(self):
        rows = _line_center_label_conversion_rows(
            [
                {
                    "base_style_layer_id": "water-line-label",
                    "style_name": "water-line-label-ocean-name",
                    "qfit_style_layer_id": "water-line-label-ocean-name",
                    "source_layer": "natural_label",
                    "layout": {"symbol-placement": "line-center"},
                    "qfit_layout": {"symbol-placement": "line-center"},
                },
                {
                    "base_style_layer_id": "natural-line-label",
                    "style_name": "natural-line-label",
                    "qfit_style_layer_id": "natural-line-label",
                    "source_layer": "natural_label",
                    "layout": {"symbol-placement": ["coalesce", ["literal", None], "line-center"]},
                    "qfit_layout": {"symbol-placement": ["coalesce", ["literal", None], "line-center"]},
                },
                {
                    "base_style_layer_id": "road-label",
                    "style_name": "road-label",
                    "qfit_style_layer_id": "road-label",
                    "source_layer": "road",
                    "layout": {"symbol-placement": "line"},
                    "qfit_layout": {"symbol-placement": "line"},
                },
                {
                    "base_style_layer_id": "qfit-only-line-center-label",
                    "style_name": "qfit-only-line-center-label",
                    "qfit_style_layer_id": "qfit-only-line-center-label",
                    "source_layer": "road",
                    "layout": {"symbol-placement": "line"},
                    "qfit_layout": {"symbol-placement": "line-center"},
                },
            ],
            [
                {
                    "base_style_layer_id": "water-line-label",
                    "style_name": "water-line-label-ocean-name",
                    "geometry_type": "Line",
                    "placement": "Line",
                    "repeat_distance": 0.0,
                    "label_per_part": False,
                    "merge_lines": False,
                },
                {
                    "base_style_layer_id": "natural-line-label",
                    "style_name": "natural-line-label",
                    "geometry_type": "Line",
                    "placement": "Line",
                    "repeat_distance": 0.0,
                    "label_per_part": False,
                    "merge_lines": False,
                },
                {
                    "base_style_layer_id": "road-label",
                    "style_name": "road-label",
                    "geometry_type": "Line",
                    "placement": "Curved",
                    "repeat_distance": 39.6875,
                    "label_per_part": False,
                    "merge_lines": True,
                },
            ],
        )

        self.assertEqual(
            [row["base_style_layer_id"] for row in rows],
            ["natural-line-label", "water-line-label"],
        )
        natural_row = rows[0]
        self.assertEqual(
            natural_row["source_symbol_placements"],
            {'["coalesce",["literal",null],"line-center"]': 1},
        )
        self.assertEqual(
            natural_row["qfit_symbol_placements"],
            {'["coalesce",["literal",null],"line-center"]': 1},
        )
        water_row = rows[1]
        self.assertEqual(water_row["source_layers"], {"natural_label": 1})
        self.assertEqual(water_row["source_symbol_placements"], {"line-center": 1})
        self.assertEqual(water_row["qfit_symbol_placements"], {"line-center": 1})
        self.assertEqual(water_row["converted_geometry_types"], {"Line": 1})
        self.assertEqual(water_row["converted_placements"], {"Line": 1})
        self.assertEqual(water_row["repeat_distances"], {"0": 1})
        self.assertEqual(water_row["merge_lines"], {"false": 1})

    def test_symbol_placement_line_detection_uses_expression_outputs_and_zoom_bounds(self):
        self.assertTrue(_symbol_placement_includes_line(["literal", "line"]))
        self.assertTrue(
            _symbol_placement_includes_line(["coalesce", ["get", "placement"], "line-center"])
        )
        self.assertFalse(_symbol_placement_includes_line(["coalesce", "point", "line"]))
        self.assertFalse(
            _symbol_placement_includes_line(["coalesce", ["literal", "point"], "line-center"])
        )
        self.assertTrue(
            _symbol_placement_includes_line(["coalesce", ["literal", None], "line-center"])
        )
        self.assertTrue(
            _symbol_placement_includes_line(
                ["let", "placement", "line", ["var", "placement"]]
            )
        )
        self.assertFalse(
            _symbol_placement_includes_line(
                ["match", ["get", "class"], "line", "point", "point"]
            )
        )
        self.assertFalse(
            _symbol_placement_includes_line(
                ["step", ["zoom"], "point", 11, "line"], max_zoom=11
            )
        )
        self.assertTrue(
            _symbol_placement_includes_line(
                ["step", ["zoom"], "point", 11, "line"], min_zoom=11
            )
        )
        self.assertTrue(_symbol_placement_includes_line_center("line-center"))
        self.assertFalse(_symbol_placement_includes_line_center("line"))
        self.assertTrue(
            _symbol_placement_includes_line_center(
                ["coalesce", ["literal", None], "line-center"]
            )
        )
        self.assertFalse(
            _symbol_placement_includes_line_center(
                ["step", ["zoom"], "point", 11, "line-center"], max_zoom=11
            )
        )
        self.assertTrue(
            _symbol_placement_includes_line_center(
                ["step", ["zoom"], "point", 11, "line-center"], min_zoom=11
            )
        )

    def test_source_label_fanout_summary_groups_qfit_style_expansion(self):
        rows = _source_label_fanout_summary_rows(
            [
                {
                    "base_style_layer_id": "settlement-major-label",
                    "style_name": "settlement-major-label-z7-name",
                    "qfit_style_layer_id": "settlement-major-label-z7-name",
                    "source_layer": "place_label",
                    "minzoom": 2,
                    "maxzoom": 13,
                    "qfit_minzoom": 7,
                    "qfit_maxzoom": 8,
                },
                {
                    "base_style_layer_id": "settlement-major-label",
                    "style_name": "settlement-major-label-z7-name-en",
                    "qfit_style_layer_id": "settlement-major-label-z7-name-en",
                    "source_layer": "place_label",
                    "minzoom": 2,
                    "maxzoom": 13,
                    "qfit_minzoom": 7,
                    "qfit_maxzoom": 8,
                },
                {
                    "base_style_layer_id": "poi-label",
                    "style_name": "poi-label",
                    "qfit_style_layer_id": None,
                    "source_layer": "poi_label",
                    "minzoom": 15,
                    "maxzoom": None,
                },
            ],
            [
                {
                    "base_style_layer_id": "settlement-major-label",
                    "style_name": "settlement-major-label-z7-name",
                    "field_name": '"name"',
                },
                {
                    "base_style_layer_id": "settlement-major-label",
                    "style_name": "settlement-major-label-z7-name-en",
                    "field_name": '"name_en"',
                },
                {"base_style_layer_id": "poi-label", "style_name": "poi-label", "field_name": '"name"'},
            ],
        )

        self.assertEqual([row["base_style_layer_id"] for row in rows], ["settlement-major-label", "poi-label"])
        settlement_row = rows[0]
        self.assertEqual(settlement_row["source_label_rows"], 2)
        self.assertEqual(settlement_row["converted_label_styles"], 2)
        self.assertEqual(settlement_row["qfit_layer_count"], 2)
        self.assertEqual(settlement_row["source_layers"], {"place_label": 2})
        self.assertEqual(settlement_row["source_zooms"], {"2 to 13": 2})
        self.assertEqual(settlement_row["qfit_zooms"], {"7 to 8": 2})
        self.assertEqual(settlement_row["field_names"], {'"name"': 1, '"name_en"': 1})
        self.assertEqual(rows[1]["qfit_zooms"], {"(missing)": 1})

    def test_source_label_control_summary_groups_missing_qfit_controls(self):
        rows = _source_label_control_summary_rows(
            [
                {
                    "base_style_layer_id": "settlement-major-label",
                    "layout": {
                        "symbol-sort-key": ["get", "symbolrank"],
                        "text-anchor": ["get", "text_anchor"],
                        "text-field": ["get", "name"],
                    },
                    "paint": {"text-color": "#111111", "text-halo-color": "#ffffff"},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {"text-color": "#111111"},
                },
                {
                    "base_style_layer_id": "settlement-major-label",
                    "layout": {"text-field": ["get", "name_en"], "text-radial-offset": 0.6},
                    "paint": {"text-color": "#111111"},
                    "qfit_layout": {"text-field": ["get", "name_en"], "text-radial-offset": 0.6},
                    "qfit_paint": {},
                },
                {
                    "base_style_layer_id": "poi-label",
                    "layout": {"text-field": ["get", "name"]},
                    "paint": {"text-color": "#69575d"},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {"text-color": "#69575d"},
                },
            ]
        )

        self.assertEqual([row["base_style_layer_id"] for row in rows], ["settlement-major-label", "poi-label"])
        settlement_row = rows[0]
        self.assertEqual(settlement_row["source_label_rows"], 2)
        self.assertEqual(settlement_row["missing_control_count"], 4)
        self.assertEqual(
            settlement_row["source_layout_controls"],
            {"text-field": 2, "symbol-sort-key": 1, "text-anchor": 1, "text-radial-offset": 1},
        )
        self.assertEqual(settlement_row["qfit_layout_controls"], {"text-field": 2, "text-radial-offset": 1})
        self.assertEqual(settlement_row["missing_layout_controls"], {"symbol-sort-key": 1, "text-anchor": 1})
        self.assertEqual(settlement_row["source_paint_controls"], {"text-color": 2, "text-halo-color": 1})
        self.assertEqual(settlement_row["qfit_paint_controls"], {"text-color": 1})
        self.assertEqual(settlement_row["missing_paint_controls"], {"text-color": 1, "text-halo-color": 1})
        self.assertEqual(rows[1]["missing_control_count"], 0)

    def test_source_label_control_omission_summary_groups_known_qfit_omissions(self):
        rows = _source_label_control_omission_summary_rows(
            [
                {
                    "base_style_layer_id": "settlement-major-label",
                    "style_name": "settlement-major-label-z4-to-z6-dot-11-left",
                    "qfit_style_layer_id": "settlement-major-label-z4-to-z6-dot-11-left",
                    "layout": {"symbol-sort-key": ["get", "symbolrank"], "text-field": ["get", "name"]},
                    "paint": {},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {},
                },
                {
                    "base_style_layer_id": "country-label",
                    "style_name": "country-label",
                    "qfit_style_layer_id": "country-label",
                    "layout": {"icon-image": "", "text-field": ["get", "name"]},
                    "paint": {"icon-opacity": 0.6, "text-color": "#111111"},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {},
                },
                {
                    "base_style_layer_id": "poi-label",
                    "style_name": "poi-label-z17-plus-icon",
                    "qfit_style_layer_id": "poi-label-z17-plus-icon",
                    "qfit_filter": [">=", ["get", "sizerank"], 13.0],
                    "layout": {"icon-image": "restaurant", "text-field": ["get", "name"]},
                    "paint": {"icon-opacity": ["step", ["get", "sizerank"], 0, 13.0, 1]},
                    "qfit_layout": {"icon-image": "restaurant", "text-field": ["get", "name"]},
                    "qfit_paint": {},
                },
            ]
        )

        self.assertEqual([row["base_style_layer_id"] for row in rows], ["country-label", "poi-label", "settlement-major-label"])
        country_row = rows[0]
        self.assertEqual(country_row["omitted_control_count"], 2)
        self.assertEqual(country_row["omitted_controls"], {"layout.icon-image": 1, "paint.icon-opacity": 1})
        self.assertEqual(
            country_row["omission_reasons"],
            {"empty icon-image removed": 1, "icon-opacity removed with no QGIS icon": 1},
        )
        self.assertEqual(
            rows[1]["omission_reasons"],
            {"icon-opacity encoded by label visibility split": 1},
        )
        self.assertEqual(rows[2]["omission_reasons"], {"settlement symbol-sort-key encoded by qfit split": 1})

    def test_source_label_control_omission_summary_groups_icon_image_split_omissions(self):
        rows = _source_label_control_omission_summary_rows(
            [
                {
                    "base_style_layer_id": "poi-label",
                    "style_name": "poi-label-z17-plus-text",
                    "qfit_style_layer_id": "poi-label-z17-plus-text",
                    "qfit_filter": [">=", ["get", "sizerank"], 13.0],
                    "layout": {
                        "icon-image": ["image", ["get", "maki"]],
                        "text-field": ["get", "name"],
                    },
                    "paint": {},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {},
                },
                {
                    "base_style_layer_id": "settlement-minor-label",
                    "style_name": "settlement-minor-label-z8-to-z10-name",
                    "qfit_style_layer_id": "settlement-minor-label-z8-to-z10-name",
                    "qfit_minzoom": 8,
                    "layout": {
                        "icon-image": [
                            "step",
                            ["zoom"],
                            ["case", ["==", ["get", "capital"], 2], "border-dot-13", "dot-11"],
                            8,
                            "",
                        ],
                        "text-field": ["get", "name"],
                    },
                    "paint": {},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {},
                },
            ]
        )

        self.assertEqual([row["base_style_layer_id"] for row in rows], ["poi-label", "settlement-minor-label"])
        self.assertEqual(rows[0]["omission_reasons"], {"icon-image encoded by label visibility split": 1})
        self.assertEqual(rows[1]["omission_reasons"], {"settlement icon-image empty at qfit zoom split": 1})

    def test_source_label_unresolved_control_summary_keeps_non_zoom_empty_icon_fallbacks(self):
        rows = _source_label_unresolved_control_summary_rows(
            [
                {
                    "base_style_layer_id": "settlement-minor-label",
                    "style_name": "settlement-minor-label-z8-to-z10-name",
                    "qfit_style_layer_id": "settlement-minor-label-z8-to-z10-name",
                    "qfit_minzoom": 8,
                    "layout": {
                        "icon-image": ["case", ["==", ["get", "capital"], 2], "dot-11", ""],
                        "text-field": ["get", "name"],
                    },
                    "paint": {},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {},
                },
                {
                    "base_style_layer_id": "settlement-minor-label",
                    "style_name": "settlement-minor-label-z10-plus-name",
                    "qfit_style_layer_id": "settlement-minor-label-z10-plus-name",
                    "qfit_minzoom": 10,
                    "layout": {
                        "icon-image": ["step", ["zoom"], "dot-10", 8, "", 9, "dot-11"],
                        "text-field": ["get", "name"],
                    },
                    "paint": {},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {},
                }
            ]
        )

        self.assertEqual(rows[0]["base_style_layer_id"], "settlement-minor-label")
        self.assertEqual(rows[0]["unresolved_controls"], {"layout.icon-image": 2})

    def test_source_label_unresolved_control_summary_ignores_known_qfit_omissions(self):
        rows = _source_label_unresolved_control_summary_rows(
            [
                {
                    "base_style_layer_id": "settlement-major-label",
                    "style_name": "settlement-major-label-z8-plus-name",
                    "qfit_style_layer_id": "settlement-major-label-z8-plus-name",
                    "qfit_minzoom": 8,
                    "layout": {
                        "icon-image": [
                            "step",
                            ["zoom"],
                            ["case", ["==", ["get", "capital"], 2], "border-dot-13", "dot-11"],
                            8,
                            "",
                        ],
                        "symbol-sort-key": ["get", "symbolrank"],
                        "text-anchor": ["get", "text_anchor"],
                        "text-field": ["get", "name"],
                    },
                    "paint": {"text-color": "#111111", "text-halo-color": "#ffffff"},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {"text-color": "#111111"},
                },
                {
                    "base_style_layer_id": "country-label",
                    "style_name": "country-label",
                    "qfit_style_layer_id": "country-label",
                    "layout": {"icon-image": "", "text-field": ["get", "name"]},
                    "paint": {"icon-opacity": 0.6, "text-color": "#111111"},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {"text-color": "#111111"},
                },
                {
                    "base_style_layer_id": "poi-label",
                    "style_name": "poi-label-z16-plus-text",
                    "qfit_style_layer_id": "poi-label-z16-plus-text",
                    "qfit_filter": [">=", ["get", "sizerank"], 13.0],
                    "layout": {
                        "icon-image": ["image", ["get", "maki"]],
                        "text-field": ["get", "name"],
                    },
                    "paint": {"text-color": "#69575d", "text-halo-color": "#ffffff"},
                    "qfit_layout": {"text-field": ["get", "name"]},
                    "qfit_paint": {"text-color": "#69575d"},
                },
            ]
        )

        self.assertEqual([row["base_style_layer_id"] for row in rows], ["settlement-major-label", "poi-label"])
        self.assertEqual(rows[0]["unresolved_control_count"], 2)
        self.assertEqual(rows[0]["unresolved_controls"], {"layout.text-anchor": 1, "paint.text-halo-color": 1})
        self.assertEqual(rows[1]["unresolved_control_count"], 1)
        self.assertEqual(rows[1]["unresolved_controls"], {"paint.text-halo-color": 1})

    def test_collect_label_settings_runs_with_fake_qgis_runtime(self):
        bbox_expression = "line_merge(difference(boundary($geometry), boundary(bounds($geometry))))"

        def fake_append_probe(layer):
            class FakeProbeSettings(FakeSettings):
                geometryGenerator = bbox_expression
                geometryGeneratorEnabled = True
                geometryGeneratorType = SimpleNamespace(name="Line")

            styles = list(layer.labeling().styles())
            layer.setLabeling(
                FakeLabeling(
                    [
                        *styles,
                        FakeLabelStyle(
                            style_name="contour-label-bbox-edge-difference-probe",
                            layer_name="contour",
                            settings=FakeProbeSettings(),
                            geometry_type=SimpleNamespace(name="Polygon"),
                        ),
                    ]
                )
            )
            layer.setLabelsEnabled(True)

        def fake_append_source_style_probe(layer):
            class FakeProbeSettings(FakeSettings):
                geometryGenerator = bbox_expression
                geometryGeneratorEnabled = True
                geometryGeneratorType = SimpleNamespace(name="Line")

            styles = list(layer.labeling().styles())
            layer.setLabeling(
                FakeLabeling(
                    [
                        *styles,
                        FakeLabelStyle(
                            style_name="contour-label-bbox-edge-difference-source-style-probe",
                            layer_name="contour",
                            settings=FakeProbeSettings(),
                            geometry_type=SimpleNamespace(name="Polygon"),
                        ),
                    ]
                )
            )

        def fake_append_source_style_high_zoom_probe(layer):
            class FakeProbeSettings(FakeSettings):
                geometryGenerator = bbox_expression
                geometryGeneratorEnabled = True
                geometryGeneratorType = SimpleNamespace(name="Line")

            styles = list(layer.labeling().styles())
            layer.setLabeling(
                FakeLabeling(
                    [
                        *styles,
                        FakeLabelStyle(
                            style_name="contour-label-bbox-edge-difference-source-style-high-zoom-probe",
                            layer_name="contour",
                            settings=FakeProbeSettings(),
                            geometry_type=SimpleNamespace(name="Polygon"),
                        ),
                    ]
                )
            )

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
                        with mock.patch(
                            "qfit.validation.mapbox_outdoors_label_settings._append_qgis_contour_bbox_edge_difference_label_probe",
                            side_effect=fake_append_probe,
                        ) as append_probe:
                            with mock.patch(
                                "qfit.validation.mapbox_outdoors_label_settings."
                                "_append_qgis_contour_bbox_edge_difference_source_style_label_probe",
                                side_effect=fake_append_source_style_probe,
                            ) as append_source_style_probe:
                                with mock.patch(
                                    "qfit.validation.mapbox_outdoors_label_settings."
                                    "_append_qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe",
                                    side_effect=fake_append_source_style_high_zoom_probe,
                                ) as append_source_style_high_zoom_probe:
                                    report = collect_label_settings(
                                        LabelSettingsConfig(
                                            token="token",
                                            output_root=Path("/tmp"),
                                            qgis_contour_bbox_edge_difference_label_probe=True,
                                            qgis_contour_bbox_edge_difference_source_style_label_probe=True,
                                            qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe=True,
                                        )
                                    )

        fetch_style.assert_called_once_with("token", "mapbox", "outdoors-v12")
        fetch_sprites.assert_called_once()
        append_probe.assert_called_once()
        append_source_style_probe.assert_called_once()
        append_source_style_high_zoom_probe.assert_called_once()
        self.assertEqual(report["qgis_converter_result"], "success")
        self.assertEqual(report["sprite_definition_count"], 1)
        self.assertFalse(report["sprite_context_loaded"])
        self.assertTrue(report["qgis_contour_bbox_edge_difference_label_probe"])
        self.assertTrue(report["qgis_contour_bbox_edge_difference_source_style_label_probe"])
        self.assertTrue(report["qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe"])
        self.assertEqual(report["label_count"], 4)
        labels_by_style = {row["style_name"]: row for row in report["labels"]}
        self.assertEqual(labels_by_style["road-label-z15-plus"]["base_style_layer_id"], "road-label")
        self.assertEqual(
            labels_by_style["road-label-z15-plus"]["data_defined_property_labels"],
            ["ShapeSizeX (50)", "Priority (87)"],
        )
        self.assertEqual(
            labels_by_style["contour-label-bbox-edge-difference-probe"]["geometry_generator"],
            bbox_expression,
        )
        self.assertEqual(
            labels_by_style["contour-label-bbox-edge-difference-probe"]["geometry_type"],
            "Polygon",
        )
        self.assertEqual(
            labels_by_style["contour-label-bbox-edge-difference-source-style-probe"]["geometry_generator"],
            bbox_expression,
        )
        self.assertEqual(
            labels_by_style["contour-label-bbox-edge-difference-source-style-high-zoom-probe"][
                "geometry_generator"
            ],
            bbox_expression,
        )
        self.assertEqual(report["source_label_layer_count"], 1)
        self.assertEqual(report["source_label_fanout_by_base_layer"][0]["base_style_layer_id"], "road-label")
        self.assertEqual(report["source_label_fanout_by_base_layer"][0]["qfit_layer_count"], 1)
        self.assertEqual(report["source_label_layers"][0]["qfit_style_layer_id"], "road-label-z15-plus")
        self.assertIsNone(FakeQgsApplication.instance())

    def test_summary_markdown_lists_label_settings(self):
        report = {
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "generated": "2026-05-18T08:22:00+00:00",
            "sprite_context_loaded": True,
            "sprite_definition_count": 440,
            "qgis_duplicate_label_controls": {
                "qgis_version": "3.34.4-Prizren",
                "remove_duplicate_labels": False,
                "remove_duplicate_label_distance": False,
                "label_margin_distance": False,
            },
            "qgis_contour_bbox_edge_difference_label_probe": True,
            "qgis_contour_bbox_edge_difference_source_style_label_probe": True,
            "qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe": True,
            "label_count": 1,
            "label_style_summary_by_base_layer": [
                {
                    "base_style_layer_id": "contour-label",
                    "count": 1,
                    "source_layers": {"contour": 1},
                    "geometry_types": {"Line": 1},
                    "priorities": {"3": 1},
                    "placements": {"Line": 1},
                    "repeat_distances": {"0": 1},
                    "display_all": {"false": 1},
                    "obstacle": {"true": 1},
                    "allow_degraded_placement": {"false": 1},
                    "overlap_handling": {"PreventOverlap": 1},
                    "label_per_part": {"false": 1},
                    "merge_lines": {"false": 1},
                }
            ],
            "road_shield_label_placement_rows": [
                {
                    "style_name": "road-number-shield-2-remaining-icons-z11-plus",
                    "source_zoom": "6+",
                    "qfit_zoom": "11+",
                    "source_symbol_placement": ["step", ["zoom"], "point", 11, "line"],
                    "qfit_symbol_placement": "line",
                    "qfit_symbol_spacing": 466.6666666667,
                    "geometry_type": "Line",
                    "placement": "Horizontal",
                    "priority": 6,
                    "repeat_distance": 123.4722222222,
                    "display_all": False,
                    "obstacle": True,
                    "allow_degraded_placement": False,
                    "overlap_handling": "PreventOverlap",
                    "label_per_part": False,
                    "merge_lines": True,
                    "text_size": 2.38125,
                    "text_size_unit": "Millimeters",
                    "text_color": "#1d1f25",
                    "background_enabled": True,
                    "background_type": "ShapeRectangle",
                    "background_size": {"width": 4.4979166667, "height": 2.6458333333},
                    "background_size_type": "SizeFixed",
                    "background_size_unit": "Millimeters",
                    "background_fill_color": "#ffffff",
                    "background_stroke_color": "#1d1f25",
                    "background_stroke_width": 0.2,
                    "background_stroke_width_unit": "Millimeters",
                    "background_opacity": 1.0,
                    "data_defined_property_keys": [50, 87],
                    "data_defined_property_labels": ["ShapeSizeX (50)", "Priority (87)"],
                    "data_defined_property_details": [
                        {
                            "key": 50,
                            "name": "ShapeSizeX",
                            "label": "ShapeSizeX (50)",
                            "active": True,
                            "property_type": "ExpressionBasedProperty",
                            "expression": 'CASE WHEN length("ref") > 3 THEN 8 ELSE 5 END',
                            "field": None,
                            "static_value": None,
                        },
                        {
                            "key": 87,
                            "name": "Priority",
                            "label": "Priority (87)",
                            "active": False,
                            "property_type": "StaticProperty",
                            "expression": None,
                            "field": None,
                            "static_value": 0,
                        },
                    ],
                }
            ],
            "data_defined_expression_issue_rows": [
                {
                    "base_style_layer_id": "road-number-shield",
                    "style_name": "road-number-shield-2-remaining-icons-z11-plus",
                    "property": "ShapeSizeX (50)",
                    "property_type": "ExpressionBasedProperty",
                    "empty_in_clause_count": 2,
                    "expression_length": 69,
                }
            ],
            "line_label_repeat_spacing_by_base_layer": [
                {
                    "base_style_layer_id": "contour-label",
                    "source_label_rows": 1,
                    "converted_line_label_styles": 1,
                    "missing_qfit_symbol_spacing": 1,
                    "source_symbol_spacings": {"(missing)": 1},
                    "qfit_symbol_spacings": {"(missing)": 1},
                    "repeat_distances": {"0": 1},
                    "qfit_symbol_spacing_to_repeat_distances": {"(missing) -> 0": 1},
                    "placements": {"Curved": 1},
                    "style_names": {"contour-label": 1},
                    "zero_repeat_distance_count": 1,
                }
            ],
            "line_label_conversion_by_base_layer": [
                {
                    "base_style_layer_id": "contour-label",
                    "source_label_rows": 1,
                    "converted_label_styles": 1,
                    "source_symbol_placements": {"line": 1},
                    "qfit_symbol_placements": {"line": 1},
                    "converted_geometry_types": {"Line": 1},
                    "converted_placements": {"Curved": 1},
                    "repeat_distances": {"0": 1},
                    "label_per_part": {"false": 1},
                    "merge_lines": {"true": 1},
                    "style_names": {"contour-label": 1},
                }
            ],
            "line_center_label_conversion_by_base_layer": [
                {
                    "base_style_layer_id": "water-line-label",
                    "source_label_rows": 1,
                    "converted_label_styles": 1,
                    "source_layers": {"natural_label": 1},
                    "source_symbol_placements": {"line-center": 1},
                    "qfit_symbol_placements": {"line-center": 1},
                    "converted_geometry_types": {"Line": 1},
                    "converted_placements": {"Line": 1},
                    "repeat_distances": {"0": 1},
                    "label_per_part": {"false": 1},
                    "merge_lines": {"false": 1},
                    "style_names": {"water-line-label": 1},
                }
            ],
            "source_label_fanout_by_base_layer": [
                {
                    "base_style_layer_id": "contour-label",
                    "source_label_rows": 1,
                    "converted_label_styles": 1,
                    "qfit_layer_count": 1,
                    "source_layers": {"contour": 1},
                    "source_zooms": {"12+": 1},
                    "qfit_zooms": {"12+": 1},
                    "field_names": {'"name"': 1},
                }
            ],
            "source_label_control_summary_by_base_layer": [
                {
                    "base_style_layer_id": "contour-label",
                    "source_label_rows": 1,
                    "missing_control_count": 0,
                    "source_layout_controls": {"symbol-placement": 1, "text-field": 1},
                    "qfit_layout_controls": {"symbol-placement": 1, "text-field": 1},
                    "missing_layout_controls": {},
                    "source_paint_controls": {"text-color": 1, "text-halo-color": 1},
                    "qfit_paint_controls": {"text-color": 1, "text-halo-color": 1},
                    "missing_paint_controls": {},
                }
            ],
            "source_label_control_omission_summary_by_base_layer": [
                {
                    "base_style_layer_id": "country-label",
                    "source_label_rows": 10,
                    "omitted_control_count": 20,
                    "omitted_controls": {"layout.icon-image": 10, "paint.icon-opacity": 10},
                    "omission_reasons": {
                        "empty icon-image removed": 10,
                        "icon-opacity removed with no QGIS icon": 10,
                    },
                }
            ],
            "source_label_unresolved_control_summary_by_base_layer": [
                {
                    "base_style_layer_id": "settlement-major-label",
                    "source_label_rows": 4,
                    "unresolved_control_count": 6,
                    "unresolved_controls": {"layout.text-anchor": 4, "paint.text-halo-color": 2},
                }
            ],
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
                    "allow_degraded_placement": False,
                    "overlap_handling": "PreventOverlap",
                    "label_per_part": False,
                    "merge_lines": False,
                    "geometry_generator": "boundary($geometry)",
                    "geometry_generator_enabled": True,
                    "geometry_generator_type": "Line",
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
                    "data_defined_property_keys": [50, 87],
                    "data_defined_property_names": ["ShapeSizeX", "Priority"],
                    "data_defined_property_labels": ["ShapeSizeX (50)", "Priority (87)"],
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
        self.assertIn(
            "QGIS duplicate-label controls: QGIS 3.34.4-Prizren; remove_duplicate_labels=no; remove_duplicate_label_distance=no; label_margin_distance=no",
            markdown,
        )
        self.assertIn("Bbox-edge contour label probe: yes", markdown)
        self.assertIn("Bbox-edge source-style contour label probe: yes", markdown)
        self.assertIn("Bbox-edge source-style high-zoom contour label probe: yes", markdown)
        self.assertIn("## Label style summary by base layer", markdown)
        self.assertIn(
            "| contour-label | 1 | contour=1 | Line=1 | 3=1 | Line=1 | 0=1 | no=1 | yes=1 | no=1 | PreventOverlap=1 | no=1 | no=1 |",
            markdown,
        )
        self.assertIn("## Road shield label placement detail", markdown)
        self.assertIn(
            '| road-number-shield-2-remaining-icons-z11-plus | 6+ | 11+ | step, [\'zoom\'], point, 11, line | line | 466.667 | Line | Horizontal | 6 | 123.472 | no | yes | no | PreventOverlap | no | yes | 2.38125 Millimeters | #1d1f25 | yes ShapeRectangle | 4.49792 x 2.64583 SizeFixed Millimeters | #ffffff 1 | #1d1f25 0.2 Millimeters | ShapeSizeX (50): ExpressionBasedProperty CASE WHEN length("ref") > 3 THEN 8 ELSE 5 END, Priority (87): StaticProperty inactive 0 |',
            markdown,
        )
        self.assertIn("## Data-defined expression issues", markdown)
        self.assertIn(
            "| road-number-shield | road-number-shield-2-remaining-icons-z11-plus | ShapeSizeX (50) | ExpressionBasedProperty | 2 | 69 |",
            markdown,
        )
        self.assertIn("## Line label repeat spacing by base layer", markdown)
        self.assertIn(
            "| contour-label | 1 | 1 | 1 | (missing)=1 | (missing)=1 | 0=1 | (missing) -> 0=1 | Curved=1 | contour-label=1 |",
            markdown,
        )
        self.assertIn("## Line-placement label conversion by base layer", markdown)
        self.assertIn(
            "| contour-label | 1 | 1 | line=1 | line=1 | Line=1 | Curved=1 | 0=1 | no=1 | yes=1 | contour-label=1 |",
            markdown,
        )
        self.assertIn("## Line-center label conversion by base layer", markdown)
        self.assertIn(
            "Subset of the line-placement table whose original Mapbox source placement includes line-center.",
            markdown,
        )
        self.assertIn(
            "| water-line-label | 1 | 1 | natural_label=1 | line-center=1 | line-center=1 | Line=1 | Line=1 | 0=1 | no=1 | no=1 | water-line-label=1 |",
            markdown,
        )
        self.assertIn("## Source label fan-out by base layer", markdown)
        self.assertIn('| contour-label | 1 | 1 | 1 | contour=1 | 12+=1 | 12+=1 | "name"=1 |', markdown)
        self.assertIn("## Source label control coverage by base layer", markdown)
        self.assertIn(
            "| contour-label | 1 | 0 | symbol-placement=1, text-field=1 | symbol-placement=1, text-field=1 | — | text-color=1, text-halo-color=1 | text-color=1, text-halo-color=1 | — |",
            markdown,
        )
        self.assertIn("## Known qfit label control omissions by base layer", markdown)
        self.assertIn(
            "| country-label | 10 | 20 | layout.icon-image=10, paint.icon-opacity=10 | empty icon-image removed=10, icon-opacity removed with no QGIS icon=10 |",
            markdown,
        )
        self.assertIn("## Unresolved label control gaps by base layer", markdown)
        self.assertIn(
            "| settlement-major-label | 4 | 6 | layout.text-anchor=4, paint.text-halo-color=2 |",
            markdown,
        )
        self.assertIn("## Converted QGIS label styles", markdown)
        self.assertIn("contour-label", markdown)
        self.assertIn("| contour-label | contour-label | contour | Line |", markdown)
        self.assertIn("yes Line boundary($geometry)", markdown)
        self.assertIn("concat", markdown)
        self.assertIn("Millimeters", markdown)
        self.assertIn("#626250", markdown)
        self.assertIn("#dcdcd4", markdown)
        self.assertIn("25/-25", markdown)
        self.assertIn("ShapeSizeX (50), Priority (87)", markdown)
        self.assertIn("## Source Mapbox label controls", markdown)
        self.assertIn("Source label layers: 1", markdown)
        self.assertIn("| contour-label | contour-label | contour-label | contour | 12+", markdown)
        self.assertIn("\"symbol-placement\":\"line\"", markdown)
        self.assertIn("\"text-halo-color\":\"#dcdcd4\"", markdown)

    def test_build_summary_markdown_keeps_converted_heading_for_legacy_reports(self):
        report = {
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "generated": "2026-05-19T00:00:00Z",
            "label_count": 1,
            "sprite_context_loaded": False,
            "sprite_definition_count": 0,
            "labels": [
                {
                    "base_style_layer_id": "contour-label",
                    "style_name": "contour-label",
                    "source_layer": "contour",
                    "geometry_type": "Line",
                }
            ],
        }

        markdown = build_summary_markdown(report)

        self.assertNotIn("## Label style summary by base layer", markdown)
        self.assertIn("## Converted QGIS label styles", markdown)
        self.assertIn("| contour-label | contour-label | contour | Line |", markdown)

    def test_geometry_generator_markdown_handles_missing_disabled_and_enabled_values(self):
        self.assertEqual(_geometry_generator_markdown_value({}), "—")
        self.assertEqual(
            _geometry_generator_markdown_value(
                {
                    "geometry_generator": "",
                    "geometry_generator_enabled": False,
                    "geometry_generator_type": "Point",
                }
            ),
            "no",
        )
        self.assertEqual(
            _geometry_generator_markdown_value(
                {
                    "geometry_generator": "boundary($geometry)",
                    "geometry_generator_enabled": True,
                    "geometry_generator_type": "Line",
                }
            ),
            "yes Line boundary($geometry)",
        )

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
                        "--qgis-contour-bbox-edge-difference-label-probe",
                        "--qgis-contour-bbox-edge-difference-source-style-label-probe",
                        "--qgis-contour-bbox-edge-difference-source-style-high-zoom-label-probe",
                    ]
                )

            self.assertEqual(exit_code, 0)
            collect.assert_called_once()
            self.assertEqual(collect.call_args.args[0].style_json_path, style_path)
            self.assertFalse(collect.call_args.args[0].include_sprite_context)
            self.assertTrue(collect.call_args.args[0].qgis_contour_bbox_edge_difference_label_probe)
            self.assertTrue(
                collect.call_args.args[0].qgis_contour_bbox_edge_difference_source_style_label_probe
            )
            config = collect.call_args.args[0]
            self.assertTrue(config.qgis_contour_bbox_edge_difference_source_style_high_zoom_label_probe)
            self.assertEqual(len(list(output_root.glob("mapbox-outdoors-v12/*/summary.md"))), 1)


if __name__ == "__main__":
    unittest.main()
