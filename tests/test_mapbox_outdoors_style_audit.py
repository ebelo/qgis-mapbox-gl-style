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
        created_count = 0

        def __init__(self):
            self.index = FakeQgsMapBoxGlStyleConverter.created_count
            FakeQgsMapBoxGlStyleConverter.created_count += 1

        def convert(self, style_definition):
            FakeQgsMapBoxGlStyleConverter.converted_styles.append(style_definition)

        def warnings(self):
            return warning_sets[self.index]

    qgis_core.QgsApplication = FakeQgsApplication
    qgis_core.QgsMapBoxGlStyleConverter = FakeQgsMapBoxGlStyleConverter
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
        self.assertEqual(simplified_counts["layout.text-field"], 2)
        self.assertEqual(simplified_counts["paint.line-width"], 1)
        self.assertEqual(simplified_counts["layout.visibility"], 1)
        self.assertEqual(unresolved_counts["filter"], 1)
        self.assertEqual(unresolved_counts["layout.icon-image"], 1)
        self.assertEqual(unresolved_counts["paint.line-dasharray"], 1)
        self.assertEqual(unresolved_group_counts[("pois/labels", "filter")], 1)
        self.assertEqual(unresolved_group_counts[("pois/labels", "layout.icon-image")], 1)
        self.assertEqual(unresolved_group_counts[("roads/trails", "paint.line-dasharray")], 1)
        self.assertEqual(operator_counts[("filter", "==")], 1)
        self.assertEqual(operator_counts[("filter", "get")], 1)
        self.assertEqual(operator_counts[("layout.icon-image", "get")], 1)
        self.assertEqual(operator_counts[("paint.line-dasharray", "step")], 1)
        self.assertEqual(operator_group_counts[("pois/labels", "filter", "==")], 1)
        self.assertEqual(operator_group_counts[("pois/labels", "layout.icon-image", "get")], 1)
        self.assertEqual(operator_group_counts[("roads/trails", "paint.line-dasharray", "step")], 1)

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

        path_unresolved = {item["property"] for item in layers["road-path"]["qfit_unresolved"]}
        self.assertIn("paint.line-dasharray", path_unresolved)

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

    def test_build_style_audit_can_include_qgis_converter_warning_summary(self):
        warning_report = {
            "raw": {"count": 3},
            "qfit_preprocessed": {
                "count": 2,
                "by_message": [{"message": "Skipping unsupported expression", "count": 2}],
                "by_layer": [{"layer": "poi-label", "count": 2}],
                "warnings": [
                    "poi-label: Skipping unsupported expression",
                    "poi-label: Referenced font DIN Pro Medium is not available on system",
                ],
            },
            "warning_count_delta": 1,
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

    def test_expression_operator_names_ignore_literal_string_arrays(self):
        self.assertEqual(
            mapbox_outdoors_style_audit._expression_operator_names(
                ["match", ["get", "class"], ["primary", "secondary"], "#fff", "#ccc"]
            ),
            ["get", "match"],
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

    def test_qgis_converter_warning_report_initializes_and_closes_qgis_app(self):
        raw_style = {"layers": []}
        qfit_style = {"layers": [{"id": "poi-label"}]}
        fake_qgis, fake_core, fake_app, fake_converter = _fake_qgis_modules(
            [
                [
                    "road-primary: Skipping unsupported expression",
                    "poi-label: Skipping unsupported expression",
                ],
                ["poi-label: Skipping unsupported expression"],
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

        self.assertEqual(report["raw"]["count"], 2)
        self.assertEqual(report["qfit_preprocessed"]["count"], 1)
        self.assertEqual(report["warning_count_delta"], 1)
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
        self.assertEqual(fake_converter.converted_styles, [raw_style, qfit_style])
        self.assertEqual(len(fake_app.created), 1)
        self.assertEqual(fake_app.created[0].args, [])
        self.assertFalse(fake_app.created[0].gui_enabled)
        self.assertEqual(fake_app.created[0].init_qgis_calls, 1)
        self.assertEqual(fake_app.created[0].exit_qgis_calls, 1)

    def test_qgis_converter_warning_report_reuses_existing_qgis_app(self):
        existing_app = object()
        fake_qgis, fake_core, fake_app, _fake_converter = _fake_qgis_modules(
            [["raw warning"], ["qfit warning"]],
            existing_app=existing_app,
        )

        with patch.dict(sys.modules, {"qgis": fake_qgis, "qgis.core": fake_core}):
            report = mapbox_outdoors_style_audit._qgis_converter_warning_report(
                raw_style={"layers": []},
                qfit_preprocessed_style={"layers": []},
            )

        self.assertEqual(report["raw"]["warnings"], ["raw warning"])
        self.assertEqual(report["qfit_preprocessed"]["warnings"], ["qfit warning"])
        self.assertEqual(fake_app.created, [])

    def test_markdown_summarizes_source_filter_preserved_and_unresolved_cues(self):
        audit = build_style_audit(SAMPLE_STYLE)
        markdown = build_audit_markdown(audit)

        self.assertIn("# Mapbox Outdoors style audit", markdown)
        self.assertIn("`road-primary`", markdown)
        self.assertIn("## Summary", markdown)
        self.assertIn("### Simplified/substituted by qfit", markdown)
        self.assertIn("| `paint.line-width` | 1 |", markdown)
        self.assertIn("### QGIS-dependent / unresolved", markdown)
        self.assertIn("| `filter` | 1 |", markdown)
        self.assertIn("| `layout.icon-image` | 1 |", markdown)
        self.assertIn("### QGIS-dependent / unresolved by layer group", markdown)
        self.assertIn("| `pois/labels` | `filter` | 1 |", markdown)
        self.assertIn("| `roads/trails` | `paint.line-dasharray` | 1 |", markdown)
        self.assertIn("### Unresolved expression operators", markdown)
        self.assertIn("| `filter` | `==` | 1 |", markdown)
        self.assertIn("| `paint.line-dasharray` | `step` | 1 |", markdown)
        self.assertIn("### Unresolved expression operators by layer group", markdown)
        self.assertIn("| `pois/labels` | `filter` | `==` | 1 |", markdown)
        self.assertIn("| `roads/trails` | `paint.line-dasharray` | `step` | 1 |", markdown)
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
                "by_message": [{"message": "Skipping unsupported expression", "count": 2}],
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
        self.assertIn("| `Skipping unsupported expression` | 2 |", markdown)
        self.assertIn("| `poi-label` | 2 |", markdown)
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

    def test_parser_exposes_json_and_style_json_options(self):
        args = build_parser().parse_args(
            ["--style-json", "style.json", "--format", "json", "--include-qgis-converter-warnings"]
        )

        self.assertEqual(args.style_json, Path("style.json"))
        self.assertEqual(args.format, "json")
        self.assertTrue(args.include_qgis_converter_warnings)


if __name__ == "__main__":
    unittest.main()
