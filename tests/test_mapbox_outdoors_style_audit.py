import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from tests import _path  # noqa: F401

from qfit.mapbox_config import MapboxSpriteResources
from qfit.validation import mapbox_outdoors_style_audit
from qfit.validation.mapbox_outdoors_style_audit import (
    StyleAuditConfig,
    build_audit_markdown,
    build_parser,
    build_style_audit,
    load_style_definition,
    main,
    render_audit,
    resolve_mapbox_token,
)


SAMPLE_STYLE = {
    "version": 8,
    "sources": {
        "composite": {
            "type": "vector",
            "url": "mapbox://mapbox.mapbox-streets-v8,mapbox.mapbox-terrain-v2",
        }
    },
    "layers": [
        {
            "id": "background",
            "type": "background",
            "paint": {
                "background-color": ["interpolate", ["linear"], ["zoom"], 5, "#eef2e8", 12, "#dde6d8"]
            },
        },
        {
            "id": "road-primary",
            "type": "line",
            "source": "composite",
            "source-layer": "road",
            "minzoom": 5,
            "paint": {
                "line-color": ["match", ["get", "class"], "primary", "#ffffff", "#cccccc"],
                "line-dasharray": [3, 3],
                "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1, 12, 6],
            },
            "layout": {"line-cap": "round"},
        },
        {
            "id": "road-path",
            "type": "line",
            "source": "composite",
            "source-layer": "road",
            "paint": {
                "line-dasharray": ["step", ["zoom"], ["literal", [3, 3]], 12, ["literal", [4, 4]]],
            },
            "layout": {},
        },
        {
            "id": "poi-label",
            "type": "symbol",
            "source": "composite",
            "source-layer": "poi_label",
            "minzoom": 12,
            "filter": ["==", ["get", "maki"], "park"],
            "layout": {
                "text-field": ["format", ["get", "name"], {}, "\n", {}, ["get", "name_en"], {}],
                "text-size": ["interpolate", ["linear"], ["zoom"], 10, 10, 14, 14],
                "text-offset": [0, 0.8],
                "icon-image": ["get", "maki"],
            },
            "paint": {
                "text-color": "#222222",
                "text-halo-color": ["coalesce", ["get", "halo"], "#ffffff"],
            },
        },
        {
            "id": "settlement-subdivision-label",
            "type": "symbol",
            "source": "composite",
            "source-layer": "place_label",
            "layout": {
                "text-field": ["format", ["get", "name"], {}],
                "icon-image": ["get", "maki"],
            },
        },
    ],
}


def _fake_qgis_modules(warning_sets, *, existing_app=None):
    qgis_module = ModuleType("qgis")
    qgis_core = ModuleType("qgis.core")

    class FakeQgsApplication:
        current_instance = existing_app
        created = []

        def __init__(self, args, gui_enabled):
            self.args = args
            self.gui_enabled = gui_enabled
            self.init_qgis_calls = 0
            self.exit_qgis_calls = 0
            FakeQgsApplication.current_instance = self
            FakeQgsApplication.created.append(self)

        @classmethod
        def instance(cls):
            return cls.current_instance

        def initQgis(self):
            self.init_qgis_calls += 1

        def exitQgis(self):
            self.exit_qgis_calls += 1
            FakeQgsApplication.current_instance = None

    class FakeQgsMapBoxGlStyleConverter:
        converted_styles = []
        converted_contexts = []
        created_count = 0

        def __init__(self):
            self.index = FakeQgsMapBoxGlStyleConverter.created_count
            FakeQgsMapBoxGlStyleConverter.created_count += 1

        def convert(self, style_definition, context=None):
            FakeQgsMapBoxGlStyleConverter.converted_styles.append(style_definition)
            FakeQgsMapBoxGlStyleConverter.converted_contexts.append(context)

        def warnings(self):
            return warning_sets[self.index]

    class FakeQgsMapBoxGlStyleConversionContext:
        created = []

        def __init__(self):
            self.target_unit = None
            self.pixel_size_conversion_factor = None
            self.sprites = None
            FakeQgsMapBoxGlStyleConversionContext.created.append(self)

        def setTargetUnit(self, unit):
            self.target_unit = unit

        def setPixelSizeConversionFactor(self, factor):
            self.pixel_size_conversion_factor = factor

        def setSprites(self, image, definitions):
            self.sprites = (image, definitions)

    class FakeQgis:
        class RenderUnit:
            Millimeters = "millimeters"

    qgis_core.QgsApplication = FakeQgsApplication
    qgis_core.QgsMapBoxGlStyleConverter = FakeQgsMapBoxGlStyleConverter
    qgis_core.QgsMapBoxGlStyleConversionContext = FakeQgsMapBoxGlStyleConversionContext
    qgis_core.Qgis = FakeQgis
    qgis_module.core = qgis_core
    return qgis_module, qgis_core, FakeQgsApplication, FakeQgsMapBoxGlStyleConverter


class MapboxOutdoorsStyleAuditTests(unittest.TestCase):
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

    def test_build_style_audit_classifies_layers_and_qfit_simplifications(self):
        audit = build_style_audit(
            SAMPLE_STYLE,
            config=StyleAuditConfig(
                style_owner="mapbox",
                style_id="outdoors-v12",
                generated_at=dt.datetime(2026, 5, 11, 7, 5, tzinfo=dt.timezone.utc),
            ),
        )

        self.assertEqual(audit["style"]["label"], "mapbox/outdoors-v12")
        self.assertEqual(audit["layer_count"], 5)
        summary = audit["summary"]
        simplified_counts = {
            item["property"]: item["count"] for item in summary["qfit_simplifies_by_property"]
        }
        simplified_group_counts = {
            (item["group"], item["property"]): item["count"]
            for item in summary["qfit_simplifies_by_layer_group_and_property"]
        }
        unresolved_counts = {
            item["property"]: item["count"] for item in summary["qfit_unresolved_by_property"]
        }
        unresolved_group_counts = {
            (item["group"], item["property"]): item["count"]
            for item in summary["qfit_unresolved_by_layer_group_and_property"]
        }
        operator_counts = {
            (item["property"], item["operator"]): item["count"]
            for item in summary["qfit_unresolved_expression_operators_by_property"]
        }
        operator_group_counts = {
            (item["group"], item["property"], item["operator"]): item["count"]
            for item in summary["qfit_unresolved_expression_operators_by_layer_group_and_property"]
        }
        filter_signatures = {
            (item["group"], item["operator_signature"]): item
            for item in summary["qfit_unresolved_filter_expression_signatures_by_layer_group"]
        }
        self.assertEqual(simplified_counts["layout.text-field"], 2)
        self.assertEqual(simplified_counts["paint.line-width"], 1)
        self.assertEqual(simplified_counts["paint.line-dasharray"], 1)
        self.assertEqual(simplified_counts["layout.visibility"], 1)
        self.assertEqual(simplified_group_counts[("pois/labels", "layout.text-field")], 1)
        self.assertEqual(simplified_group_counts[("settlements/places", "layout.text-field")], 1)
        self.assertEqual(simplified_group_counts[("roads/trails", "paint.line-width")], 1)
        self.assertEqual(simplified_group_counts[("roads/trails", "paint.line-dasharray")], 1)
        self.assertEqual(simplified_group_counts[("settlements/places", "layout.visibility")], 1)
        self.assertEqual(unresolved_counts["filter"], 1)
        self.assertEqual(unresolved_counts["layout.icon-image"], 1)
        self.assertEqual(unresolved_group_counts[("pois/labels", "filter")], 1)
        self.assertEqual(unresolved_group_counts[("pois/labels", "layout.icon-image")], 1)
        self.assertEqual(operator_counts[("filter", "==")], 1)
        self.assertEqual(operator_counts[("filter", "get")], 1)
        self.assertEqual(operator_counts[("layout.icon-image", "get")], 1)
        self.assertEqual(operator_group_counts[("pois/labels", "filter", "==")], 1)
        self.assertEqual(operator_group_counts[("pois/labels", "layout.icon-image", "get")], 1)
        self.assertEqual(filter_signatures[("pois/labels", "==, get")]["count"], 1)
        self.assertEqual(filter_signatures[("pois/labels", "==, get")]["operators"], ["==", "get"])
        self.assertEqual(filter_signatures[("pois/labels", "==, get")]["example_layers"], ["poi-label"])

        layers = {layer["id"]: layer for layer in audit["layers"]}
        self.assertEqual(layers["background"]["group"], "background")
        self.assertEqual(layers["road-primary"]["group"], "roads/trails")
        self.assertEqual(layers["road-primary"]["source_layer"], "road")
        self.assertEqual(layers["road-primary"]["zoom_band"], "z≥5")

        road_simplified = {change["property"] for change in layers["road-primary"]["qfit_simplifies"]}
        self.assertIn("paint.line-color", road_simplified)
        self.assertIn("paint.line-width", road_simplified)
        self.assertIn("paint.line-dasharray", layers["road-primary"]["qfit_preserves"])
        self.assertIn("layout.line-cap", layers["road-primary"]["qfit_preserves"])
        road_unresolved = {item["property"] for item in layers["road-primary"]["qfit_unresolved"]}
        self.assertNotIn("paint.line-dasharray", road_unresolved)

        path_simplified = {change["property"] for change in layers["road-path"]["qfit_simplifies"]}
        path_unresolved = {item["property"] for item in layers["road-path"]["qfit_unresolved"]}
        self.assertIn("paint.line-dasharray", path_simplified)
        self.assertNotIn("paint.line-dasharray", path_unresolved)

        poi_simplified = {change["property"] for change in layers["poi-label"]["qfit_simplifies"]}
        self.assertIn("layout.text-field", poi_simplified)
        self.assertIn("layout.text-size", poi_simplified)
        self.assertIn("paint.text-halo-color", poi_simplified)
        self.assertIn("filter", layers["poi-label"]["qfit_preserves"])
        self.assertIn("layout.text-offset", layers["poi-label"]["qfit_preserves"])

        unresolved = {item["property"]: item for item in layers["poi-label"]["qfit_unresolved"]}
        self.assertIn("filter", unresolved)
        self.assertIn("layout.icon-image", unresolved)
        self.assertNotIn("layout.text-field", unresolved)
        self.assertNotIn("layout.text-offset", unresolved)
        self.assertIn("filter expression", unresolved["filter"]["reason"])
        self.assertIn("sprites", unresolved["layout.icon-image"]["reason"])
        self.assertEqual(unresolved["filter"]["expression_operators"], ["==", "get"])
        self.assertEqual(unresolved["layout.icon-image"]["expression_operators"], ["get"])

        hidden_layer = layers["settlement-subdivision-label"]
        hidden_changes = {change["property"]: change for change in hidden_layer["qfit_simplifies"]}
        self.assertEqual(hidden_changes["layout.visibility"]["from"], "absent")
        self.assertEqual(hidden_changes["layout.visibility"]["to"], '"none"')
        self.assertEqual(hidden_layer["qfit_unresolved"], [])

    def test_build_style_audit_treats_qgis_font_fallback_as_resolved(self):
        audit = build_style_audit(
            {
                "version": 8,
                "layers": [
                    {
                        "id": "poi-label",
                        "type": "symbol",
                        "source-layer": "poi_label",
                        "layout": {"text-font": ["DIN Pro Medium", "Arial Unicode MS Regular"]},
                    }
                ],
            }
        )

        layer = audit["layers"][0]
        self.assertIn("layout.text-font", {change["property"] for change in layer["qfit_simplifies"]})
        self.assertNotIn("layout.text-font", {item["property"] for item in layer["qfit_unresolved"]})
        self.assertEqual(
            audit["summary"]["qfit_simplifies_by_property"],
            [{"property": "layout.text-font", "count": 1}],
        )

    def test_build_style_audit_reports_visible_label_density_candidates(self):
        audit = build_style_audit(
            {
                "version": 8,
                "layers": [
                    {
                        "id": "road-label",
                        "type": "symbol",
                        "source-layer": "road",
                        "minzoom": 10,
                        "filter": ["all", ["==", ["get", "class"], "primary"], ["has", "name"]],
                        "layout": {
                            "text-field": ["get", "name"],
                            "text-size": ["interpolate", ["linear"], ["zoom"], 10, 10, 14, 14],
                            "symbol-sort-key": ["get", "rank"],
                            "symbol-spacing": ["step", ["zoom"], 150, 14, 250],
                        },
                    },
                    {
                        "id": "settlement-major-label",
                        "type": "symbol",
                        "source-layer": "place_label",
                        "layout": {"text-field": ["get", "name"]},
                    },
                    {
                        "id": "settlement-subdivision-label",
                        "type": "symbol",
                        "source-layer": "place_label",
                        "layout": {"text-field": ["get", "name"]},
                    },
                    {
                        "id": "hidden-label",
                        "type": "symbol",
                        "source-layer": "place_label",
                        "layout": {"text-field": ["get", "name"], "visibility": "none"},
                    },
                    {
                        "id": "poi-icon-only",
                        "type": "symbol",
                        "source-layer": "poi_label",
                        "layout": {"icon-image": ["get", "maki"]},
                    },
                ],
            }
        )

        candidates = audit["summary"]["label_density_candidates"]
        self.assertEqual([candidate["layer"] for candidate in candidates], ["road-label", "settlement-major-label"])
        road_candidate, settlement_candidate = candidates
        self.assertEqual(road_candidate["group"], "roads/trails")
        self.assertEqual(road_candidate["source_layer"], "road")
        self.assertEqual(road_candidate["zoom_band"], "z≥10")
        self.assertEqual(road_candidate["filter_operator_signature"], "==, all, get, has")
        self.assertEqual(
            road_candidate["label_control_properties"],
            ["filter", "layout.symbol-sort-key", "layout.symbol-spacing", "layout.text-field", "layout.text-size"],
        )
        self.assertEqual(
            road_candidate["qgis_dependent_control_properties"],
            ["filter", "layout.symbol-sort-key", "layout.symbol-spacing"],
        )
        self.assertEqual(settlement_candidate["filter_operator_signature"], "get, match")
        self.assertEqual(settlement_candidate["label_control_properties"], ["filter", "layout.text-field"])
        self.assertEqual(settlement_candidate["qgis_dependent_control_properties"], ["filter"])
        settlement_layer = next(layer for layer in audit["layers"] if layer["id"] == "settlement-major-label")
        self.assertIsNone(settlement_layer["filter"])
        self.assertIsNotNone(settlement_layer["qgis_filter"])
        self.assertEqual(
            audit["summary"]["label_density_candidates_by_layer_group"],
            [{"group": "roads/trails", "count": 1}, {"group": "settlements/places", "count": 1}],
        )

        markdown = build_audit_markdown(audit)
        self.assertIn("### Label density candidates", markdown)
        self.assertIn("Visible symbol layers with text labels", markdown)
        self.assertIn("| `roads/trails` | `road-label` | `road` | z≥10 | `==, all, get, has` |", markdown)
        self.assertIn("| `settlements/places` | `settlement-major-label` | `place_label` | all zooms | `get, match` |", markdown)
        self.assertNotIn("settlement-subdivision-label` |", markdown)
        self.assertNotIn("hidden-label` |", markdown)

    def test_build_style_audit_reports_road_trail_hierarchy_candidates(self):
        audit = build_style_audit(
            {
                "version": 8,
                "layers": [
                    {
                        "id": "road-primary",
                        "type": "line",
                        "source-layer": "road",
                        "minzoom": 5,
                        "filter": ["==", ["get", "class"], "primary"],
                        "layout": {"line-cap": "round", "line-join": "round"},
                        "paint": {
                            "line-color": ["match", ["get", "class"], "primary", "#fff", "#ccc"],
                            "line-dasharray": ["step", ["zoom"], ["literal", [1, 2]], 12, ["literal", [2, 2]]],
                            "line-opacity": ["step", ["zoom"], 0, 10, 1],
                            "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1, 12, 5],
                        },
                    },
                    {
                        "id": "road-pedestrian-polygon-fill",
                        "type": "fill",
                        "source-layer": "road",
                        "paint": {
                            "fill-color": ["match", ["get", "class"], "pedestrian", "#eee", "#fff"],
                            "fill-opacity": ["interpolate", ["linear"], ["zoom"], 14, 0, 16, 1],
                        },
                    },
                    {
                        "id": "hidden-road",
                        "type": "line",
                        "source-layer": "road",
                        "layout": {"visibility": "none"},
                        "paint": {"line-color": "#ccc"},
                    },
                    {
                        "id": "poi-label",
                        "type": "symbol",
                        "source-layer": "poi_label",
                        "layout": {"text-field": ["get", "name"]},
                    },
                ],
            }
        )

        candidates = audit["summary"]["road_trail_hierarchy_candidates"]
        self.assertEqual([candidate["layer"] for candidate in candidates], ["road-pedestrian-polygon-fill", "road-primary"])
        fill_candidate, road_candidate = candidates
        self.assertEqual(fill_candidate["type"], "fill")
        self.assertEqual(fill_candidate["source_layer"], "road")
        self.assertEqual(fill_candidate["filter_operator_signature"], "(none)")
        self.assertEqual(fill_candidate["road_trail_control_properties"], ["paint.fill-color", "paint.fill-opacity"])
        self.assertEqual(fill_candidate["qfit_simplified_control_properties"], ["paint.fill-color"])
        self.assertEqual(fill_candidate["qgis_dependent_control_properties"], ["paint.fill-opacity"])
        self.assertEqual(road_candidate["zoom_band"], "z≥5")
        self.assertEqual(road_candidate["filter_operator_signature"], "==, get")
        self.assertEqual(
            road_candidate["road_trail_control_properties"],
            [
                "filter",
                "layout.line-cap",
                "layout.line-join",
                "paint.line-color",
                "paint.line-dasharray",
                "paint.line-opacity",
                "paint.line-width",
            ],
        )
        self.assertEqual(
            road_candidate["qfit_simplified_control_properties"],
            ["paint.line-color", "paint.line-dasharray", "paint.line-opacity", "paint.line-width"],
        )
        self.assertEqual(
            road_candidate["qgis_dependent_control_properties"],
            ["filter"],
        )
        self.assertEqual(audit["summary"]["road_trail_hierarchy_candidates_by_source_layer"], [{"source_layer": "road", "count": 2}])
        self.assertEqual(
            audit["summary"]["road_trail_hierarchy_candidates_by_type"],
            [{"type": "fill", "count": 1}, {"type": "line", "count": 1}],
        )
        self.assertEqual(
            audit["summary"]["road_trail_hierarchy_simplified_by_property"],
            [
                {"property": "paint.fill-color", "count": 1},
                {"property": "paint.line-color", "count": 1},
                {"property": "paint.line-dasharray", "count": 1},
                {"property": "paint.line-opacity", "count": 1},
                {"property": "paint.line-width", "count": 1},
            ],
        )
        self.assertEqual(
            audit["summary"]["road_trail_hierarchy_qgis_dependent_by_property"],
            [
                {"property": "filter", "count": 1},
                {"property": "paint.fill-opacity", "count": 1},
            ],
        )

        markdown = build_audit_markdown(audit)
        self.assertIn("### Road/trail hierarchy candidates", markdown)
        self.assertIn("Visible road/trail line and fill layers", markdown)
        self.assertIn("#### Road/trail hierarchy candidates QGIS-dependent controls", markdown)
        self.assertIn("| `paint.line-width` | 1 |", markdown)
        self.assertIn("| `road-primary` | `line` | `road` | z≥5 | `==, get` |", markdown)
        self.assertIn(
            "paint.line-color<br>paint.line-dasharray<br>paint.line-opacity<br>paint.line-width",
            markdown,
        )
        self.assertIn("filter", markdown)
        self.assertNotIn("filter<br>paint.line-opacity", markdown)
        self.assertNotIn("hidden-road` |", markdown)

    def test_build_style_audit_reports_terrain_landcover_palette_candidates(self):
        audit = build_style_audit(
            {
                "version": 8,
                "layers": [
                    {
                        "id": "landuse",
                        "type": "fill",
                        "source-layer": "landuse",
                        "minzoom": 5,
                        "filter": ["match", ["get", "class"], ["wood", "grass"], True, False],
                        "paint": {
                            "fill-antialias": False,
                            "fill-color": ["match", ["get", "class"], "wood", "#88aa66", "#ddddaa"],
                            "fill-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0.4, 12, 0.8],
                        },
                    },
                    {
                        "id": "contour-line",
                        "type": "line",
                        "source-layer": "contour",
                        "filter": ["!=", ["get", "index"], -1],
                        "paint": {
                            "line-color": "#b3a78a",
                            "line-opacity": ["match", ["get", "index"], [1, 2], 0.5, 0.8],
                            "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.4, 16, 1.2],
                        },
                    },
                    {
                        "id": "hidden-landcover",
                        "type": "fill",
                        "source-layer": "landcover",
                        "layout": {"visibility": "none"},
                        "paint": {"fill-color": "#eeeeee"},
                    },
                    {
                        "id": "contour-label",
                        "type": "symbol",
                        "source-layer": "contour",
                        "layout": {"text-field": ["get", "ele"]},
                    },
                ],
            }
        )

        candidates = audit["summary"]["terrain_landcover_palette_candidates"]
        self.assertEqual([candidate["layer"] for candidate in candidates], ["landuse", "contour-line"])
        fill_candidate, line_candidate = candidates
        self.assertEqual(fill_candidate["type"], "fill")
        self.assertEqual(fill_candidate["source_layer"], "landuse")
        self.assertEqual(fill_candidate["zoom_band"], "z≥5")
        self.assertEqual(fill_candidate["filter_operator_signature"], "get, match")
        self.assertEqual(
            fill_candidate["terrain_landcover_palette_control_properties"],
            ["filter", "paint.fill-antialias", "paint.fill-color", "paint.fill-opacity"],
        )
        self.assertEqual(fill_candidate["qfit_simplified_control_properties"], ["paint.fill-color"])
        self.assertEqual(fill_candidate["qgis_dependent_control_properties"], ["filter", "paint.fill-opacity"])
        self.assertEqual(line_candidate["type"], "line")
        self.assertEqual(line_candidate["source_layer"], "contour")
        self.assertEqual(line_candidate["filter_operator_signature"], "!=, get")
        self.assertEqual(
            line_candidate["terrain_landcover_palette_control_properties"],
            ["filter", "paint.line-color", "paint.line-opacity", "paint.line-width"],
        )
        self.assertEqual(line_candidate["qfit_simplified_control_properties"], ["paint.line-width"])
        self.assertEqual(line_candidate["qgis_dependent_control_properties"], ["filter", "paint.line-opacity"])
        self.assertEqual(
            audit["summary"]["terrain_landcover_palette_candidates_by_source_layer"],
            [{"source_layer": "contour", "count": 1}, {"source_layer": "landuse", "count": 1}],
        )
        self.assertEqual(
            audit["summary"]["terrain_landcover_palette_candidates_by_type"],
            [{"type": "fill", "count": 1}, {"type": "line", "count": 1}],
        )
        self.assertEqual(
            audit["summary"]["terrain_landcover_palette_simplified_by_property"],
            [{"property": "paint.fill-color", "count": 1}, {"property": "paint.line-width", "count": 1}],
        )
        self.assertEqual(
            audit["summary"]["terrain_landcover_palette_qgis_dependent_by_property"],
            [
                {"property": "filter", "count": 2},
                {"property": "paint.fill-opacity", "count": 1},
                {"property": "paint.line-opacity", "count": 1},
            ],
        )

        markdown = build_audit_markdown(audit)
        self.assertIn("### Terrain/landcover palette candidates", markdown)
        self.assertIn("Visible terrain/landcover fill and line layers", markdown)
        self.assertIn("#### Terrain/landcover palette candidates simplified/substituted by qfit", markdown)
        self.assertIn("#### Terrain/landcover palette candidates QGIS-dependent controls", markdown)
        self.assertIn("| `filter` | 2 |", markdown)
        self.assertIn("| `landuse` | `fill` | `landuse` | z≥5 | `get, match` |", markdown)
        self.assertIn("paint.fill-antialias<br>paint.fill-color<br>paint.fill-opacity", markdown)
        self.assertIn("filter<br>paint.fill-opacity", markdown)
        self.assertNotIn("hidden-landcover` |", markdown)

    def test_build_style_audit_reports_water_surface_flow_candidates(self):
        audit = build_style_audit(
            {
                "version": 8,
                "layers": [
                    {
                        "id": "water-depth",
                        "type": "fill",
                        "source-layer": "depth",
                        "maxzoom": 8,
                        "paint": {
                            "fill-antialias": True,
                            "fill-color": ["interpolate", ["linear"], ["get", "min_depth"], 0, "#a8d8f0", 100, "#4f97c2"],
                            "fill-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0.35, 8, 0],
                        },
                    },
                    {
                        "id": "waterway",
                        "type": "line",
                        "source-layer": "waterway",
                        "minzoom": 8,
                        "filter": ["match", ["get", "class"], ["river", "canal"], True, False],
                        "paint": {
                            "line-color": "#8ec5e8",
                            "line-opacity": ["match", ["get", "class"], "river", 0.8, 0.45],
                            "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 14, 4],
                        },
                    },
                    {
                        "id": "hidden-water",
                        "type": "fill",
                        "source-layer": "water",
                        "layout": {"visibility": "none"},
                        "paint": {"fill-color": "#a8d8f0"},
                    },
                    {
                        "id": "water-label",
                        "type": "symbol",
                        "source-layer": "natural_label",
                        "layout": {"text-field": ["get", "name"]},
                    },
                ],
            }
        )

        candidates = audit["summary"]["water_surface_flow_candidates"]
        self.assertEqual([candidate["layer"] for candidate in candidates], ["water-depth", "waterway"])
        fill_candidate, line_candidate = candidates
        self.assertEqual(fill_candidate["type"], "fill")
        self.assertEqual(fill_candidate["source_layer"], "depth")
        self.assertEqual(fill_candidate["zoom_band"], "z<8")
        self.assertEqual(fill_candidate["filter_operator_signature"], "(none)")
        self.assertEqual(
            fill_candidate["water_surface_flow_control_properties"],
            ["paint.fill-antialias", "paint.fill-color", "paint.fill-opacity"],
        )
        self.assertEqual(fill_candidate["qfit_simplified_control_properties"], ["paint.fill-color"])
        self.assertEqual(fill_candidate["qgis_dependent_control_properties"], ["paint.fill-opacity"])
        self.assertEqual(line_candidate["type"], "line")
        self.assertEqual(line_candidate["source_layer"], "waterway")
        self.assertEqual(line_candidate["zoom_band"], "z≥8")
        self.assertEqual(line_candidate["filter_operator_signature"], "get, match")
        self.assertEqual(
            line_candidate["water_surface_flow_control_properties"],
            ["filter", "paint.line-color", "paint.line-opacity", "paint.line-width"],
        )
        self.assertEqual(line_candidate["qfit_simplified_control_properties"], ["paint.line-width"])
        self.assertEqual(line_candidate["qgis_dependent_control_properties"], ["filter", "paint.line-opacity"])
        self.assertEqual(
            audit["summary"]["water_surface_flow_candidates_by_source_layer"],
            [{"source_layer": "depth", "count": 1}, {"source_layer": "waterway", "count": 1}],
        )
        self.assertEqual(
            audit["summary"]["water_surface_flow_candidates_by_type"],
            [{"type": "fill", "count": 1}, {"type": "line", "count": 1}],
        )
        self.assertEqual(
            audit["summary"]["water_surface_flow_simplified_by_property"],
            [{"property": "paint.fill-color", "count": 1}, {"property": "paint.line-width", "count": 1}],
        )
        self.assertEqual(
            audit["summary"]["water_surface_flow_qgis_dependent_by_property"],
            [
                {"property": "filter", "count": 1},
                {"property": "paint.fill-opacity", "count": 1},
                {"property": "paint.line-opacity", "count": 1},
            ],
        )

        markdown = build_audit_markdown(audit)
        self.assertIn("### Water surface/flow candidates", markdown)
        self.assertIn("Visible water fill and line layers", markdown)
        self.assertIn("#### Water surface/flow candidates simplified/substituted by qfit", markdown)
        self.assertIn("#### Water surface/flow candidates QGIS-dependent controls", markdown)
        self.assertIn("| `water-depth` | `fill` | `depth` | z<8 | `(none)` |", markdown)
        self.assertIn("paint.fill-antialias<br>paint.fill-color<br>paint.fill-opacity", markdown)
        self.assertIn("filter<br>paint.line-opacity", markdown)
        self.assertNotIn("hidden-water` |", markdown)

    def test_build_style_audit_reports_icon_sprite_candidates(self):
        audit = build_style_audit(
            {
                "version": 8,
                "layers": [
                    {
                        "id": "road-shield",
                        "type": "symbol",
                        "source-layer": "road",
                        "minzoom": 6,
                        "filter": ["all", ["has", "reflen"], ["<=", ["get", "reflen"], 6]],
                        "layout": {
                            "icon-image": [
                                "step",
                                ["zoom"],
                                "shield-small",
                                12,
                                ["concat", ["get", "shield"], "-", ["to-string", ["get", "reflen"]]],
                            ],
                            "icon-rotation-alignment": "map",
                            "symbol-placement": ["step", ["zoom"], "point", 11, "line"],
                            "symbol-spacing": ["interpolate", ["linear"], ["zoom"], 10, 120, 16, 400],
                            "text-field": ["get", "ref"],
                        },
                        "paint": {"icon-opacity": ["step", ["zoom"], 0, 6, 1]},
                    },
                    {
                        "id": "poi-label",
                        "type": "symbol",
                        "source-layer": "poi_label",
                        "layout": {
                            "icon-allow-overlap": False,
                            "icon-image": ["get", "maki"],
                            "icon-size": 1.0,
                            "text-field": ["get", "name"],
                        },
                        "paint": {"icon-opacity": 0.8},
                    },
                    {
                        "id": "hidden-icon",
                        "type": "symbol",
                        "source-layer": "poi_label",
                        "layout": {"visibility": "none", "icon-image": "park"},
                    },
                    {
                        "id": "empty-icon-label",
                        "type": "symbol",
                        "source-layer": "place_label",
                        "layout": {"icon-image": "", "text-field": ["get", "name"]},
                    },
                    {
                        "id": "text-only-label",
                        "type": "symbol",
                        "source-layer": "place_label",
                        "layout": {"text-field": ["get", "name"]},
                    },
                ],
            }
        )

        candidates = audit["summary"]["icon_sprite_candidates"]
        self.assertEqual([candidate["layer"] for candidate in candidates], ["poi-label", "road-shield", "empty-icon-label"])
        poi_candidate, road_candidate, empty_icon_candidate = candidates
        self.assertEqual(empty_icon_candidate["source_layer"], "place_label")
        self.assertEqual(empty_icon_candidate["icon_image_operator_signature"], "(none)")
        self.assertEqual(empty_icon_candidate["qfit_simplified_control_properties"], ["layout.icon-image"])
        self.assertEqual(empty_icon_candidate["qgis_dependent_control_properties"], [])
        self.assertEqual(poi_candidate["group"], "pois/labels")
        self.assertEqual(poi_candidate["source_layer"], "poi_label")
        self.assertEqual(poi_candidate["icon_image_operator_signature"], "get")
        self.assertEqual(
            poi_candidate["icon_sprite_control_properties"],
            ["layout.icon-allow-overlap", "layout.icon-image", "layout.icon-size", "paint.icon-opacity"],
        )
        self.assertEqual(poi_candidate["qfit_simplified_control_properties"], [])
        self.assertEqual(poi_candidate["qgis_dependent_control_properties"], ["layout.icon-image"])
        self.assertEqual(road_candidate["group"], "roads/trails")
        self.assertEqual(road_candidate["source_layer"], "road")
        self.assertEqual(road_candidate["zoom_band"], "z≥6")
        self.assertEqual(road_candidate["filter_operator_signature"], "<=, all, get, has")
        self.assertEqual(
            road_candidate["icon_image_operator_signature"],
            "concat, get, step, to-string, zoom",
        )
        self.assertEqual(
            road_candidate["icon_sprite_control_properties"],
            [
                "filter",
                "layout.icon-image",
                "layout.icon-rotation-alignment",
                "layout.symbol-placement",
                "layout.symbol-spacing",
                "paint.icon-opacity",
            ],
        )
        self.assertEqual(road_candidate["qfit_simplified_control_properties"], [])
        self.assertEqual(
            road_candidate["qgis_dependent_control_properties"],
            [
                "filter",
                "layout.icon-image",
                "layout.symbol-placement",
                "layout.symbol-spacing",
                "paint.icon-opacity",
            ],
        )
        self.assertEqual(
            audit["summary"]["icon_sprite_candidates_by_layer_group"],
            [
                {"group": "pois/labels", "count": 1},
                {"group": "roads/trails", "count": 1},
                {"group": "settlements/places", "count": 1},
            ],
        )
        self.assertEqual(
            audit["summary"]["icon_sprite_candidates_by_source_layer"],
            [
                {"source_layer": "place_label", "count": 1},
                {"source_layer": "poi_label", "count": 1},
                {"source_layer": "road", "count": 1},
            ],
        )
        self.assertEqual(
            audit["summary"]["icon_sprite_candidates_by_icon_image_operator_signature"],
            [
                {"icon_image_operator_signature": "(none)", "count": 1},
                {"icon_image_operator_signature": "concat, get, step, to-string, zoom", "count": 1},
                {"icon_image_operator_signature": "get", "count": 1},
            ],
        )
        self.assertEqual(audit["summary"]["icon_sprite_simplified_by_property"], [{"property": "layout.icon-image", "count": 1}])
        self.assertEqual(
            audit["summary"]["icon_sprite_qgis_dependent_by_property"],
            [
                {"property": "layout.icon-image", "count": 2},
                {"property": "filter", "count": 1},
                {"property": "layout.symbol-placement", "count": 1},
                {"property": "layout.symbol-spacing", "count": 1},
                {"property": "paint.icon-opacity", "count": 1},
            ],
        )

        markdown = build_audit_markdown(audit)
        self.assertIn("### Sprite/icon candidates", markdown)
        self.assertIn("Visible symbol layers with sprite-backed icons", markdown)
        self.assertIn("#### Sprite/icon candidates QGIS-dependent controls", markdown)
        self.assertIn("| `settlements/places` | `empty-icon-label` | `place_label` | all zooms | `(none)` | `(none)` |", markdown)
        self.assertIn("| `pois/labels` | `poi-label` | `poi_label` | all zooms | `(none)` | `get` |", markdown)
        self.assertIn("layout.icon-image<br>layout.icon-rotation-alignment", markdown)
        self.assertIn("filter<br>layout.icon-image<br>layout.symbol-placement", markdown)
        self.assertNotIn("hidden-icon` |", markdown)

    def test_build_style_audit_reports_route_overlay_candidates(self):
        audit = build_style_audit(
            {
                "version": 8,
                "layers": [
                    {
                        "id": "ferry",
                        "type": "line",
                        "source-layer": "road",
                        "minzoom": 8,
                        "filter": ["==", ["get", "type"], "ferry"],
                        "paint": {
                            "line-color": ["interpolate", ["linear"], ["zoom"], 10, "#79a8e8", 16, "#5978e8"],
                            "line-dasharray": ["step", ["zoom"], ["literal", [1, 0]], 12, ["literal", [2, 2]]],
                            "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.5, 14, 2],
                        },
                    },
                    {
                        "id": "ferry-aerialway-label",
                        "type": "symbol",
                        "source-layer": "road",
                        "minzoom": 15,
                        "filter": ["match", ["get", "class"], ["aerialway", "ferry"], True, False],
                        "layout": {
                            "symbol-placement": "line",
                            "text-field": ["get", "name"],
                            "text-padding": 2,
                        },
                        "paint": {"text-color": "#6670cc"},
                    },
                    {
                        "id": "transit-label",
                        "type": "symbol",
                        "source-layer": "transit_stop_label",
                        "minzoom": 12,
                        "layout": {"icon-image": ["get", "maki"], "text-field": ["get", "name"]},
                        "paint": {"icon-opacity": 0.85},
                    },
                    {
                        "id": "ferry-auto",
                        "type": "line",
                        "source-layer": "road",
                        "filter": ["==", ["get", "type"], "ferry_auto"],
                        "paint": {"line-color": "#79a8e8"},
                    },
                    {
                        "id": "hidden-ferry",
                        "type": "line",
                        "source-layer": "road",
                        "layout": {"visibility": "none"},
                        "filter": ["==", ["get", "type"], "ferry"],
                        "paint": {"line-color": "#79a8e8"},
                    },
                    {
                        "id": "ordinary-road",
                        "type": "line",
                        "source-layer": "road",
                        "paint": {"line-color": "#ffffff"},
                    },
                ],
            }
        )

        candidates = audit["summary"]["route_overlay_candidates"]
        self.assertEqual(
            [candidate["layer"] for candidate in candidates],
            ["ferry-aerialway-label", "ferry", "ferry-auto", "transit-label"],
        )
        aerialway_candidate, ferry_candidate, ferry_auto_candidate, transit_candidate = candidates
        self.assertEqual(aerialway_candidate["route_overlay_marker"], "aerialway, ferry")
        self.assertEqual(aerialway_candidate["route_overlay_markers"], ["aerialway", "ferry"])
        self.assertEqual(aerialway_candidate["type"], "symbol")
        self.assertEqual(aerialway_candidate["group"], "roads/trails")
        self.assertEqual(aerialway_candidate["filter_operator_signature"], "get, match")
        self.assertEqual(
            aerialway_candidate["route_overlay_control_properties"],
            ["filter", "layout.text-field", "layout.text-padding", "layout.symbol-placement", "paint.text-color"],
        )
        self.assertEqual(aerialway_candidate["qfit_simplified_control_properties"], [])
        self.assertEqual(aerialway_candidate["qgis_dependent_control_properties"], ["filter"])
        self.assertEqual(ferry_candidate["route_overlay_marker"], "ferry")
        self.assertEqual(ferry_candidate["type"], "line")
        self.assertEqual(ferry_candidate["source_layer"], "road")
        self.assertEqual(ferry_candidate["zoom_band"], "z≥8")
        self.assertEqual(ferry_candidate["filter_operator_signature"], "==, get")
        self.assertEqual(
            ferry_candidate["route_overlay_control_properties"],
            ["filter", "paint.line-color", "paint.line-dasharray", "paint.line-width"],
        )
        self.assertEqual(
            ferry_candidate["qfit_simplified_control_properties"],
            ["paint.line-color", "paint.line-dasharray", "paint.line-width"],
        )
        self.assertEqual(ferry_candidate["qgis_dependent_control_properties"], ["filter"])
        self.assertEqual(ferry_auto_candidate["route_overlay_marker"], "ferry_auto")
        self.assertEqual(ferry_auto_candidate["route_overlay_markers"], ["ferry_auto"])
        self.assertEqual(ferry_auto_candidate["qgis_dependent_control_properties"], ["filter"])
        self.assertEqual(transit_candidate["route_overlay_marker"], "transit")
        self.assertEqual(transit_candidate["group"], "pois/labels")
        self.assertEqual(transit_candidate["filter_operator_signature"], "(none)")
        self.assertEqual(
            transit_candidate["route_overlay_control_properties"],
            ["layout.text-field", "layout.icon-image", "paint.icon-opacity"],
        )
        self.assertEqual(transit_candidate["qfit_simplified_control_properties"], [])
        self.assertEqual(transit_candidate["qgis_dependent_control_properties"], ["layout.icon-image"])
        self.assertEqual(
            audit["summary"]["route_overlay_candidates_by_marker"],
            [
                {"route_overlay_marker": "ferry", "count": 2},
                {"route_overlay_marker": "aerialway", "count": 1},
                {"route_overlay_marker": "ferry_auto", "count": 1},
                {"route_overlay_marker": "transit", "count": 1},
            ],
        )
        self.assertEqual(
            audit["summary"]["route_overlay_candidates_by_layer_group"],
            [{"group": "roads/trails", "count": 3}, {"group": "pois/labels", "count": 1}],
        )
        self.assertEqual(
            audit["summary"]["route_overlay_candidates_by_source_layer"],
            [{"source_layer": "road", "count": 3}, {"source_layer": "transit_stop_label", "count": 1}],
        )
        self.assertEqual(
            audit["summary"]["route_overlay_candidates_by_type"],
            [{"type": "line", "count": 2}, {"type": "symbol", "count": 2}],
        )
        self.assertEqual(
            audit["summary"]["route_overlay_simplified_by_property"],
            [
                {"property": "paint.line-color", "count": 1},
                {"property": "paint.line-dasharray", "count": 1},
                {"property": "paint.line-width", "count": 1},
            ],
        )
        self.assertEqual(
            audit["summary"]["route_overlay_qgis_dependent_by_property"],
            [
                {"property": "filter", "count": 3},
                {"property": "layout.icon-image", "count": 1},
            ],
        )

        markdown = build_audit_markdown(audit)
        self.assertIn("### Route overlay candidates", markdown)
        self.assertIn("Visible ferry, ferry_auto, aerialway, piste, ski, golf, and transit line/symbol layers", markdown)
        self.assertIn("#### Route overlay candidates simplified/substituted by qfit", markdown)
        self.assertIn("| `aerialway, ferry` | `roads/trails` | `ferry-aerialway-label` | `symbol` | `road` | z≥15 |", markdown)
        self.assertIn("paint.line-color<br>paint.line-dasharray<br>paint.line-width", markdown)
        self.assertNotIn("hidden-ferry` | `line` |", markdown)

    def test_route_overlay_candidates_search_untruncated_filter_text(self):
        long_class_list = [f"ordinary-class-{index:02d}" for index in range(30)] + ["piste"]
        audit = build_style_audit(
            {
                "version": 8,
                "layers": [
                    {
                        "id": "winter-overlay",
                        "type": "line",
                        "source-layer": "road",
                        "filter": ["match", ["get", "type"], long_class_list, True, False],
                        "paint": {"line-color": "#3979d9"},
                    },
                ],
            }
        )

        candidates = audit["summary"]["route_overlay_candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["layer"], "winter-overlay")
        self.assertEqual(candidates[0]["route_overlay_marker"], "piste")
        self.assertEqual(candidates[0]["route_overlay_markers"], ["piste"])

    def test_build_style_audit_can_include_qgis_converter_warning_summary(self):
        warning_report = {
            "raw": {
                "count": 3,
                "warnings": [
                    "road-primary: Skipping unsupported expression",
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Referenced font DIN Pro Medium is not available on system",
                ],
            },
            "qfit_preprocessed": {
                "count": 2,
                "by_message": [
                    {"message": "Referenced font DIN Pro Medium is not available on system", "count": 1},
                    {"message": "Skipping unsupported expression", "count": 1},
                ],
                "by_layer_group": [{"group": "pois/labels", "count": 2}],
                "by_layer_group_and_message": [
                    {
                        "group": "pois/labels",
                        "message": "Referenced font DIN Pro Medium is not available on system",
                        "count": 1,
                    },
                    {
                        "group": "pois/labels",
                        "message": "Skipping unsupported expression",
                        "count": 1,
                    }
                ],
                "by_layer": [{"layer": "poi-label", "count": 2}],
                "warnings": [
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Referenced font DIN Pro Medium is not available on system",
                ],
            },
            "warning_count_delta": 1,
            "without_filters_probe": {
                "summary": {
                    "count": 1,
                    "warnings": ["poi-label: Skipping unsupported expression"],
                },
                "reduced_from_qfit": {},
            },
            "without_icon_images_probe": {
                "summary": {
                    "count": 1,
                    "warnings": ["poi-label: Could not retrieve sprite 'park'"],
                },
                "reduced_from_qfit": {},
            },
        }
        with patch.object(
            mapbox_outdoors_style_audit,
            "_qgis_converter_warning_report",
            return_value=warning_report,
        ) as report_mock:
            audit = build_style_audit(
                SAMPLE_STYLE,
                config=StyleAuditConfig(include_qgis_converter_warnings=True),
            )

        self.assertEqual(audit["qgis_converter_warnings"], warning_report)
        self.assertEqual(
            audit["qgis_converter_warnings"]["raw"]["by_layer_group"],
            [{"group": "pois/labels", "count": 2}, {"group": "roads/trails", "count": 1}],
        )
        self.assertEqual(
            audit["qgis_converter_warnings"]["raw"]["by_layer_group_and_message"],
            [
                {
                    "group": "pois/labels",
                    "message": "Referenced font DIN Pro Medium is not available on system",
                    "count": 1,
                },
                {"group": "pois/labels", "message": "Skipping unsupported expression", "count": 1},
                {"group": "roads/trails", "message": "Skipping unsupported expression", "count": 1},
            ],
        )
        self.assertEqual(
            audit["qgis_converter_warnings"]["qfit_preprocessed"]["by_layer_group"],
            [{"group": "pois/labels", "count": 2}],
        )
        self.assertEqual(
            audit["qgis_converter_warnings"]["qfit_preprocessed"]["by_layer_group_and_message"],
            [
                {
                    "group": "pois/labels",
                    "message": "Referenced font DIN Pro Medium is not available on system",
                    "count": 1,
                },
                {"group": "pois/labels", "message": "Skipping unsupported expression", "count": 1},
            ],
        )
        self.assertEqual(
            audit["qgis_converter_warnings"]["reduced_by_qfit"]["by_layer_group"],
            [{"group": "roads/trails", "raw_count": 1, "qfit_count": 0, "reduced_count": 1}],
        )
        self.assertEqual(
            audit["qgis_converter_warnings"]["without_filters_probe"]["summary"]["by_layer_group"],
            [{"group": "pois/labels", "count": 1}],
        )
        self.assertEqual(
            audit["qgis_converter_warnings"]["without_filters_probe"][
                "remaining_warning_layers_by_unresolved_property"
            ],
            {
                "by_property": [{"property": "layout.icon-image", "count": 1}],
                "by_layer_group_and_property": [
                    {"group": "pois/labels", "property": "layout.icon-image", "count": 1}
                ],
            },
        )
        self.assertEqual(
            audit["qgis_converter_warnings"]["without_icon_images_probe"]["summary"]["by_layer_group"],
            [{"group": "pois/labels", "count": 1}],
        )
        self.assertEqual(
            audit["qgis_converter_warnings"]["without_icon_images_probe"]["reduced_from_qfit"][
                "by_layer_group"
            ],
            [{"group": "pois/labels", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}],
        )
        layers = {layer["id"]: layer for layer in audit["layers"]}
        self.assertEqual(
            layers["poi-label"]["qgis_converter_warnings"],
            {
                "count": 2,
                "by_message": [
                    {"message": "Referenced font DIN Pro Medium is not available on system", "count": 1},
                    {"message": "Skipping unsupported expression", "count": 1},
                ],
                "warnings": [
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Referenced font DIN Pro Medium is not available on system",
                ],
            },
        )
        self.assertNotIn("qgis_converter_warnings", layers["road-primary"])
        report_mock.assert_called_once()
        self.assertIs(report_mock.call_args.kwargs["raw_style"], SAMPLE_STYLE)
        self.assertIsInstance(report_mock.call_args.kwargs["qfit_preprocessed_style"], dict)
        self.assertFalse(report_mock.call_args.kwargs["include_property_removal_impact"])
        self.assertFalse(report_mock.call_args.kwargs["include_filter_parse_support"])

    def test_build_style_audit_filter_parse_support_flag_implies_converter_warning_report(self):
        warning_report = {
            "raw": {"count": 0, "warnings": []},
            "qfit_preprocessed": {"count": 0, "warnings": []},
            "warning_count_delta": 0,
            "reduced_by_qfit": {},
            "filter_expression_parse_support_probe": {
                "filter_expression_count": 1,
                "qgis_parser_supported_count": 0,
                "qgis_parser_unsupported_count": 1,
                "unsupported_by_layer_group": [{"group": "pois/labels", "count": 1}],
                "unsupported_by_layer_group_and_operator_signature": [],
                "unsupported_layers": [],
            },
        }
        with patch.object(
            mapbox_outdoors_style_audit,
            "_qgis_converter_warning_report",
            return_value=warning_report,
        ) as report_mock:
            audit = build_style_audit(
                SAMPLE_STYLE,
                config=StyleAuditConfig(include_qgis_filter_parse_support=True),
            )

        self.assertIs(audit["qgis_converter_warnings"], warning_report)
        self.assertFalse(report_mock.call_args.kwargs["include_property_removal_impact"])
        self.assertTrue(report_mock.call_args.kwargs["include_filter_parse_support"])

    def test_qgis_warning_summary_counts_by_message_and_layer(self):
        summary = mapbox_outdoors_style_audit._qgis_warning_summary(
            [
                "road-primary: Skipping unsupported expression",
                "poi-label: Skipping unsupported expression",
                "poi-label: Referenced font DIN Pro Medium is not available on system",
                "Could not find sprite image",
            ]
        )

        self.assertEqual(summary["count"], 4)
        self.assertEqual(
            summary["by_message"],
            [
                {"message": "Skipping unsupported expression", "count": 2},
                {"message": "Could not find sprite image", "count": 1},
                {"message": "Referenced font DIN Pro Medium is not available on system", "count": 1},
            ],
        )
        self.assertEqual(
            summary["by_layer"],
            [
                {"layer": "poi-label", "count": 2},
                {"layer": "road-primary", "count": 1},
            ],
        )

    def test_filter_parse_unsupported_warning_count_handles_colon_in_layer_id(self):
        warnings = [
            "admin: label: Skipping unsupported expression",
            'admin: label: Skipping unsupported expression "within"',
            'road-label: Skipping unsupported expression part "case"',
            "road-label: Could not retrieve sprite 'park'",
        ]
        self.assertEqual(
            mapbox_outdoors_style_audit._filter_parse_unsupported_warning_count(warnings),
            3,
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._filter_parse_unsupported_message_summary(warnings),
            [
                {"message": "Skipping unsupported expression", "count": 1},
                {"message": 'Skipping unsupported expression "within"', "count": 1},
                {"message": 'Skipping unsupported expression part "case"', "count": 1},
            ],
        )

    def test_filter_operator_signature_omits_match_label_arrays(self):
        self.assertEqual(
            mapbox_outdoors_style_audit._operator_signature(
                [
                    "match",
                    ["get", "structure"],
                    ["none", "ford"],
                    True,
                    False,
                ]
            ),
            "get, match",
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._operator_signature(
                ["none", ["!has", "reflen"], ["!in", "class", "path"]]
            ),
            "!has, !in, none",
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._operator_signature(
                [
                    "match",
                    ["get", "class"],
                    "road",
                    ["case", ["has", "layer"], True, False],
                    False,
                ]
            ),
            "case, get, has, match",
        )

    def test_property_removal_impact_probe_ranks_expression_property_warning_deltas(self):
        style = {
            "version": 8,
            "layers": [
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": ["==", ["get", "class"], "road"],
                    "layout": {"text-field": ["get", "name"]},
                    "paint": {"line-opacity": ["step", ["zoom"], 0.4, 12, 1.0]},
                },
                {
                    "id": "poi-label",
                    "type": "symbol",
                    "layout": {"icon-image": ["get", "maki"]},
                },
            ],
        }
        qfit_summary = mapbox_outdoors_style_audit._qgis_warning_summary(
            [
                "road-label: Skipping unsupported expression",
                "poi-label: Could not retrieve sprite 'park'",
            ]
        )

        self.assertEqual(
            mapbox_outdoors_style_audit._removable_expression_property_paths(style),
            ["filter", "layout.icon-image", "layout.text-field", "paint.line-opacity"],
        )

        with patch.object(
            mapbox_outdoors_style_audit,
            "_collect_qgis_converter_warnings",
            side_effect=[
                ["poi-label: Could not retrieve sprite 'park'"],
                ["road-label: Skipping unsupported expression"],
            ],
        ):
            report = mapbox_outdoors_style_audit._qgis_property_removal_impact_report(
                style,
                qfit_summary,
                property_paths=["filter", "layout.icon-image"],
            )

        self.assertEqual(report["candidate_property_count"], 2)
        self.assertEqual(
            report["by_property"],
            [
                {
                    "property": "filter",
                    "property_count_removed": 1,
                    "warning_count_after_removal": 1,
                    "warning_count_delta_from_qfit": 1,
                    "skipping_unsupported_expression_delta": 1,
                    "reduced_from_qfit": {
                        "by_message": [
                            {
                                "message": "Skipping unsupported expression",
                                "raw_count": 1,
                                "qfit_count": 0,
                                "reduced_count": 1,
                            }
                        ],
                        "by_layer_group": [
                            {"group": "roads/trails", "raw_count": 1, "qfit_count": 0, "reduced_count": 1}
                        ],
                        "by_layer": [
                            {
                                "layer": "road-label",
                                "raw_count": 1,
                                "qfit_count": 0,
                                "reduced_count": 1,
                                "property_value": ["==", ["get", "class"], "road"],
                            }
                        ],
                    },
                },
                {
                    "property": "layout.icon-image",
                    "property_count_removed": 1,
                    "warning_count_after_removal": 1,
                    "warning_count_delta_from_qfit": 1,
                    "skipping_unsupported_expression_delta": 0,
                    "reduced_from_qfit": {
                        "by_message": [
                            {
                                "message": "Could not retrieve sprite 'park'",
                                "raw_count": 1,
                                "qfit_count": 0,
                                "reduced_count": 1,
                            }
                        ],
                        "by_layer_group": [
                            {"group": "pois/labels", "raw_count": 1, "qfit_count": 0, "reduced_count": 1}
                        ],
                        "by_layer": [
                            {
                                "layer": "poi-label",
                                "raw_count": 1,
                                "qfit_count": 0,
                                "reduced_count": 1,
                                "property_value": ["get", "maki"],
                            }
                        ],
                    },
                },
            ],
        )

    def test_style_without_property_path_removes_only_requested_property(self):
        style = {
            "version": 8,
            "layers": [
                {"id": "a", "filter": ["==", ["get", "class"], "road"], "paint": {"line-opacity": 0.5}},
                {"id": "b", "layout": {"icon-image": ["get", "maki"], "text-field": ["get", "name"]}},
                {"id": "c", "paint": {"line-opacity": ["step", ["zoom"], 0.4, 12, 1.0]}},
            ],
        }

        without_filter, filter_count = mapbox_outdoors_style_audit._style_without_property_path(style, "filter")
        without_icon, icon_count = mapbox_outdoors_style_audit._style_without_property_path(
            style,
            "layout.icon-image",
        )
        without_opacity, opacity_count = mapbox_outdoors_style_audit._style_without_property_path(
            style,
            "paint.line-opacity",
        )

        self.assertEqual(filter_count, 1)
        self.assertNotIn("filter", without_filter["layers"][0])
        self.assertIn("filter", style["layers"][0])
        self.assertEqual(icon_count, 1)
        self.assertNotIn("icon-image", without_icon["layers"][1]["layout"])
        self.assertIn("text-field", without_icon["layers"][1]["layout"])
        self.assertEqual(opacity_count, 1)
        self.assertIn("line-opacity", without_opacity["layers"][0]["paint"])
        self.assertNotIn("line-opacity", without_opacity["layers"][2]["paint"])

    def test_property_removal_impact_layer_reductions_include_positive_deltas_only(self):
        rows = [
            {
                "property": "filter",
                "warning_count_delta_from_qfit": 2,
                "reduced_from_qfit": {
                    "by_layer": [
                        {"layer": "road-label", "raw_count": 3, "qfit_count": 1, "reduced_count": 2},
                        {"layer": "poi-label", "raw_count": 2, "qfit_count": 1, "reduced_count": 1},
                    ]
                },
            },
            {
                "property": "layout.text-field",
                "warning_count_delta_from_qfit": -1,
                "reduced_from_qfit": {
                    "by_layer": [
                        {"layer": "hidden", "raw_count": 1, "qfit_count": 0, "reduced_count": 1}
                    ]
                },
            },
        ]

        self.assertEqual(
            mapbox_outdoors_style_audit._property_removal_impact_layer_reductions(
                rows,
                per_property_limit=2,
                total_limit=1,
            ),
            [
                {
                    "property": "filter",
                    "layer": "road-label",
                    "raw_count": 3,
                    "qfit_count": 1,
                    "reduced_count": 2,
                }
            ],
        )

    def test_warning_summary_layer_group_reductions_net_full_group_counts(self):
        layer_groups = mapbox_outdoors_style_audit._layer_groups_by_id(
            {
                "layers": [
                    {"id": "road-label", "type": "symbol", "source-layer": "road"},
                    {"id": "road-minor", "type": "line", "source-layer": "road"},
                    {"id": "poi-label", "type": "symbol", "source-layer": "poi_label"},
                ]
            }
        )
        self.assertEqual(
            layer_groups,
            {"road-label": "roads/trails", "road-minor": "roads/trails", "poi-label": "pois/labels"},
        )
        before = mapbox_outdoors_style_audit._qgis_warning_summary(
            ["road-label: Skipping unsupported expression", "poi-label: Could not retrieve sprite 'park'"]
        )
        after_same_group = mapbox_outdoors_style_audit._qgis_warning_summary(
            ["road-minor: Skipping unsupported expression", "poi-label: Could not retrieve sprite 'park'"]
        )
        after_reduced_group = mapbox_outdoors_style_audit._qgis_warning_summary(
            ["poi-label: Could not retrieve sprite 'park'"]
        )

        self.assertEqual(
            mapbox_outdoors_style_audit._warning_summary_layer_group_reductions(
                before,
                after_same_group,
                layer_groups,
            ),
            [],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._warning_summary_layer_group_reductions(
                before,
                after_reduced_group,
                layer_groups,
            ),
            [{"group": "roads/trails", "raw_count": 1, "qfit_count": 0, "reduced_count": 1}],
        )

    def test_property_removal_impact_group_reductions_include_positive_deltas_only(self):
        rows = [
            {
                "property": "filter",
                "warning_count_delta_from_qfit": 2,
                "reduced_from_qfit": {
                    "by_layer_group": [
                        {"group": "roads/trails", "raw_count": 3, "qfit_count": 1, "reduced_count": 2},
                        {"group": "pois/labels", "raw_count": 2, "qfit_count": 1, "reduced_count": 1},
                    ]
                },
            },
            {
                "property": "layout.text-field",
                "warning_count_delta_from_qfit": -1,
                "reduced_from_qfit": {
                    "by_layer_group": [
                        {"group": "other", "raw_count": 1, "qfit_count": 0, "reduced_count": 1}
                    ]
                },
            },
        ]

        self.assertEqual(
            mapbox_outdoors_style_audit._property_removal_impact_group_reductions(
                rows,
                per_property_limit=2,
                total_limit=1,
            ),
            [
                {
                    "property": "filter",
                    "group": "roads/trails",
                    "raw_count": 3,
                    "qfit_count": 1,
                    "reduced_count": 2,
                }
            ],
        )

    def test_expression_property_values_by_layer_keeps_expression_instances(self):
        style = {
            "version": 8,
            "layers": [
                {"id": "literal", "paint": {"line-opacity": 0.5}},
                {"id": "expr", "paint": {"line-opacity": ["step", ["zoom"], 0.4, 12, 1.0]}},
                {"id": "filter", "filter": ["==", ["get", "class"], "road"]},
            ],
        }
        layer_reductions = [
            {"layer": "expr", "raw_count": 2, "qfit_count": 1, "reduced_count": 1},
            {"layer": "literal", "raw_count": 1, "qfit_count": 0, "reduced_count": 1},
        ]

        self.assertEqual(
            mapbox_outdoors_style_audit._expression_property_values_by_layer(style, "paint.line-opacity"),
            {"expr": ["step", ["zoom"], 0.4, 12, 1.0]},
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_property_values_by_layer(style, "filter"),
            {"filter": ["==", ["get", "class"], "road"]},
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._layer_reductions_with_property_values(
                layer_reductions,
                {"expr": ["step", ["zoom"], 0.4, 12, 1.0]},
            ),
            [
                {
                    "layer": "expr",
                    "raw_count": 2,
                    "qfit_count": 1,
                    "reduced_count": 1,
                    "property_value": ["step", ["zoom"], 0.4, 12, 1.0],
                },
                {"layer": "literal", "raw_count": 1, "qfit_count": 0, "reduced_count": 1},
            ],
        )

    def test_markdown_property_impact_layer_table_escapes_expression_cells(self):
        self.assertEqual(
            mapbox_outdoors_style_audit._markdown_property_removal_impact_layer_table(
                [
                    {
                        "property": "layout.text-field",
                        "layer": "pipe-label",
                        "property_value": ["concat", ["get", "a|b"], "`suffix`"],
                        "raw_count": 2,
                        "qfit_count": 1,
                        "reduced_count": 1,
                    }
                ]
            ),
            [
                "##### Top warning reductions by property and layer",
                "",
                "| Property | Layer | Expression | Before removal | After removal | Reduced |",
                "| --- | --- | --- | ---: | ---: | ---: |",
                (
                    "| `layout.text-field` | `pipe-label` | "
                    '<code>["concat",["get","a&#124;b"],"`suffix`"]</code> | 2 | 1 | 1 |'
                ),
                "",
            ],
        )

    def test_expression_operator_names_ignore_literal_string_arrays(self):
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_operator_names(
                ["match", ["get", "class"], ["primary", "secondary"], "#fff", "#ccc"]
            ),
            ["get", "match"],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_operator_names(
                ["match", ["get", "worldview"], ["all", "US"], True, False]
            ),
            ["get", "match"],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_operator_names(
                ["match", ["get", "class"], "road", ["case", ["has", "layer"], True, False], False]
            ),
            ["case", "get", "has", "match"],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_operator_names(["DIN Pro Medium", "Arial Unicode MS Regular"]),
            [],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_operator_names(
                ["in", ["get", "class"], ["literal", ["primary", "secondary"]]]
            ),
            ["get", "in", "literal"],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_operator_names(["literal", ["step", 1, 2]]),
            ["literal"],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_operator_names(
                ["slice", ["get", "ref"], ["index-of", "A", ["get", "ref"]]]
            ),
            ["get", "index-of", "slice"],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_operator_names(["accumulated"]),
            ["accumulated"],
        )

    def test_expression_operator_names_include_current_mapbox_reference_operators(self):
        reference_operators = [
            "at-interpolated",
            "distance-from-center",
            "hsl",
            "hsla",
            "number-format",
            "pitch",
            "random",
            "split",
            "to-hsla",
            "to-rgba",
            "worldview",
        ]
        for operator in reference_operators:
            with self.subTest(operator=operator):
                self.assertEqual(mapbox_outdoors_style_audit._expression_operator_names([operator]), [operator])

    def test_qgis_warning_summaries_by_layer_skip_unprefixed_warnings(self):
        summaries = mapbox_outdoors_style_audit._qgis_warning_summaries_by_layer(
            [
                "road-primary: Skipping unsupported expression",
                "poi-label: Skipping unsupported expression",
                "Could not find sprite image",
            ]
        )

        self.assertEqual(sorted(summaries), ["poi-label", "road-primary"])
        self.assertEqual(summaries["poi-label"]["count"], 1)
        self.assertEqual(
            summaries["road-primary"]["by_message"],
            [{"message": "Skipping unsupported expression", "count": 1}],
        )

    def test_warning_group_count_summary_skips_unprefixed_warnings(self):
        self.assertEqual(
            mapbox_outdoors_style_audit._warning_group_count_summary(
                [
                    "road-primary: Skipping unsupported expression",
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Referenced font DIN Pro Medium is not available on system",
                    "Could not find sprite image",
                ],
                {"road-primary": "roads/trails", "poi-label": "pois/labels"},
            ),
            [{"group": "pois/labels", "count": 2}, {"group": "roads/trails", "count": 1}],
        )

    def test_warning_group_message_count_summary_skips_unprefixed_warnings(self):
        self.assertEqual(
            mapbox_outdoors_style_audit._warning_group_message_count_summary(
                [
                    "road-primary: Skipping unsupported expression",
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Referenced font DIN Pro Medium is not available on system",
                    "poi-label: Skipping unsupported expression",
                    "Could not find sprite image",
                ],
                {"road-primary": "roads/trails", "poi-label": "pois/labels"},
            ),
            [
                {"group": "pois/labels", "message": "Skipping unsupported expression", "count": 2},
                {
                    "group": "pois/labels",
                    "message": "Referenced font DIN Pro Medium is not available on system",
                    "count": 1,
                },
                {"group": "roads/trails", "message": "Skipping unsupported expression", "count": 1},
            ],
        )

    def test_warning_layer_unresolved_property_summaries_skip_filters_for_probe(self):
        layers = [
            {
                "id": "road-primary",
                "group": "roads/trails",
                "qfit_unresolved": [
                    {"property": "filter"},
                    {"property": "paint.line-opacity"},
                ],
            },
            {
                "id": "poi-label",
                "group": "pois/labels",
                "qfit_unresolved": [
                    {"property": "filter"},
                    {"property": "layout.icon-image"},
                    {"property": "layout.text-font"},
                ],
            },
            {
                "id": "building",
                "group": "boundaries/buildings",
                "qfit_unresolved": [{"property": "fill-opacity"}],
            },
        ]

        self.assertEqual(
            mapbox_outdoors_style_audit._warning_layer_unresolved_property_summaries(
                [
                    "road-primary: Skipping unsupported expression",
                    "poi-label: Could not retrieve sprite 'marker'",
                    "Could not find sprite sheet",
                ],
                layers,
                exclude_properties={"filter"},
            ),
            {
                "by_property": [
                    {"property": "layout.icon-image", "count": 1},
                    {"property": "layout.text-font", "count": 1},
                    {"property": "paint.line-opacity", "count": 1},
                ],
                "by_layer_group_and_property": [
                    {"group": "pois/labels", "property": "layout.icon-image", "count": 1},
                    {"group": "pois/labels", "property": "layout.text-font", "count": 1},
                    {"group": "roads/trails", "property": "paint.line-opacity", "count": 1},
                ],
            },
        )

    def test_filter_expression_signature_group_summary_keeps_examples(self):
        layers = [
            {
                "id": "road-primary",
                "group": "roads/trails",
                "qfit_unresolved": [
                    {"property": "filter", "expression_operators": ["==", "all", "get"]}
                ],
            },
            {
                "id": "road-secondary",
                "group": "roads/trails",
                "qfit_unresolved": [
                    {"property": "filter", "expression_operators": ["==", "all", "get"]}
                ],
            },
            {
                "id": "poi-label",
                "group": "pois/labels",
                "qfit_unresolved": [
                    {"property": "filter", "expression_operators": ["==", "get"]},
                    {"property": "layout.icon-image", "expression_operators": ["get"]},
                ],
            },
        ]

        self.assertEqual(
            mapbox_outdoors_style_audit._filter_expression_signature_group_summary(layers),
            [
                {
                    "group": "roads/trails",
                    "operators": ["==", "all", "get"],
                    "operator_signature": "==, all, get",
                    "count": 2,
                    "example_layers": ["road-primary", "road-secondary"],
                },
                {
                    "group": "pois/labels",
                    "operators": ["==", "get"],
                    "operator_signature": "==, get",
                    "count": 1,
                    "example_layers": ["poi-label"],
                },
            ],
        )

    def test_style_without_icon_images_removes_layout_icons_without_mutating_original(self):
        style = {
            "layers": [
                {"id": "poi-label", "layout": {"icon-image": ["get", "maki"], "text-field": ["get", "name"]}},
                {"id": "road-label", "layout": {"text-field": ["get", "name"]}},
                {"id": "background", "paint": {"background-color": "#ffffff"}},
            ]
        }

        result, removed_count = mapbox_outdoors_style_audit._style_without_icon_images(style)

        self.assertEqual(removed_count, 1)
        self.assertNotIn("icon-image", result["layers"][0]["layout"])
        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name"])
        self.assertIn("icon-image", style["layers"][0]["layout"])

    def test_style_with_scalar_line_opacity_replaces_supported_expressions_without_mutating_original(self):
        style = {
            "layers": [
                {
                    "id": "waterway",
                    "paint": {"line-opacity": ["interpolate", ["linear"], ["zoom"], 8, 0, 14, 1]},
                },
                {"id": "road", "paint": {"line-opacity": ["step", ["zoom"], 0, 11, 1]}},
                {
                    "id": "contour",
                    "paint": {
                        "line-opacity": [
                            "interpolate",
                            ["linear"],
                            ["zoom"],
                            11,
                            ["match", ["get", "index"], [1, 2], 0.15, 0.3],
                            13,
                            ["match", ["get", "index"], [1, 2], 0.3, 0.5],
                        ]
                    },
                },
                {"id": "literal", "paint": {"line-opacity": 0.2}},
                {"id": "data-only", "paint": {"line-opacity": ["get", "opacity"]}},
                {"id": "data-step", "paint": {"line-opacity": ["step", ["get", "rank"], 0.2, 10, 0.8]}},
                {
                    "id": "data-interpolate",
                    "paint": {
                        "line-opacity": ["interpolate", ["linear"], ["get", "altitude"], 0, 0.2, 3000, 1.0]
                    },
                },
            ]
        }

        result, replaced_count = mapbox_outdoors_style_audit._style_with_scalar_line_opacity(style)
        _details_result, replacement_rows = mapbox_outdoors_style_audit._style_with_scalar_line_opacity_details(style)

        self.assertEqual(replaced_count, 5)
        self.assertEqual(len(replacement_rows), 5)
        contour_row = next(row for row in replacement_rows if row["layer"] == "contour")
        self.assertEqual(contour_row["group"], "terrain/landcover")
        self.assertEqual(contour_row["operator_signature"], "get, interpolate, match, zoom")
        self.assertEqual(contour_row["scalar_line_opacity"], 0.3)
        self.assertEqual(result["layers"][0]["paint"]["line-opacity"], 1.0)
        self.assertEqual(result["layers"][1]["paint"]["line-opacity"], 1.0)
        self.assertEqual(result["layers"][2]["paint"]["line-opacity"], 0.3)
        self.assertEqual(result["layers"][3]["paint"]["line-opacity"], 0.2)
        self.assertEqual(result["layers"][4]["paint"]["line-opacity"], ["get", "opacity"])
        self.assertEqual(result["layers"][5]["paint"]["line-opacity"], 0.2)
        self.assertEqual(result["layers"][6]["paint"]["line-opacity"], 0.2)
        self.assertIsInstance(style["layers"][0]["paint"]["line-opacity"], list)

    def test_style_with_literal_line_dasharray_replaces_supported_expressions_without_mutating_original(self):
        style = {
            "layers": [
                {
                    "id": "path",
                    "paint": {
                        "line-dasharray": ["step", ["zoom"], ["literal", [3, 3]], 12, ["literal", [4, 4]]]
                    },
                },
                {
                    "id": "rail",
                    "paint": {
                        "line-dasharray": [
                            "interpolate",
                            ["linear"],
                            ["zoom"],
                            10,
                            ["literal", [1, 2]],
                            14,
                            ["literal", [2, 4]],
                        ]
                    },
                },
                {
                    "id": "fence",
                    "paint": {
                        "line-dasharray": [
                            "case",
                            ["==", ["get", "class"], "gate"],
                            ["literal", [1, 1]],
                            ["literal", [2, 2]],
                        ]
                    },
                },
                {"id": "literal", "paint": {"line-dasharray": [5, 2]}},
                {"id": "literal-expression", "paint": {"line-dasharray": ["literal", [2, 1]]}},
                {
                    "id": "malformed-match",
                    "paint": {"line-dasharray": ["match", ["get", "class"], "primary", ["literal", [9, 9]]]},
                },
                {
                    "id": "case-literal-condition",
                    "paint": {
                        "line-dasharray": [
                            "case",
                            ["literal", [9, 9]],
                            ["get", "dash"],
                            ["get", "fallbackDash"],
                        ]
                    },
                },
                {"id": "data-only", "paint": {"line-dasharray": ["get", "dash"]}},
            ]
        }

        result, replaced_count = mapbox_outdoors_style_audit._style_with_literal_line_dasharray(style)

        self.assertEqual(replaced_count, 4)
        self.assertEqual(result["layers"][0]["paint"]["line-dasharray"], [4, 4])
        self.assertEqual(result["layers"][1]["paint"]["line-dasharray"], [1, 2])
        self.assertEqual(result["layers"][2]["paint"]["line-dasharray"], [2, 2])
        self.assertEqual(result["layers"][3]["paint"]["line-dasharray"], [5, 2])
        self.assertEqual(result["layers"][4]["paint"]["line-dasharray"], [2, 1])
        self.assertEqual(result["layers"][5]["paint"]["line-dasharray"], ["match", ["get", "class"], "primary", ["literal", [9, 9]]])
        self.assertEqual(
            result["layers"][6]["paint"]["line-dasharray"],
            ["case", ["literal", [9, 9]], ["get", "dash"], ["get", "fallbackDash"]],
        )
        self.assertEqual(result["layers"][7]["paint"]["line-dasharray"], ["get", "dash"])
        self.assertEqual(style["layers"][0]["paint"]["line-dasharray"][0], "step")

    def test_style_with_scalar_symbol_spacing_replaces_supported_expressions_without_mutating_original(self):
        style = {
            "layers": [
                {
                    "id": "road-label",
                    "layout": {"symbol-spacing": ["step", ["zoom"], 150, 12, 300]},
                },
                {
                    "id": "water-label",
                    "layout": {
                        "symbol-spacing": ["interpolate", ["linear"], ["zoom"], 8, 100, 12, 250, 16, 400]
                    },
                },
                {
                    "id": "trail-label",
                    "layout": {
                        "symbol-spacing": [
                            "case",
                            ["==", ["get", "class"], "minor"],
                            90,
                            180,
                        ]
                    },
                },
                {"id": "poi-label", "layout": {"symbol-spacing": ["coalesce", ["get", "spacing"], 220]}},
                {
                    "id": "settlement-label",
                    "layout": {"symbol-spacing": ["match", ["get", "rank"], 1, 400, 120]},
                },
                {"id": "literal", "layout": {"symbol-spacing": 250}},
                {"id": "data-only", "layout": {"symbol-spacing": ["get", "spacing"]}},
                {"id": "negative", "layout": {"symbol-spacing": ["step", ["zoom"], -1, 12, -2]}},
                {"id": "background", "paint": {"background-color": "#ffffff"}},
            ]
        }

        result, replaced_count = mapbox_outdoors_style_audit._style_with_scalar_symbol_spacing(style)

        self.assertEqual(replaced_count, 5)
        self.assertEqual(result["layers"][0]["layout"]["symbol-spacing"], 300.0)
        self.assertEqual(result["layers"][1]["layout"]["symbol-spacing"], 250.0)
        self.assertEqual(result["layers"][2]["layout"]["symbol-spacing"], 180.0)
        self.assertEqual(result["layers"][3]["layout"]["symbol-spacing"], 220.0)
        self.assertEqual(result["layers"][4]["layout"]["symbol-spacing"], 120.0)
        self.assertEqual(result["layers"][5]["layout"]["symbol-spacing"], 250)
        self.assertEqual(result["layers"][6]["layout"]["symbol-spacing"], ["get", "spacing"])
        self.assertEqual(result["layers"][7]["layout"]["symbol-spacing"], ["step", ["zoom"], -1, 12, -2])
        self.assertEqual(style["layers"][0]["layout"]["symbol-spacing"][0], "step")

    def test_qgis_converter_warning_report_initializes_and_closes_qgis_app(self):
        raw_style = {"layers": []}
        qfit_style = {
            "layers": [
                {
                    "id": "poi-label",
                    "filter": ["==", ["get", "maki"], "park"],
                    "layout": {"icon-image": ["get", "maki"]},
                }
            ]
        }
        fake_qgis, fake_core, fake_app, fake_converter = _fake_qgis_modules(
            [
                [
                    "road-primary: Skipping unsupported expression",
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Could not retrieve sprite 'park'",
                ],
                [
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Could not retrieve sprite 'park'",
                ],
                ["poi-label: Could not retrieve sprite 'park'"],
                ["poi-label: Skipping unsupported expression"],
                [
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Could not retrieve sprite 'park'",
                ],
                [
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Could not retrieve sprite 'park'",
                ],
                [
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Could not retrieve sprite 'park'",
                ],
            ]
        )

        with patch.dict(sys.modules, {"qgis": fake_qgis, "qgis.core": fake_core}), patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop("QT_QPA_PLATFORM", None)
            report = mapbox_outdoors_style_audit._qgis_converter_warning_report(
                raw_style=raw_style,
                qfit_preprocessed_style=qfit_style,
            )

        self.assertEqual(report["raw"]["count"], 3)
        self.assertEqual(report["qfit_preprocessed"]["count"], 2)
        self.assertEqual(report["warning_count_delta"], 1)
        self.assertEqual(report["without_filters_probe"]["filter_count_removed"], 1)
        self.assertEqual(report["without_filters_probe"]["summary"]["count"], 1)
        self.assertEqual(report["without_filters_probe"]["warning_count_delta_from_qfit"], 1)
        self.assertEqual(
            report["without_filters_probe"]["reduced_from_qfit"],
            {
                "by_message": [
                    {
                        "message": "Skipping unsupported expression",
                        "raw_count": 1,
                        "qfit_count": 0,
                        "reduced_count": 1,
                    }
                ],
                "by_layer": [
                    {"layer": "poi-label", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}
                ],
            },
        )
        self.assertEqual(report["without_icon_images_probe"]["icon_image_count_removed"], 1)
        self.assertEqual(report["without_icon_images_probe"]["summary"]["count"], 1)
        self.assertEqual(report["without_icon_images_probe"]["warning_count_delta_from_qfit"], 1)
        self.assertEqual(
            report["without_icon_images_probe"]["reduced_from_qfit"],
            {
                "by_message": [
                    {
                        "message": "Could not retrieve sprite 'park'",
                        "raw_count": 1,
                        "qfit_count": 0,
                        "reduced_count": 1,
                    }
                ],
                "by_layer": [
                    {"layer": "poi-label", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}
                ],
            },
        )
        self.assertEqual(report["with_scalar_line_opacity_probe"]["line_opacity_expression_count_replaced"], 0)
        self.assertEqual(report["with_scalar_line_opacity_probe"]["line_opacity_scalarization_rows"], [])
        self.assertEqual(report["with_scalar_line_opacity_probe"]["summary"]["count"], 2)
        self.assertEqual(report["with_scalar_line_opacity_probe"]["warning_count_delta_from_qfit"], 0)
        self.assertEqual(report["with_scalar_line_opacity_probe"]["reduced_from_qfit"], {"by_message": [], "by_layer": []})
        self.assertEqual(report["with_literal_line_dasharray_probe"]["line_dasharray_expression_count_replaced"], 0)
        self.assertEqual(report["with_literal_line_dasharray_probe"]["summary"]["count"], 2)
        self.assertEqual(report["with_literal_line_dasharray_probe"]["warning_count_delta_from_qfit"], 0)
        self.assertEqual(
            report["with_literal_line_dasharray_probe"]["reduced_from_qfit"],
            {"by_message": [], "by_layer": []},
        )
        self.assertEqual(report["with_scalar_symbol_spacing_probe"]["symbol_spacing_expression_count_replaced"], 0)
        self.assertEqual(report["with_scalar_symbol_spacing_probe"]["symbol_spacing_replaced_layers"], [])
        self.assertEqual(report["with_scalar_symbol_spacing_probe"]["summary"]["count"], 2)
        self.assertEqual(report["with_scalar_symbol_spacing_probe"]["warning_count_delta_from_qfit"], 0)
        self.assertEqual(
            report["with_scalar_symbol_spacing_probe"]["reduced_from_qfit"],
            {"by_message": [], "by_layer": []},
        )
        self.assertEqual(
            report["reduced_by_qfit"],
            {
                "by_message": [
                    {
                        "message": "Skipping unsupported expression",
                        "raw_count": 2,
                        "qfit_count": 1,
                        "reduced_count": 1,
                    }
                ],
                "by_layer": [
                    {"layer": "road-primary", "raw_count": 1, "qfit_count": 0, "reduced_count": 1}
                ],
            },
        )
        self.assertEqual(fake_converter.converted_styles[:2], [raw_style, qfit_style])
        self.assertEqual(
            fake_converter.converted_styles[2],
            {"layers": [{"id": "poi-label", "layout": {"icon-image": ["get", "maki"]}}]},
        )
        self.assertEqual(
            fake_converter.converted_styles[3],
            {"layers": [{"id": "poi-label", "filter": ["==", ["get", "maki"], "park"], "layout": {}}]},
        )
        self.assertEqual(fake_converter.converted_styles[4], qfit_style)
        self.assertEqual(fake_converter.converted_styles[5], qfit_style)
        self.assertEqual(fake_converter.converted_styles[6], qfit_style)
        self.assertIn("filter", qfit_style["layers"][0])
        self.assertIn("icon-image", qfit_style["layers"][0]["layout"])
        self.assertEqual(len(fake_app.created), 1)
        self.assertEqual(fake_app.created[0].args, [])
        self.assertFalse(fake_app.created[0].gui_enabled)
        self.assertEqual(fake_app.created[0].init_qgis_calls, 1)
        self.assertEqual(fake_app.created[0].exit_qgis_calls, 1)

    def test_qgis_converter_warning_report_reuses_existing_qgis_app(self):
        existing_app = object()
        fake_qgis, fake_core, fake_app, _fake_converter = _fake_qgis_modules(
            [
                ["raw warning"],
                ["qfit warning"],
                ["filterless warning"],
                ["iconless warning"],
                ["line opacity warning"],
                ["line dasharray warning"],
                ["symbol spacing warning"],
            ],
            existing_app=existing_app,
        )

        with patch.dict(sys.modules, {"qgis": fake_qgis, "qgis.core": fake_core}):
            report = mapbox_outdoors_style_audit._qgis_converter_warning_report(
                raw_style={"layers": []},
                qfit_preprocessed_style={"layers": []},
            )

        self.assertEqual(report["raw"]["warnings"], ["raw warning"])
        self.assertEqual(report["qfit_preprocessed"]["warnings"], ["qfit warning"])
        self.assertEqual(report["without_filters_probe"]["summary"]["warnings"], ["filterless warning"])
        self.assertEqual(report["without_icon_images_probe"]["summary"]["warnings"], ["iconless warning"])
        self.assertEqual(report["with_scalar_line_opacity_probe"]["summary"]["warnings"], ["line opacity warning"])
        self.assertEqual(report["with_literal_line_dasharray_probe"]["summary"]["warnings"], ["line dasharray warning"])
        self.assertEqual(report["with_scalar_symbol_spacing_probe"]["summary"]["warnings"], ["symbol spacing warning"])
        self.assertEqual(fake_app.created, [])

    def test_qgis_converter_warning_report_can_include_filter_parse_support_probe(self):
        raw_style = {"version": 8, "sources": {"composite": {"type": "vector"}}, "layers": []}
        qfit_style = {
            "version": 8,
            "sources": {"composite": {"type": "vector"}},
            "layers": [
                {
                    "id": "road-primary",
                    "type": "line",
                    "source": "composite",
                    "source-layer": "road",
                    "filter": ["all", [">", ["get", "len"], 0], ["==", ["geometry-type"], "LineString"]],
                    "paint": {"line-color": "#ffffff", "line-width": 1},
                },
                {
                    "id": "poi-label",
                    "type": "symbol",
                    "source": "composite",
                    "source-layer": "poi_label",
                    "filter": ["case", ["==", ["get", "class"], "park"], True, False],
                    "layout": {"icon-image": ["get", "maki"]},
                },
                {
                    "id": "legacy-filter",
                    "type": "line",
                    "source": "composite",
                    "source-layer": "road",
                    "filter": ["none", ["!has", "reflen"], ["!in", "class", "path"]],
                    "paint": {"line-color": "#ffffff", "line-width": 1},
                },
            ],
        }
        fake_qgis, fake_core, _fake_app, fake_converter = _fake_qgis_modules(
            [
                [],
                [],
                [],
                [],
                [],
                [],
                [],
                [],
                [
                    "poi-label: Skipping unsupported expression",
                    'poi-label: Skipping unsupported expression part "case"',
                    "poi-label: Some other warning",
                ],
                [],
                [],
            ]
        )

        with patch.dict(sys.modules, {"qgis": fake_qgis, "qgis.core": fake_core}):
            report = mapbox_outdoors_style_audit._qgis_converter_warning_report(
                raw_style=raw_style,
                qfit_preprocessed_style=qfit_style,
                include_filter_parse_support=True,
            )

        probe = report["filter_expression_parse_support_probe"]
        self.assertEqual(probe["filter_expression_count"], 3)
        self.assertEqual(probe["qgis_parser_supported_count"], 2)
        self.assertEqual(probe["qgis_parser_unsupported_count"], 1)
        self.assertEqual(probe["parser_friendly_filter_count"], 1)
        self.assertEqual(probe["parser_friendly_changed_filter_count"], 1)
        self.assertEqual(probe["qgis_parser_supported_parser_friendly_filter_count"], 1)
        self.assertEqual(probe["qgis_parser_unsupported_parser_friendly_filter_count"], 0)
        self.assertEqual(probe["unsupported_by_layer_group"], [{"group": "pois/labels", "count": 1}])
        self.assertEqual(
            probe["unsupported_by_warning_message"],
            [
                {"message": "Skipping unsupported expression", "count": 1},
                {"message": 'Skipping unsupported expression part "case"', "count": 1},
            ],
        )
        self.assertEqual(
            probe["unsupported_by_layer_group_and_operator_signature"],
            [
                {
                    "group": "pois/labels",
                    "operator_signature": "==, case, get",
                    "count": 1,
                    "example_layers": ["poi-label"],
                }
            ],
        )
        self.assertEqual(probe["unsupported_layers"][0]["layer"], "poi-label")
        self.assertEqual(probe["unsupported_layers"][0]["unsupported_warning_count"], 2)
        self.assertEqual(
            probe["unsupported_layers"][0]["unsupported_warning_messages"],
            [
                {"message": "Skipping unsupported expression", "count": 1},
                {"message": 'Skipping unsupported expression part "case"', "count": 1},
            ],
        )
        self.assertFalse(probe["unsupported_layers"][0]["supported_by_qgis_parser"])
        self.assertEqual(
            probe["unsupported_layers"][0]["warnings"],
            [
                "poi-label: Skipping unsupported expression",
                'poi-label: Skipping unsupported expression part "case"',
                "poi-label: Some other warning",
            ],
        )
        self.assertEqual(
            fake_converter.converted_styles[7],
            {
                "version": 8,
                "sources": {"composite": {"type": "vector"}},
                "layers": [
                    {
                        "id": "road-primary",
                        "type": "line",
                        "filter": [
                            "all",
                            [">", ["get", "len"], 0],
                            ["==", ["geometry-type"], "LineString"],
                        ],
                        "source": "composite",
                        "source-layer": "road",
                        "paint": {"line-color": "#000000", "line-width": 1},
                    }
                ],
            },
        )
        self.assertEqual(fake_converter.converted_styles[8]["layers"][0]["layout"]["text-field"], "filter-probe")
        self.assertNotIn("icon-image", fake_converter.converted_styles[8]["layers"][0]["layout"])
        self.assertEqual(
            fake_converter.converted_styles[9]["layers"][0]["filter"],
            ["none", ["!has", "reflen"], ["!in", "class", "path"]],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._operator_signature(
                fake_converter.converted_styles[9]["layers"][0]["filter"]
            ),
            "!has, !in, none",
        )
        self.assertEqual(fake_converter.converted_styles[10]["layers"][0]["filter"], ["==", ["get", "class"], "park"])

    def test_filter_parse_support_report_summarizes_unsupported_direct_filter_parts(self):
        style = {
            "version": 8,
            "sources": {"composite": {"type": "vector"}},
            "layers": [
                {
                    "id": "poi-label",
                    "type": "symbol",
                    "source": "composite",
                    "source-layer": "poi_label",
                    "filter": [
                        "all",
                        ["==", ["get", "class"], "park"],
                        ["step", ["zoom"], False, 12, True],
                    ],
                }
            ],
        }

        with patch.object(
            mapbox_outdoors_style_audit,
            "_collect_qgis_converter_warnings",
            side_effect=[
                ["poi-label: Skipping unsupported expression"],
                [],
                ["poi-label: Skipping unsupported expression"],
                [],
                [],
            ],
        ) as collect_warnings:
            report = mapbox_outdoors_style_audit._qgis_filter_parse_support_report(style)

        self.assertEqual(report["parser_friendly_filter_count"], 1)
        self.assertEqual(report["parser_friendly_changed_filter_count"], 1)
        self.assertEqual(report["qgis_parser_supported_parser_friendly_filter_count"], 1)
        self.assertEqual(report["qgis_parser_unsupported_parser_friendly_filter_count"], 0)
        self.assertEqual(len(report["parser_friendly_supported_filters"]), 1)
        self.assertEqual(report["parser_friendly_unsupported_filters"], [])
        self.assertEqual(
            report["parser_friendly_supported_filters"][0]["filter"],
            ["all", ["==", ["get", "class"], "park"], True],
        )
        self.assertEqual(
            report["parser_friendly_supported_filters_by_layer_group_and_operator_signature"],
            [
                {
                    "group": "pois/labels",
                    "operator_signature": "==, all, get",
                    "count": 1,
                    "example_layers": ["poi-label"],
                }
            ],
        )
        self.assertEqual(report["direct_filter_part_count"], 2)
        self.assertEqual(report["qgis_parser_supported_part_count"], 1)
        self.assertEqual(report["qgis_parser_unsupported_part_count"], 1)
        self.assertEqual(report["zoom_normalized_direct_part_count"], 1)
        self.assertEqual(report["zoom_normalized_changed_direct_part_count"], 1)
        self.assertEqual(report["qgis_parser_supported_zoom_normalized_part_count"], 1)
        self.assertEqual(report["qgis_parser_unsupported_zoom_normalized_part_count"], 0)
        self.assertEqual(report["zoom_normalized_unsupported_parts_by_layer_group_and_operator_signature"], [])
        self.assertEqual(
            report["unsupported_parts_by_layer_group_and_operator_signature"],
            [
                {
                    "group": "pois/labels",
                    "operator_signature": "step, zoom",
                    "count": 1,
                    "example_layers": ["poi-label"],
                }
            ],
        )
        self.assertEqual(len(report["unsupported_parts"]), 1)
        self.assertEqual(report["unsupported_parts"][0]["layer"], "poi-label")
        self.assertEqual(report["unsupported_parts"][0]["parent_operator"], "all")
        self.assertEqual(report["unsupported_parts"][0]["part_index"], 2)
        self.assertEqual(report["unsupported_parts"][0]["operator_signature"], "step, zoom")
        self.assertEqual(len(report["zoom_normalized_supported_parts"]), 1)
        self.assertEqual(report["zoom_normalized_unsupported_parts"], [])
        self.assertTrue(report["zoom_normalized_supported_parts"][0]["filter"])
        self.assertEqual(report["zoom_normalized_supported_parts"][0]["original_operator_signature"], "step, zoom")
        self.assertEqual(report["zoom_normalized_supported_parts"][0]["operator_signature"], "(none)")
        self.assertEqual(
            collect_warnings.call_args_list[2].args[0]["layers"][0]["filter"],
            ["step", ["zoom"], False, 12, True],
        )
        self.assertEqual(
            collect_warnings.call_args_list[3].args[0]["layers"][0]["filter"],
            ["all", ["==", ["get", "class"], "park"], True],
        )
        self.assertTrue(collect_warnings.call_args_list[4].args[0]["layers"][0]["filter"])

    def test_filter_parse_support_report_lists_zoom_normalized_rejections(self):
        style = {
            "version": 8,
            "sources": {"composite": {"type": "vector"}},
            "layers": [
                {
                    "id": "road-label",
                    "type": "line",
                    "source": "composite",
                    "source-layer": "road",
                    "filter": [
                        "all",
                        [
                            "!",
                            ["match", ["get", "type"], ["steps", "sidewalk"], True, False],
                        ],
                    ],
                }
            ],
        }

        with patch.object(
            mapbox_outdoors_style_audit,
            "_collect_qgis_converter_warnings",
            side_effect=[
                ["road-label: Skipping unsupported expression"],
                ["road-label: Skipping unsupported expression"],
                [],
                ["road-label: Skipping unsupported expression"],
                [],
            ],
        ):
            report = mapbox_outdoors_style_audit._qgis_filter_parse_support_report(style)

        self.assertEqual(report["parser_friendly_filter_count"], 1)
        self.assertEqual(report["parser_friendly_changed_filter_count"], 1)
        self.assertEqual(report["qgis_parser_supported_parser_friendly_filter_count"], 1)
        self.assertEqual(report["qgis_parser_unsupported_parser_friendly_filter_count"], 0)
        self.assertEqual(
            report["parser_friendly_supported_filters_by_layer_group_and_operator_signature"],
            [
                {
                    "group": "roads/trails",
                    "operator_signature": "all, get, match",
                    "count": 1,
                    "example_layers": ["road-label"],
                }
            ],
        )
        self.assertEqual(report["qgis_parser_supported_zoom_normalized_part_count"], 0)
        self.assertEqual(report["qgis_parser_unsupported_zoom_normalized_part_count"], 1)
        self.assertEqual(report["parser_friendly_direct_part_count"], 1)
        self.assertEqual(report["parser_friendly_changed_direct_part_count"], 1)
        self.assertEqual(report["qgis_parser_supported_parser_friendly_part_count"], 1)
        self.assertEqual(report["qgis_parser_unsupported_parser_friendly_part_count"], 0)
        self.assertEqual(
            report["zoom_normalized_unsupported_parts_by_layer_group_and_operator_signature"],
            [
                {
                    "group": "roads/trails",
                    "operator_signature": "!, get, match",
                    "count": 1,
                    "example_layers": ["road-label"],
                }
            ],
        )
        self.assertEqual(
            report["parser_friendly_supported_parts_by_layer_group_and_operator_signature"],
            [
                {
                    "group": "roads/trails",
                    "operator_signature": "get, match",
                    "count": 1,
                    "example_layers": ["road-label"],
                }
            ],
        )
        self.assertEqual(len(report["zoom_normalized_unsupported_parts"]), 1)
        row = report["zoom_normalized_unsupported_parts"][0]
        self.assertEqual(row["layer"], "road-label")
        self.assertEqual(row["part_index"], 1)
        self.assertEqual(row["original_operator_signature"], "!, get, match")
        self.assertEqual(row["operator_signature"], "!, get, match")
        self.assertFalse(row["changed_by_zoom_normalization"])
        self.assertEqual(row["unsupported_warning_count"], 1)
        self.assertEqual(row["filter"], ["!", ["match", ["get", "type"], ["steps", "sidewalk"], True, False]])
        self.assertEqual(len(report["parser_friendly_supported_parts"]), 1)
        parser_friendly_row = report["parser_friendly_supported_parts"][0]
        self.assertEqual(parser_friendly_row["zoom_normalized_operator_signature"], "!, get, match")
        self.assertEqual(parser_friendly_row["operator_signature"], "get, match")
        self.assertTrue(parser_friendly_row["changed_by_parser_friendly_normalization"])
        self.assertEqual(
            parser_friendly_row["filter"],
            ["match", ["get", "type"], ["steps", "sidewalk"], False, True],
        )

    def test_filter_parse_support_report_probes_additive_zero_simplification(self):
        style = {
            "version": 8,
            "sources": {"composite": {"type": "vector"}},
            "layers": [
                {
                    "id": "poi-label",
                    "type": "symbol",
                    "source": "composite",
                    "source-layer": "poi_label",
                    "filter": [
                        "<=",
                        ["get", "filterrank"],
                        ["+", 0, ["match", ["get", "class"], "historic", 3, 2]],
                    ],
                }
            ],
        }

        with patch.object(
            mapbox_outdoors_style_audit,
            "_collect_qgis_converter_warnings",
            side_effect=[
                ["poi-label: Skipping unsupported expression"],
                [],
            ],
        ) as collect_warnings:
            report = mapbox_outdoors_style_audit._qgis_filter_parse_support_report(style)

        self.assertEqual(report["parser_friendly_filter_count"], 1)
        self.assertEqual(report["parser_friendly_changed_filter_count"], 1)
        self.assertEqual(report["qgis_parser_supported_parser_friendly_filter_count"], 1)
        self.assertEqual(report["qgis_parser_unsupported_parser_friendly_filter_count"], 0)
        self.assertEqual(report["direct_filter_part_count"], 0)
        self.assertEqual(
            report["parser_friendly_supported_filters"][0]["filter"],
            ["<=", ["get", "filterrank"], ["match", ["get", "class"], "historic", 3, 2]],
        )
        self.assertEqual(
            collect_warnings.call_args_list[1].args[0]["layers"][0]["filter"],
            ["<=", ["get", "filterrank"], ["match", ["get", "class"], "historic", 3, 2]],
        )

    def test_diagnostic_filter_value_at_zoom_evaluates_zoom_driven_steps(self):
        self.assertEqual(mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["zoom"]), 12.0)
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(
                ["step", ["zoom"], False, 10, ["match", ["get", "class"], "track", True, False], 13, True]
            ),
            ["match", ["get", "class"], "track", True, False],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["<", ["-", 16, ["zoom"]], 5]),
            ["<", 4.0, 5],
        )

    def test_markdown_filter_parse_zoom_normalized_unsupported_part_table_lists_messages(self):
        markdown = "\n".join(
            mapbox_outdoors_style_audit._markdown_filter_parse_zoom_normalized_unsupported_part_table(
                [
                    {
                        "layer": "road-label",
                        "part_index": 1,
                        "original_operator_signature": "get, match, step, zoom",
                        "operator_signature": "get, match",
                        "unsupported_warning_count": 2,
                        "unsupported_warning_messages": [
                            {"message": "Skipping unsupported expression", "count": 2}
                        ],
                        "filter": ["match", ["get", "class"], "path", False, False],
                    }
                ]
            )
        )

        self.assertIn(
            "| Layer | Part | Original operators | Normalized operators | Unsupported warnings | Messages | Normalized filter part |",
            markdown,
        )
        self.assertIn("| `road-label` | 1 | `get, match, step, zoom` | `get, match` | 2 |", markdown)
        self.assertIn("<code>Skipping unsupported expression</code>", markdown)
        self.assertIn('<code>["match",["get","class"],"path",false,false]</code>', markdown)

    def test_markdown_filter_parse_parser_friendly_part_table_lists_simplified_parts(self):
        markdown = "\n".join(
            mapbox_outdoors_style_audit._markdown_filter_parse_parser_friendly_part_table(
                [
                    {
                        "layer": "road-path",
                        "part_index": 2,
                        "zoom_normalized_operator_signature": "!, get, match",
                        "operator_signature": "get, match",
                        "filter": ["match", ["get", "type"], ["steps", "sidewalk"], False, True],
                    }
                ]
            )
        )

        self.assertIn(
            "| Layer | Part | Zoom-normalized operators | Parser-friendly operators | Parser-friendly filter part |",
            markdown,
        )
        self.assertIn("| `road-path` | 2 | `!, get, match` | `get, match` |", markdown)
        self.assertIn('<code>["match",["get","type"],["steps","sidewalk"],false,true]</code>', markdown)

    def test_markdown_filter_parse_parser_friendly_filter_table_lists_simplified_filters(self):
        markdown = "\n".join(
            mapbox_outdoors_style_audit._markdown_filter_parse_parser_friendly_filter_table(
                [
                    {
                        "layer": "road-path",
                        "original_operator_signature": "!, all, get, match",
                        "zoom_normalized_operator_signature": "!, all, get, match",
                        "operator_signature": "all, get, match",
                        "filter": ["all", ["match", ["get", "type"], ["steps", "sidewalk"], False, True]],
                    }
                ]
            )
        )

        self.assertIn(
            "| Layer | Original operators | Zoom-normalized operators | Parser-friendly operators | Parser-friendly filter |",
            markdown,
        )
        self.assertIn(
            "| `road-path` | `!, all, get, match` | `!, all, get, match` | `all, get, match` |",
            markdown,
        )
        self.assertIn('<code>["all",["match",["get","type"],["steps","sidewalk"],false,true]]</code>', markdown)

    def test_markdown_filter_parse_parser_friendly_unsupported_part_table_lists_messages(self):
        markdown = "\n".join(
            mapbox_outdoors_style_audit._markdown_filter_parse_parser_friendly_unsupported_part_table(
                [
                    {
                        "layer": "road-path",
                        "part_index": 2,
                        "zoom_normalized_operator_signature": "!, get, match",
                        "operator_signature": "get, match",
                        "unsupported_warning_count": 1,
                        "unsupported_warning_messages": [
                            {"message": "Skipping unsupported expression", "count": 1}
                        ],
                        "filter": ["match", ["get", "type"], ["steps", "sidewalk"], False, True],
                    }
                ]
            )
        )

        self.assertIn(
            "| Layer | Part | Zoom-normalized operators | Parser-friendly operators | Unsupported warnings | Messages | Parser-friendly filter part |",
            markdown,
        )
        self.assertIn("| `road-path` | 2 | `!, get, match` | `get, match` | 1 |", markdown)
        self.assertIn("<code>Skipping unsupported expression</code>", markdown)
        self.assertIn('<code>["match",["get","type"],["steps","sidewalk"],false,true]</code>', markdown)

    def test_markdown_filter_parse_parser_friendly_unsupported_filter_table_lists_messages(self):
        markdown = "\n".join(
            mapbox_outdoors_style_audit._markdown_filter_parse_parser_friendly_unsupported_filter_table(
                [
                    {
                        "layer": "road-path",
                        "original_operator_signature": "!, all, get, match",
                        "zoom_normalized_operator_signature": "!, all, get, match",
                        "operator_signature": "all, get, match",
                        "unsupported_warning_count": 1,
                        "unsupported_warning_messages": [
                            {"message": "Skipping unsupported expression", "count": 1}
                        ],
                        "filter": ["all", ["match", ["get", "type"], ["steps", "sidewalk"], False, True]],
                    }
                ]
            )
        )

        self.assertIn(
            "| Layer | Original operators | Zoom-normalized operators | Parser-friendly operators | Unsupported warnings | Messages | Parser-friendly filter |",
            markdown,
        )
        self.assertIn(
            "| `road-path` | `!, all, get, match` | `!, all, get, match` | `all, get, match` | 1 |",
            markdown,
        )
        self.assertIn("<code>Skipping unsupported expression</code>", markdown)
        self.assertIn('<code>["all",["match",["get","type"],["steps","sidewalk"],false,true]]</code>', markdown)

    def test_diagnostic_filter_parser_friendly_value_handles_remaining_probe_shapes(self):
        self.assertEqual(mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(True), ["==", 1, 1])
        self.assertEqual(mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(False), ["==", 1, 0])
        literal_filter = ["literal", [True, ["match", "label"]]]
        self.assertIs(
            mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(literal_filter),
            literal_filter,
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(
                ["!", ["match", ["get", "type"], ["steps", "sidewalk"], True, False]]
            ),
            ["match", ["get", "type"], ["steps", "sidewalk"], False, True],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(
                ["case", ["has", "layer"], [">=", ["get", "layer"], 0], True]
            ),
            ["any", ["!", ["has", "layer"]], [">=", ["get", "layer"], 0]],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(
                ["<=", ["-", ["to-number", ["get", "sizerank"]], 0], 14]
            ),
            ["<=", ["to-number", ["get", "sizerank"]], 14],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(
                ["<=", ["get", "filterrank"], ["+", 0, ["match", ["get", "class"], "historic", 3, 2]]]
            ),
            ["<=", ["get", "filterrank"], ["match", ["get", "class"], "historic", 3, 2]],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(
                ["<=", ["+", ["get", "filterrank"], 0.0], 14]
            ),
            ["<=", ["get", "filterrank"], 14],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(["-", ["get", "sizerank"], False]),
            ["-", ["get", "sizerank"], False],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(["+", ["get", "sizerank"], False]),
            ["+", ["get", "sizerank"], False],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_parser_friendly_value(
                ["match", ["get", "class"], "path", True, False]
            ),
            ["match", ["get", "class"], "path", True, False],
        )

    def test_diagnostic_filter_value_at_zoom_handles_arithmetic_and_interpolation_edges(self):
        self.assertEqual(mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["+", 1, ["zoom"], 3]), 16.0)
        self.assertEqual(mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["-", ["zoom"]]), -12.0)
        self.assertEqual(mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["*", 2, ["zoom"]]), 24.0)
        self.assertEqual(mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["/", ["zoom"], 3]), 4.0)
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["/", ["zoom"], 0]),
            ["/", 12.0, 0],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["+", 1, "not-numeric"]),
            ["+", 1, "not-numeric"],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["==", ["+", 1, 2], 3]),
            ["==", ["+", 1, 2], 3],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["==", ["+", ["zoom"], 2], 14]),
            ["==", 14.0, 14],
        )
        literal_filter = ["literal", [["zoom"]]]
        self.assertIs(mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(literal_filter), literal_filter)
        self.assertEqual(mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["step"]), ["step"])
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(["interpolate"]),
            ["interpolate"],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(
                ["interpolate", ["linear"], ["get", "rank"], 10, 0, 14, 8]
            ),
            ["interpolate", ["linear"], ["get", "rank"], 10, 0, 14, 8],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(
                ["interpolate", ["linear"], ["zoom"], 14, 8, 16, 12]
            ),
            8,
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(
                ["interpolate", ["linear"], ["zoom"], 10, 0, 14, 8]
            ),
            4.0,
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(
                ["interpolate", ["linear"], ["zoom"], 10, "wide", 14, "wider"]
            ),
            "wide",
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(
                ["interpolate", ["linear"], ["zoom"], "bad-stop", 0, "worse-stop", 1]
            ),
            ["interpolate", ["linear"], 12.0, "bad-stop", 0, "worse-stop", 1],
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(
                ["interpolate", ["linear"], ["zoom"], 8, 1, 10, 2]
            ),
            2,
        )
        self.assertEqual(
            mapbox_outdoors_style_audit._diagnostic_filter_value_at_zoom(
                ["step", ["get", "rank"], False, 10, True]
            ),
            ["step", ["get", "rank"], False, 10, True],
        )

    def test_filter_probe_helpers_cover_nonstandard_inputs(self):
        self.assertFalse(mapbox_outdoors_style_audit._diagnostic_value_depends_on_zoom("not-a-list"))
        self.assertFalse(mapbox_outdoors_style_audit._diagnostic_value_depends_on_zoom(["literal", [["zoom"]]]))
        self.assertTrue(mapbox_outdoors_style_audit._diagnostic_value_depends_on_zoom(["unknown", ["zoom"]]))
        self.assertTrue(mapbox_outdoors_style_audit._diagnostic_value_depends_on_zoom([["zoom"]]))
        self.assertEqual(
            mapbox_outdoors_style_audit._filter_operator_names(["not-a-filter-op", ["==", ["get", "class"], "park"]]),
            ["==", "get"],
        )
        self.assertEqual(list(mapbox_outdoors_style_audit._iter_filter_probe_layers({"layers": "not-a-list"})), [])
        self.assertEqual(
            list(mapbox_outdoors_style_audit._iter_filter_probe_layers({"layers": ["not-a-layer", {"id": "x"}]})),
            [],
        )

    def test_iter_direct_filter_parts_skips_non_boolean_and_non_list_parts(self):
        self.assertEqual(list(mapbox_outdoors_style_audit._iter_direct_filter_parts([])), [])
        self.assertEqual(
            list(mapbox_outdoors_style_audit._iter_direct_filter_parts(["==", ["get", "class"], "park"])),
            [],
        )
        self.assertEqual(
            list(
                mapbox_outdoors_style_audit._iter_direct_filter_parts(
                    ["all", True, ["==", ["get", "class"], "park"], False, ["has", "name"]]
                )
            ),
            [
                (2, "all", ["==", ["get", "class"], "park"]),
                (4, "all", ["has", "name"]),
            ],
        )

    def test_filter_part_probe_style_uses_direct_part_filter(self):
        layer = {
            "id": "poi-label",
            "type": "symbol",
            "source": "composite",
            "source-layer": "poi_label",
            "filter": ["all", ["==", ["get", "class"], "park"], ["has", "name"]],
        }
        filter_part = ["==", ["get", "class"], "park"]

        style = mapbox_outdoors_style_audit._filter_part_probe_style(
            {"version": 8, "sources": {"composite": {"type": "vector"}}}, layer, filter_part
        )

        self.assertEqual(style["layers"][0]["filter"], ["==", ["get", "class"], "park"])
        self.assertNotEqual(style["layers"][0]["filter"], layer["filter"])
        filter_part[1][1] = "mutated"
        self.assertEqual(style["layers"][0]["filter"], ["==", ["get", "class"], "park"])

    def test_qgis_converter_warning_report_can_include_sprite_context_probe(self):
        class FakeQImage:
            Format_ARGB32 = "argb32"

            def __init__(self):
                self.loaded_data = None
                self.converted_format = None

            def loadFromData(self, data):
                self.loaded_data = data
                return True

            def convertToFormat(self, image_format):
                self.converted_format = image_format
                return self

        fake_qt = ModuleType("qgis.PyQt")
        fake_qt_gui = ModuleType("qgis.PyQt.QtGui")
        fake_qt_gui.QImage = FakeQImage
        fake_qgis, fake_core, fake_app, fake_converter = _fake_qgis_modules(
            [
                ["raw warning"],
                ["poi-label: Could not retrieve sprite 'park'", "poi-label: Skipping unsupported expression"],
                ["filterless warning"],
                ["iconless warning"],
                ["line opacity warning"],
                ["line dasharray warning"],
                ["symbol spacing warning"],
                ["poi-label: Skipping unsupported expression"],
            ]
        )
        sprite_resources = MapboxSpriteResources(definitions={"park": {"x": 0}}, image_bytes=b"png-bytes")

        with patch.dict(
            sys.modules,
            {
                "qgis": fake_qgis,
                "qgis.core": fake_core,
                "qgis.PyQt": fake_qt,
                "qgis.PyQt.QtGui": fake_qt_gui,
            },
        ):
            report = mapbox_outdoors_style_audit._qgis_converter_warning_report(
                raw_style={"layers": []},
                qfit_preprocessed_style={"layers": [{"id": "poi-label"}]},
                sprite_resources=sprite_resources,
            )

        probe = report["with_sprite_context_probe"]
        self.assertEqual(probe["sprite_definition_count"], 1)
        self.assertTrue(probe["sprite_image_loaded"])
        self.assertEqual(probe["summary"]["warnings"], ["poi-label: Skipping unsupported expression"])
        self.assertEqual(probe["warning_count_delta_from_qfit"], 1)
        self.assertEqual(
            probe["reduced_from_qfit"]["by_message"],
            [
                {
                    "message": "Could not retrieve sprite 'park'",
                    "raw_count": 1,
                    "qfit_count": 0,
                    "reduced_count": 1,
                }
            ],
        )
        sprite_context = fake_converter.converted_contexts[7]
        self.assertEqual(sprite_context.target_unit, "millimeters")
        self.assertAlmostEqual(sprite_context.pixel_size_conversion_factor, 25.4 / 96.0)
        image, definitions = sprite_context.sprites
        self.assertEqual(image.loaded_data, b"png-bytes")
        self.assertEqual(image.converted_format, "argb32")
        self.assertEqual(definitions, {"park": {"x": 0}})
        self.assertEqual(len(fake_app.created), 1)

    def test_qgis_converter_warning_report_marks_failed_sprite_image_load(self):
        class FakeQImage:
            Format_ARGB32 = "argb32"

            def loadFromData(self, _data):
                return False

            def convertToFormat(self, _image_format):
                raise AssertionError("undecodable sprite images must not be converted")

        fake_qt = ModuleType("qgis.PyQt")
        fake_qt_gui = ModuleType("qgis.PyQt.QtGui")
        fake_qt_gui.QImage = FakeQImage
        fake_qgis, fake_core, _fake_app, fake_converter = _fake_qgis_modules(
            [
                ["raw warning"],
                ["poi-label: Could not retrieve sprite 'park'"],
                ["filterless warning"],
                ["iconless warning"],
                ["line opacity warning"],
                ["line dasharray warning"],
                ["symbol spacing warning"],
                ["poi-label: Could not retrieve sprite 'park'"],
            ]
        )
        sprite_resources = MapboxSpriteResources(definitions={"park": {"x": 0}}, image_bytes=b"not-an-image")

        with patch.dict(
            sys.modules,
            {
                "qgis": fake_qgis,
                "qgis.core": fake_core,
                "qgis.PyQt": fake_qt,
                "qgis.PyQt.QtGui": fake_qt_gui,
            },
        ):
            report = mapbox_outdoors_style_audit._qgis_converter_warning_report(
                raw_style={"layers": []},
                qfit_preprocessed_style={"layers": [{"id": "poi-label"}]},
                sprite_resources=sprite_resources,
            )

        probe = report["with_sprite_context_probe"]
        self.assertEqual(probe["sprite_definition_count"], 1)
        self.assertFalse(probe["sprite_image_loaded"])
        self.assertEqual(probe["warning_count_delta_from_qfit"], 0)
        self.assertIsNone(fake_converter.converted_contexts[7].sprites)

    def test_build_style_audit_summarizes_sprite_context_residual_unresolved_properties(self):
        warning_report = {
            "raw": {"count": 0, "warnings": []},
            "qfit_preprocessed": {"count": 1, "warnings": ["poi-label: Skipping unsupported expression"]},
            "reduced_by_qfit": {},
            "with_sprite_context_probe": {
                "sprite_definition_count": 1,
                "sprite_image_loaded": True,
                "summary": {
                    "count": 2,
                    "warnings": [
                        "poi-label: Skipping unsupported expression",
                        "road-path: Skipping unsupported expression",
                    ],
                },
                "reduced_from_qfit": {},
            },
        }

        with patch.object(
            mapbox_outdoors_style_audit,
            "_qgis_converter_warning_report",
            return_value=warning_report,
        ):
            audit = build_style_audit(
                SAMPLE_STYLE,
                config=StyleAuditConfig(include_qgis_converter_warnings=True),
            )

        sprite_probe = audit["qgis_converter_warnings"]["with_sprite_context_probe"]
        self.assertEqual(
            sprite_probe["remaining_warning_layers_by_unresolved_property"],
            {
                "by_property": [
                    {"property": "filter", "count": 1},
                    {"property": "layout.icon-image", "count": 1},
                ],
                "by_layer_group_and_property": [
                    {"group": "pois/labels", "property": "filter", "count": 1},
                    {"group": "pois/labels", "property": "layout.icon-image", "count": 1},
                ],
            },
        )

    def test_build_style_audit_summarizes_line_dasharray_probe_residual_unresolved_properties(self):
        warning_report = {
            "raw": {"count": 0, "warnings": []},
            "qfit_preprocessed": {
                "count": 2,
                "warnings": [
                    "road-path: Skipping unsupported expression",
                    "poi-label: Skipping unsupported expression",
                ],
            },
            "reduced_by_qfit": {},
            "with_literal_line_dasharray_probe": {
                "line_dasharray_expression_count_replaced": 1,
                "summary": {
                    "count": 1,
                    "warnings": ["poi-label: Skipping unsupported expression"],
                },
                "reduced_from_qfit": {},
            },
        }

        with patch.object(
            mapbox_outdoors_style_audit,
            "_qgis_converter_warning_report",
            return_value=warning_report,
        ):
            audit = build_style_audit(
                SAMPLE_STYLE,
                config=StyleAuditConfig(include_qgis_converter_warnings=True),
            )

        dasharray_probe = audit["qgis_converter_warnings"]["with_literal_line_dasharray_probe"]
        self.assertEqual(
            dasharray_probe["remaining_warning_layers_by_unresolved_property"],
            {
                "by_property": [
                    {"property": "filter", "count": 1},
                    {"property": "layout.icon-image", "count": 1},
                ],
                "by_layer_group_and_property": [
                    {"group": "pois/labels", "property": "filter", "count": 1},
                    {"group": "pois/labels", "property": "layout.icon-image", "count": 1},
                ],
            },
        )

    def test_build_style_audit_summarizes_symbol_spacing_probe_residual_unresolved_properties(self):
        style = {
            "version": 8,
            "sources": {"composite": {"type": "vector", "url": "mapbox://mapbox.mapbox-streets-v8"}},
            "layers": [
                {
                    "id": "road-label",
                    "type": "symbol",
                    "source": "composite",
                    "source-layer": "road",
                    "layout": {"symbol-spacing": ["step", ["zoom"], 100, 12, 200]},
                },
                {
                    "id": "poi-label",
                    "type": "symbol",
                    "source": "composite",
                    "source-layer": "poi_label",
                    "layout": {"icon-image": ["get", "maki"]},
                },
                {
                    "id": "data-symbol-label",
                    "type": "symbol",
                    "source": "composite",
                    "source-layer": "road",
                    "layout": {"symbol-spacing": ["get", "spacing"]},
                },
            ],
        }
        warning_report = {
            "raw": {"count": 0, "warnings": []},
            "qfit_preprocessed": {
                "count": 3,
                "warnings": [
                    "road-label: Skipping unsupported expression",
                    "poi-label: Skipping unsupported expression",
                    "data-symbol-label: Skipping unsupported expression",
                ],
            },
            "reduced_by_qfit": {},
            "with_scalar_symbol_spacing_probe": {
                "symbol_spacing_expression_count_replaced": 1,
                "symbol_spacing_replaced_layers": ["road-label"],
                "summary": {
                    "count": 3,
                    "warnings": [
                        "road-label: Skipping unsupported expression",
                        "poi-label: Skipping unsupported expression",
                        "data-symbol-label: Skipping unsupported expression",
                    ],
                },
                "reduced_from_qfit": {},
            },
        }

        with patch.object(
            mapbox_outdoors_style_audit,
            "_qgis_converter_warning_report",
            return_value=warning_report,
        ):
            audit = build_style_audit(
                style,
                config=StyleAuditConfig(include_qgis_converter_warnings=True),
            )

        symbol_spacing_probe = audit["qgis_converter_warnings"]["with_scalar_symbol_spacing_probe"]
        self.assertEqual(
            symbol_spacing_probe["remaining_warning_layers_by_unresolved_property"],
            {
                "by_property": [
                    {"property": "layout.icon-image", "count": 1},
                    {"property": "layout.symbol-spacing", "count": 1},
                ],
                "by_layer_group_and_property": [
                    {"group": "pois/labels", "property": "layout.icon-image", "count": 1},
                    {"group": "roads/trails", "property": "layout.symbol-spacing", "count": 1},
                ],
            },
        )

    def test_markdown_summarizes_source_filter_preserved_and_unresolved_cues(self):
        audit = build_style_audit(SAMPLE_STYLE)
        markdown = build_audit_markdown(audit)

        self.assertIn("# Mapbox Outdoors style audit", markdown)
        self.assertIn("`road-primary`", markdown)
        self.assertIn("## Summary", markdown)
        self.assertIn("### Simplified/substituted by qfit", markdown)
        self.assertIn("| `paint.line-width` | 1 |", markdown)
        self.assertIn("| `paint.line-dasharray` | 1 |", markdown)
        self.assertIn("### Simplified/substituted by qfit by layer group", markdown)
        self.assertIn("| `roads/trails` | `paint.line-width` | 1 |", markdown)
        self.assertIn("| `roads/trails` | `paint.line-dasharray` | 1 |", markdown)
        self.assertIn("### QGIS-dependent / unresolved", markdown)
        self.assertIn("| `filter` | 1 |", markdown)
        self.assertIn("| `layout.icon-image` | 1 |", markdown)
        self.assertIn("### QGIS-dependent / unresolved by layer group", markdown)
        self.assertIn("| `pois/labels` | `filter` | 1 |", markdown)
        self.assertIn("### Unresolved expression operators", markdown)
        self.assertIn("| `filter` | `==` | 1 |", markdown)
        self.assertIn("### Unresolved expression operators by layer group", markdown)
        self.assertIn("| `pois/labels` | `filter` | `==` | 1 |", markdown)
        self.assertIn("### Unresolved filter expression signatures by layer group", markdown)
        self.assertIn("| `pois/labels` | `==, get` | 1 | `poi-label` |", markdown)
        self.assertIn("## Layers", markdown)
        self.assertIn("composite / road", markdown)
        self.assertIn("`paint.line-width`", markdown)
        self.assertIn("`layout.icon-image`", markdown)
        self.assertIn("Mapbox sprite references", markdown)

    def test_markdown_can_include_qgis_converter_warning_summary(self):
        audit = build_style_audit(SAMPLE_STYLE)
        audit["qgis_converter_warnings"] = {
            "raw": {"count": 3},
            "qfit_preprocessed": {
                "count": 2,
                "by_message": [
                    {"message": "Referenced font DIN Pro Medium is not available on system", "count": 1},
                    {"message": "Skipping unsupported expression", "count": 1},
                ],
                "by_layer_group": [{"group": "pois/labels", "count": 2}],
                "by_layer_group_and_message": [
                    {
                        "group": "pois/labels",
                        "message": "Referenced font DIN Pro Medium is not available on system",
                        "count": 1,
                    },
                    {
                        "group": "pois/labels",
                        "message": "Skipping unsupported expression",
                        "count": 1,
                    }
                ],
                "by_layer": [{"layer": "poi-label", "count": 2}],
                "warnings": [
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Referenced font DIN Pro Medium is not available on system",
                ],
            },
            "warning_count_delta": 1,
            "reduced_by_qfit": {
                "by_message": [
                    {
                        "message": "Could not parse non-string color , skipping",
                        "raw_count": 3,
                        "qfit_count": 0,
                        "reduced_count": 3,
                    }
                ],
                "by_layer": [
                    {"layer": "water-depth", "raw_count": 4, "qfit_count": 0, "reduced_count": 4}
                ],
                "by_layer_group": [
                    {"group": "water", "raw_count": 4, "qfit_count": 0, "reduced_count": 4}
                ],
            },
            "property_removal_impact_probe": {
                "candidate_property_count": 2,
                "by_property": [
                    {
                        "property": "filter",
                        "property_count_removed": 3,
                        "warning_count_after_removal": 1,
                        "warning_count_delta_from_qfit": 1,
                        "skipping_unsupported_expression_delta": 1,
                        "reduced_from_qfit": {
                            "by_layer_group": [
                                {"group": "pois/labels", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}
                            ],
                            "by_layer": [
                                {
                                    "layer": "poi-label",
                                    "raw_count": 2,
                                    "qfit_count": 1,
                                    "reduced_count": 1,
                                    "property_value": ["==", ["get", "class"], "poi"],
                                }
                            ]
                        },
                    },
                    {
                        "property": "layout.text-field",
                        "property_count_removed": 1,
                        "warning_count_after_removal": 5,
                        "warning_count_delta_from_qfit": -3,
                        "skipping_unsupported_expression_delta": -2,
                        "reduced_from_qfit": {
                            "by_layer_group": [
                                {"group": "roads/trails", "raw_count": 1, "qfit_count": 0, "reduced_count": 1}
                            ],
                            "by_layer": [
                                {
                                    "layer": "road-label",
                                    "raw_count": 1,
                                    "qfit_count": 0,
                                    "reduced_count": 1,
                                    "property_value": ["get", "name"],
                                }
                            ]
                        },
                    },
                ],
            },
            "filter_expression_parse_support_probe": {
                "filter_expression_count": 2,
                "qgis_parser_supported_count": 1,
                "qgis_parser_unsupported_count": 1,
                "direct_filter_part_count": 2,
                "qgis_parser_supported_part_count": 1,
                "qgis_parser_unsupported_part_count": 1,
                "zoom_normalized_direct_part_count": 1,
                "zoom_normalized_changed_direct_part_count": 1,
                "qgis_parser_supported_zoom_normalized_part_count": 1,
                "qgis_parser_unsupported_zoom_normalized_part_count": 0,
                "unsupported_by_layer_group": [{"group": "pois/labels", "count": 1}],
                "unsupported_by_warning_message": [{"message": "Skipping unsupported expression", "count": 1}],
                "unsupported_by_layer_group_and_operator_signature": [
                    {
                        "group": "pois/labels",
                        "operator_signature": "==, case, get",
                        "count": 1,
                        "example_layers": ["poi-label"],
                    }
                ],
                "unsupported_parts_by_layer_group_and_operator_signature": [
                    {
                        "group": "pois/labels",
                        "operator_signature": "step, zoom",
                        "count": 1,
                        "example_layers": ["poi-label"],
                    }
                ],
                "unsupported_parts": [
                    {
                        "layer": "poi-label",
                        "group": "pois/labels",
                        "type": "symbol",
                        "source_layer": "poi_label",
                        "parent_operator": "all",
                        "part_index": 2,
                        "operator_signature": "step, zoom",
                        "unsupported_warning_count": 1,
                        "unsupported_warning_messages": [
                            {"message": "Skipping unsupported expression", "count": 1}
                        ],
                        "supported_by_qgis_parser": False,
                        "filter": ["step", ["zoom"], False, 12, True],
                    }
                ],
                "zoom_normalized_supported_parts": [
                    {
                        "layer": "poi-label",
                        "group": "pois/labels",
                        "type": "symbol",
                        "source_layer": "poi_label",
                        "parent_operator": "all",
                        "part_index": 2,
                        "original_operator_signature": "step, zoom",
                        "operator_signature": "(none)",
                        "changed_by_zoom_normalization": True,
                        "unsupported_warning_count": 0,
                        "unsupported_warning_messages": [],
                        "supported_by_qgis_parser": True,
                        "filter": True,
                        "original_filter": ["step", ["zoom"], False, 12, True],
                    }
                ],
                "unsupported_layers": [
                    {
                        "layer": "poi-label",
                        "group": "pois/labels",
                        "type": "symbol",
                        "source_layer": "poi_label",
                        "operator_signature": "==, case, get",
                        "unsupported_warning_count": 1,
                        "unsupported_warning_messages": [
                            {"message": "Skipping unsupported expression", "count": 1}
                        ],
                        "supported_by_qgis_parser": False,
                        "filter": ["case", ["==", ["get", "class"], "park"], True, False],
                    }
                ],
            },
            "without_filters_probe": {
                "filter_count_removed": 1,
                "summary": {
                    "count": 1,
                    "by_message": [
                        {"message": "Referenced font DIN Pro Medium is not available on system", "count": 1}
                    ],
                    "by_layer_group": [{"group": "pois/labels", "count": 1}],
                    "by_layer_group_and_message": [
                        {
                            "group": "pois/labels",
                            "message": "Referenced font DIN Pro Medium is not available on system",
                            "count": 1,
                        }
                    ],
                    "by_layer": [{"layer": "poi-label", "count": 1}],
                },
                "warning_count_delta_from_qfit": 1,
                "reduced_from_qfit": {
                    "by_message": [
                        {
                            "message": "Skipping unsupported expression",
                            "raw_count": 2,
                            "qfit_count": 1,
                            "reduced_count": 1,
                        }
                    ],
                    "by_layer_group": [
                        {"group": "pois/labels", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}
                    ],
                },
                "remaining_warning_layers_by_unresolved_property": {
                    "by_property": [
                        {"property": "layout.icon-image", "count": 1},
                        {"property": "layout.text-font", "count": 1},
                    ],
                    "by_layer_group_and_property": [
                        {"group": "pois/labels", "property": "layout.icon-image", "count": 1},
                        {"group": "pois/labels", "property": "layout.text-font", "count": 1},
                    ],
                },
            },
            "without_icon_images_probe": {
                "icon_image_count_removed": 1,
                "summary": {
                    "count": 1,
                    "by_message": [{"message": "Skipping unsupported expression", "count": 1}],
                    "by_layer_group": [{"group": "pois/labels", "count": 1}],
                    "by_layer_group_and_message": [
                        {
                            "group": "pois/labels",
                            "message": "Skipping unsupported expression",
                            "count": 1,
                        }
                    ],
                    "by_layer": [{"layer": "poi-label", "count": 1}],
                },
                "warning_count_delta_from_qfit": 1,
                "reduced_from_qfit": {
                    "by_message": [
                        {
                            "message": "Referenced font DIN Pro Medium is not available on system",
                            "raw_count": 1,
                            "qfit_count": 0,
                            "reduced_count": 1,
                        }
                    ],
                    "by_layer_group": [
                        {"group": "pois/labels", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}
                    ],
                },
            },
            "with_scalar_line_opacity_probe": {
                "line_opacity_expression_count_replaced": 1,
                "line_opacity_scalarization_rows": [
                    {
                        "group": "roads/trails",
                        "layer": "road-minor",
                        "operator_signature": "step, zoom",
                        "scalar_line_opacity": 1.0,
                        "line_opacity": ["step", ["zoom"], 0, 11, 1],
                    }
                ],
                "summary": {
                    "count": 1,
                    "by_message": [{"message": "Could not retrieve sprite 'park'", "count": 1}],
                    "by_layer_group": [{"group": "pois/labels", "count": 1}],
                    "by_layer_group_and_message": [
                        {"group": "pois/labels", "message": "Could not retrieve sprite 'park'", "count": 1}
                    ],
                    "by_layer": [{"layer": "poi-label", "count": 1}],
                },
                "warning_count_delta_from_qfit": 1,
                "reduced_from_qfit": {
                    "by_message": [
                        {
                            "message": "Skipping unsupported expression",
                            "raw_count": 2,
                            "qfit_count": 1,
                            "reduced_count": 1,
                        }
                    ],
                    "by_layer_group": [
                        {"group": "pois/labels", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}
                    ],
                },
            },
            "with_literal_line_dasharray_probe": {
                "line_dasharray_expression_count_replaced": 1,
                "summary": {
                    "count": 1,
                    "by_message": [{"message": "Could not retrieve sprite 'park'", "count": 1}],
                    "by_layer_group": [{"group": "pois/labels", "count": 1}],
                    "by_layer_group_and_message": [
                        {"group": "pois/labels", "message": "Could not retrieve sprite 'park'", "count": 1}
                    ],
                    "by_layer": [{"layer": "poi-label", "count": 1}],
                },
                "warning_count_delta_from_qfit": 1,
                "reduced_from_qfit": {
                    "by_message": [
                        {
                            "message": "Skipping unsupported expression",
                            "raw_count": 2,
                            "qfit_count": 1,
                            "reduced_count": 1,
                        }
                    ],
                    "by_layer_group": [
                        {"group": "roads/trails", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}
                    ],
                },
                "remaining_warning_layers_by_unresolved_property": {
                    "by_property": [
                        {"property": "layout.icon-image", "count": 1},
                    ],
                    "by_layer_group_and_property": [
                        {"group": "pois/labels", "property": "layout.icon-image", "count": 1},
                    ],
                },
            },
            "with_scalar_symbol_spacing_probe": {
                "symbol_spacing_expression_count_replaced": 2,
                "summary": {
                    "count": 1,
                    "by_message": [{"message": "Could not retrieve sprite 'park'", "count": 1}],
                    "by_layer_group": [{"group": "pois/labels", "count": 1}],
                    "by_layer_group_and_message": [
                        {"group": "pois/labels", "message": "Could not retrieve sprite 'park'", "count": 1}
                    ],
                    "by_layer": [{"layer": "poi-label", "count": 1}],
                },
                "warning_count_delta_from_qfit": 1,
                "reduced_from_qfit": {
                    "by_message": [
                        {
                            "message": "Skipping unsupported expression",
                            "raw_count": 2,
                            "qfit_count": 1,
                            "reduced_count": 1,
                        }
                    ],
                    "by_layer_group": [
                        {"group": "pois/labels", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}
                    ],
                },
                "remaining_warning_layers_by_unresolved_property": {
                    "by_property": [
                        {"property": "layout.icon-image", "count": 1},
                    ],
                    "by_layer_group_and_property": [
                        {"group": "pois/labels", "property": "layout.icon-image", "count": 1},
                    ],
                },
            },
            "with_sprite_context_probe": {
                "sprite_definition_count": 2,
                "sprite_image_loaded": True,
                "summary": {
                    "count": 1,
                    "by_message": [{"message": "Skipping unsupported expression", "count": 1}],
                    "by_layer_group": [{"group": "pois/labels", "count": 1}],
                    "by_layer_group_and_message": [
                        {"group": "pois/labels", "message": "Skipping unsupported expression", "count": 1}
                    ],
                    "by_layer": [{"layer": "poi-label", "count": 1}],
                },
                "warning_count_delta_from_qfit": 1,
                "reduced_from_qfit": {
                    "by_message": [
                        {
                            "message": "Could not retrieve sprite 'park'",
                            "raw_count": 1,
                            "qfit_count": 0,
                            "reduced_count": 1,
                        }
                    ],
                    "by_layer_group": [
                        {"group": "pois/labels", "raw_count": 2, "qfit_count": 1, "reduced_count": 1}
                    ],
                },
                "remaining_warning_layers_by_unresolved_property": {
                    "by_property": [
                        {"property": "filter", "count": 1},
                        {"property": "layout.icon-image", "count": 1},
                    ],
                    "by_layer_group_and_property": [
                        {"group": "pois/labels", "property": "filter", "count": 1},
                        {"group": "pois/labels", "property": "layout.icon-image", "count": 1},
                    ],
                },
            },
        }
        layers = {layer["id"]: layer for layer in audit["layers"]}
        layers["poi-label"]["qgis_converter_warnings"] = {
            "count": 2,
            "by_message": [
                {"message": "Referenced font DIN Pro Medium is not available on system", "count": 1},
                {"message": "Skipping unsupported expression", "count": 1},
            ],
            "warnings": [
                "poi-label: Skipping unsupported expression",
                "poi-label: Referenced font DIN Pro Medium is not available on system",
            ],
        }

        markdown = build_audit_markdown(audit)

        self.assertIn("### QGIS converter warnings", markdown)
        self.assertIn("Raw style warnings: 3", markdown)
        self.assertIn("After qfit preprocessing: 2", markdown)
        self.assertIn("#### Warnings reduced by qfit preprocessing", markdown)
        self.assertIn("| `Could not parse non-string color , skipping` | 3 | 0 | 3 |", markdown)
        self.assertIn("| `water-depth` | 4 | 0 | 4 |", markdown)
        self.assertIn("#### Layer groups with fewer warnings after qfit preprocessing", markdown)
        self.assertIn("| `water` | 4 | 0 | 4 |", markdown)
        self.assertIn("| `Skipping unsupported expression` | 1 |", markdown)
        self.assertIn("#### Remaining warnings by layer group", markdown)
        self.assertIn("| `pois/labels` | 2 |", markdown)
        self.assertIn("#### Remaining warnings by layer group and message", markdown)
        self.assertIn(
            "| `pois/labels` | `Referenced font DIN Pro Medium is not available on system` | 1 |",
            markdown,
        )
        self.assertIn("| `pois/labels` | `Skipping unsupported expression` | 1 |", markdown)
        self.assertIn("| `poi-label` | 2 |", markdown)
        self.assertIn("#### Diagnostic unresolved-property removal impact probe", markdown)
        self.assertIn("Candidate properties tested: 2", markdown)
        self.assertIn(
            "| Property | Removed from layers | Warnings after removal | Warning delta | Skipping-expression delta |",
            markdown,
        )
        self.assertIn("| `filter` | 3 | 1 | 1 | 1 |", markdown)
        self.assertIn("| `layout.text-field` | 1 | 5 | -3 | -2 |", markdown)
        self.assertIn("##### Top warning reductions by property and layer group", markdown)
        self.assertIn("| Property | Layer group | Before removal | After removal | Reduced |", markdown)
        self.assertIn("| `filter` | `pois/labels` | 2 | 1 | 1 |", markdown)
        self.assertIn("##### Top warning reductions by property and layer", markdown)
        self.assertIn("| Property | Layer | Expression | Before removal | After removal | Reduced |", markdown)
        self.assertIn(
            '| `filter` | `poi-label` | <code>["==",["get","class"],"poi"]</code> | 2 | 1 | 1 |',
            markdown,
        )
        self.assertNotIn(
            '| `layout.text-field` | `road-label` | <code>["get","name"]</code> | 1 | 0 | 1 |',
            markdown,
        )
        self.assertIn("#### Diagnostic filter-removal probe", markdown)
        self.assertIn("This is not a rendering-safe qfit preprocessing mode", markdown)
        self.assertIn("Filters removed in probe: 1", markdown)
        self.assertIn("Warnings after removing filters: 1", markdown)
        self.assertIn("Warning count delta from qfit preprocessing: 1", markdown)
        self.assertIn("##### Probe reductions by message", markdown)
        self.assertIn("| Message | Before probe | Without filters | Reduced |", markdown)
        self.assertIn("| `Skipping unsupported expression` | 2 | 1 | 1 |", markdown)
        self.assertIn("##### Probe reductions by layer group", markdown)
        self.assertIn("| Layer group | Before probe | Without filters | Reduced |", markdown)
        self.assertIn("| `pois/labels` | 2 | 1 | 1 |", markdown)
        self.assertIn("##### Remaining probe warnings by message", markdown)
        self.assertIn("| `Referenced font DIN Pro Medium is not available on system` | 1 |", markdown)
        self.assertIn("##### Remaining probe warnings by layer group", markdown)
        self.assertIn("| `pois/labels` | 1 |", markdown)
        self.assertIn("##### Remaining probe warnings by layer group and message", markdown)
        self.assertIn(
            "| `pois/labels` | `Referenced font DIN Pro Medium is not available on system` | 1 |",
            markdown,
        )
        self.assertIn("##### Remaining probe warnings by layer", markdown)
        self.assertIn("| `poi-label` | 1 |", markdown)
        self.assertIn("##### Remaining probe warning layers by unresolved qfit property", markdown)
        self.assertIn("| `layout.icon-image` | 1 |", markdown)
        self.assertIn("##### Remaining probe warning layers by layer group and unresolved qfit property", markdown)
        self.assertIn("| `pois/labels` | `layout.text-font` | 1 |", markdown)
        self.assertIn("#### Diagnostic filter parser support probe", markdown)
        self.assertIn("Filter expressions tested: 2", markdown)
        self.assertIn("Accepted by the QGIS parser probe: 1", markdown)
        self.assertIn("Rejected by the QGIS parser probe: 1", markdown)
        self.assertIn("Direct parts tested from rejected boolean filters: 2", markdown)
        self.assertIn("Rejected direct parts: 1", markdown)
        self.assertIn("Unsupported direct parts re-tested after zoom-normalizing at z12: 1", markdown)
        self.assertIn("Changed by zoom-normalization: 1", markdown)
        self.assertIn("Accepted after zoom-normalization: 1", markdown)
        self.assertIn("Still rejected after zoom-normalization: 0", markdown)
        self.assertIn("Still-rejected parts re-tested with parser-friendly simplifications: 0", markdown)
        self.assertIn("Changed by parser-friendly simplification: 0", markdown)
        self.assertIn("Accepted after parser-friendly simplification: 0", markdown)
        self.assertIn("Still rejected after parser-friendly simplification: 0", markdown)
        self.assertIn("##### Unsupported filter probes by layer group", markdown)
        self.assertIn("| `pois/labels` | 1 |", markdown)
        self.assertIn("##### Unsupported filter parser warnings by message", markdown)
        self.assertIn("| `Skipping unsupported expression` | 1 |", markdown)
        self.assertIn("##### Unsupported filter probes by layer group and operators", markdown)
        self.assertIn("| `pois/labels` | `==, case, get` | 1 | `poi-label` |", markdown)
        self.assertIn("##### Unsupported direct filter parts by layer group and operators", markdown)
        self.assertIn("| `pois/labels` | `step, zoom` | 1 | `poi-label` |", markdown)
        self.assertIn("##### Zoom-normalized direct parts still rejected by layer group and operators", markdown)
        self.assertIn("##### Parser-friendly direct parts accepted by layer group and operators", markdown)
        self.assertIn("##### Parser-friendly direct parts still rejected by layer group and operators", markdown)
        self.assertIn("##### Direct filter parts accepted after zoom-normalization", markdown)
        self.assertIn("This diagnostic evaluates zoom-driven `step`/`interpolate` filter fragments at z12", markdown)
        self.assertIn("| `poi-label` | 2 | `step, zoom` | `(none)` | <code>true</code> |", markdown)
        self.assertIn("##### Direct filter parts still rejected after zoom-normalization", markdown)
        self.assertIn("These rows distinguish remaining parser gaps", markdown)
        self.assertIn("##### Direct filter parts accepted after parser-friendly simplification", markdown)
        self.assertIn("This diagnostic applies parser-friendly", markdown)
        self.assertIn("##### Direct filter parts still rejected after parser-friendly simplification", markdown)
        self.assertIn("##### Unsupported direct filter parts", markdown)
        self.assertIn("| `poi-label` | `all` | 2 | `step, zoom` | 1 |", markdown)
        self.assertIn('<code>["step",["zoom"],false,12,true]</code>', markdown)
        self.assertIn("##### Unsupported filter probe layers", markdown)
        self.assertIn("| `poi-label` | `pois/labels` | `symbol / poi_label` | `==, case, get` | 1 |", markdown)
        self.assertIn('<code>["case",["==",["get","class"],"park"],true,false]</code>', markdown)
        self.assertIn("#### Diagnostic icon-image removal probe", markdown)
        self.assertIn("Icon images removed in probe: 1", markdown)
        self.assertIn("Warnings after removing icon images: 1", markdown)
        self.assertIn("##### Icon probe reductions by message", markdown)
        self.assertIn("| Message | Before icon probe | Without icon-image | Reduced |", markdown)
        self.assertIn("| `Referenced font DIN Pro Medium is not available on system` | 1 | 0 | 1 |", markdown)
        self.assertIn("##### Icon probe reductions by layer group", markdown)
        self.assertIn("| Layer group | Before icon probe | Without icon-image | Reduced |", markdown)
        self.assertIn("##### Remaining icon probe warnings by message", markdown)
        self.assertIn("##### Remaining icon probe warnings by layer", markdown)
        self.assertIn("#### Runtime sprite context probe", markdown)
        self.assertIn("Sprite definitions available in probe: 2", markdown)
        self.assertIn("Sprite image loaded in probe: yes", markdown)
        self.assertIn("Warnings with sprite context: 1", markdown)
        self.assertIn("##### Sprite context reductions by message", markdown)
        self.assertIn("| Message | Before sprite context | With sprite context | Reduced |", markdown)
        self.assertIn("| `Could not retrieve sprite 'park'` | 1 | 0 | 1 |", markdown)
        self.assertIn("##### Sprite context reductions by layer group", markdown)
        self.assertIn("##### Remaining sprite-context warnings by message", markdown)
        self.assertIn("##### Remaining sprite-context warnings by layer", markdown)
        self.assertIn("##### Remaining sprite-context warning layers by unresolved qfit property", markdown)
        self.assertIn("| `filter` | 1 |", markdown)
        self.assertIn(
            "##### Remaining sprite-context warning layers by layer group and unresolved qfit property",
            markdown,
        )
        self.assertIn("| `pois/labels` | `layout.icon-image` | 1 |", markdown)
        self.assertIn("#### Diagnostic line-opacity scalarization probe", markdown)
        self.assertIn("Line opacity expressions replaced in probe: 1", markdown)
        self.assertIn("Warnings after scalar line opacity: 1", markdown)
        self.assertIn("##### Line-opacity probe reductions by message", markdown)
        self.assertIn("| Message | Before line-opacity probe | Scalar line-opacity | Reduced |", markdown)
        self.assertIn("##### Line-opacity probe reductions by layer group", markdown)
        self.assertIn("##### Scalar line-opacity replacements", markdown)
        self.assertIn("| Layer group | Layer | Original operators | Scalar opacity | Original expression |", markdown)
        self.assertIn("| `roads/trails` | `road-minor` | `step, zoom` | 1 |", markdown)
        self.assertIn("##### Remaining line-opacity probe warnings by message", markdown)
        self.assertIn("##### Remaining line-opacity probe warnings by layer", markdown)
        self.assertIn("#### Diagnostic line-dasharray literalization probe", markdown)
        self.assertIn("Line dasharray expressions replaced in probe: 1", markdown)
        self.assertIn("Warnings after literal line dasharray: 1", markdown)
        self.assertIn("##### Line-dasharray probe reductions by message", markdown)
        self.assertIn("| Message | Before line-dasharray probe | Literal line-dasharray | Reduced |", markdown)
        self.assertIn("##### Line-dasharray probe reductions by layer group", markdown)
        self.assertIn("##### Remaining line-dasharray probe warnings by message", markdown)
        self.assertIn("##### Remaining line-dasharray probe warnings by layer", markdown)
        self.assertIn("##### Remaining line-dasharray probe warning layers by unresolved qfit property", markdown)
        self.assertIn("#### Diagnostic symbol-spacing scalarization probe", markdown)
        self.assertIn("Symbol spacing expressions replaced in probe: 2", markdown)
        self.assertIn("Warnings after scalar symbol spacing: 1", markdown)
        self.assertIn("##### Symbol-spacing probe reductions by message", markdown)
        self.assertIn("| Message | Before symbol-spacing probe | Scalar symbol-spacing | Reduced |", markdown)
        self.assertIn("##### Symbol-spacing probe reductions by layer group", markdown)
        self.assertIn("##### Remaining symbol-spacing probe warnings by message", markdown)
        self.assertIn("##### Remaining symbol-spacing probe warnings by layer", markdown)
        self.assertIn("##### Remaining symbol-spacing probe warning layers by unresolved qfit property", markdown)
        self.assertIn("QGIS converter warnings: 2", markdown)
        self.assertIn("`Referenced font DIN Pro Medium is not available on system` (1)", markdown)

    def test_markdown_layer_unresolved_omits_empty_unresolved_sentinel_for_qgis_warnings(self):
        rendered = mapbox_outdoors_style_audit._markdown_layer_unresolved(
            {
                "qfit_unresolved": [],
                "qgis_converter_warnings": {
                    "count": 1,
                    "by_message": [{"message": "Skipping unsupported expression", "count": 1}],
                },
            }
        )

        self.assertEqual(rendered, "QGIS converter warnings: 1<br>`Skipping unsupported expression` (1)")

    def test_markdown_omits_qgis_reduction_sections_when_no_reductions_exist(self):
        audit = build_style_audit(SAMPLE_STYLE)
        audit["qgis_converter_warnings"] = {
            "raw": {"count": 1},
            "qfit_preprocessed": {
                "count": 1,
                "by_message": [{"message": "Skipping unsupported expression", "count": 1}],
                "by_layer": [{"layer": "poi-label", "count": 1}],
            },
            "warning_count_delta": 0,
        }

        markdown = build_audit_markdown(audit)

        self.assertNotIn("#### Warnings reduced by qfit preprocessing", markdown)
        self.assertNotIn("#### Layers with fewer warnings after qfit preprocessing", markdown)
        self.assertIn("#### Remaining warnings by message", markdown)

    def test_render_json_returns_machine_readable_audit(self):
        audit = build_style_audit(SAMPLE_STYLE)
        rendered = render_audit(audit, output_format="json")

        decoded = json.loads(rendered)
        self.assertEqual(decoded["layer_count"], 5)
        self.assertEqual(decoded["layers"][0]["id"], "background")

    def test_load_style_definition_requires_json_object(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "style.json"
            path.write_text(json.dumps(SAMPLE_STYLE), encoding="utf-8")

            self.assertEqual(load_style_definition(path)["version"], 8)

            bad_path = Path(tmp_dir) / "bad-style.json"
            bad_path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_style_definition(bad_path)

    def test_main_can_audit_downloaded_style_json_without_mapbox_credentials(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            style_path = Path(tmp_dir) / "style.json"
            style_path.write_text(json.dumps(SAMPLE_STYLE), encoding="utf-8")

            with patch.object(mapbox_outdoors_style_audit, "DEFAULT_OUTPUT_ROOT", Path(tmp_dir)):
                with patch("builtins.print") as print_mock:
                    result = main(["--style-json", str(style_path)])

            self.assertEqual(result, 0)
            output_path = print_mock.call_args.args[0]
            self.assertTrue(output_path.exists())
            self.assertIn("poi-label", output_path.read_text(encoding="utf-8"))
            self.assertTrue(output_path.is_relative_to(Path(tmp_dir)))
            print_mock.assert_called_once_with(output_path)

    def test_main_property_removal_impact_flag_implies_qgis_warning_audit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            style_path = Path(tmp_dir) / "style.json"
            style_path.write_text(json.dumps(SAMPLE_STYLE), encoding="utf-8")
            audit = {
                "style": {"label": "mapbox/outdoors-v12"},
                "generated_at": "2026-05-12T06:05:00+00:00",
                "layer_count": 0,
                "summary": {},
                "layers": [],
            }

            with patch.object(mapbox_outdoors_style_audit, "DEFAULT_OUTPUT_ROOT", Path(tmp_dir)), patch.object(
                mapbox_outdoors_style_audit,
                "build_style_audit",
                return_value=audit,
            ) as build_audit, patch("builtins.print"):
                result = main([
                    "--style-json",
                    str(style_path),
                    "--include-qgis-property-removal-impact",
                ])

        self.assertEqual(result, 0)
        config = build_audit.call_args.kwargs["config"]
        self.assertTrue(config.include_qgis_converter_warnings)
        self.assertTrue(config.include_qgis_property_removal_impact)

    def test_main_filter_parse_support_flag_implies_qgis_warning_audit(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            style_path = Path(tmp_dir) / "style.json"
            style_path.write_text(json.dumps(SAMPLE_STYLE), encoding="utf-8")
            audit = {
                "style": {"label": "mapbox/outdoors-v12"},
                "generated_at": "2026-05-12T10:05:00+00:00",
                "layer_count": 0,
                "summary": {},
                "layers": [],
            }

            with patch.object(mapbox_outdoors_style_audit, "DEFAULT_OUTPUT_ROOT", Path(tmp_dir)), patch.object(
                mapbox_outdoors_style_audit,
                "build_style_audit",
                return_value=audit,
            ) as build_audit, patch("builtins.print"):
                result = main([
                    "--style-json",
                    str(style_path),
                    "--include-qgis-filter-parse-support",
                ])

        self.assertEqual(result, 0)
        config = build_audit.call_args.kwargs["config"]
        self.assertTrue(config.include_qgis_converter_warnings)
        self.assertTrue(config.include_qgis_filter_parse_support)

    def test_main_fetches_sprite_resources_for_live_qgis_warning_audit(self):
        style_definition = {"version": 8, "sprite": "mapbox://sprites/shared-owner/shared-style", "layers": []}
        sprite_resources = MapboxSpriteResources(definitions={"park": {"x": 0}}, image_bytes=b"png-bytes")
        audit = {
            "style": {"label": "mapbox/outdoors-v12"},
            "generated_at": "2026-05-12T01:45:00+00:00",
            "layer_count": 0,
            "summary": {},
            "layers": [],
        }

        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            mapbox_outdoors_style_audit,
            "DEFAULT_OUTPUT_ROOT",
            Path(tmp_dir),
        ), patch.object(
            mapbox_outdoors_style_audit,
            "fetch_mapbox_style_definition",
            return_value=style_definition,
        ), patch.object(
            mapbox_outdoors_style_audit,
            "fetch_mapbox_sprite_resources",
            return_value=sprite_resources,
        ) as sprite_fetch, patch.object(
            mapbox_outdoors_style_audit,
            "build_style_audit",
            return_value=audit,
        ) as build_audit, patch("builtins.print"):
            result = main(["--mapbox-token", "pk.test", "--include-qgis-converter-warnings", "--format", "json"])

        self.assertEqual(result, 0)
        sprite_fetch.assert_called_once_with(
            "pk.test",
            "mapbox",
            "outdoors-v12",
            sprite_url="mapbox://sprites/shared-owner/shared-style",
        )
        config = build_audit.call_args.kwargs["config"]
        self.assertTrue(config.include_qgis_converter_warnings)
        self.assertIs(config.sprite_resources, sprite_resources)

    def test_parser_exposes_json_and_style_json_options(self):
        args = build_parser().parse_args(
            [
                "--style-json",
                "style.json",
                "--format",
                "json",
                "--include-qgis-converter-warnings",
                "--include-qgis-property-removal-impact",
                "--include-qgis-filter-parse-support",
            ]
        )

        self.assertEqual(args.style_json, Path("style.json"))
        self.assertEqual(args.format, "json")
        self.assertTrue(args.include_qgis_converter_warnings)
        self.assertTrue(args.include_qgis_property_removal_impact)
        self.assertTrue(args.include_qgis_filter_parse_support)


if __name__ == "__main__":
    unittest.main()
