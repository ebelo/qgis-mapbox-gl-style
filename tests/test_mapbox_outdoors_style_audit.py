import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
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

        unresolved = {item["property"]: item["reason"] for item in layers["poi-label"]["qfit_unresolved"]}
        self.assertIn("layout.icon-image", unresolved)
        self.assertNotIn("layout.text-field", unresolved)
        self.assertIn("sprites", unresolved["layout.icon-image"])

        hidden_layer = layers["settlement-subdivision-label"]
        hidden_changes = {change["property"]: change for change in hidden_layer["qfit_simplifies"]}
        self.assertEqual(hidden_changes["layout.visibility"]["from"], "absent")
        self.assertEqual(hidden_changes["layout.visibility"]["to"], '"none"')
        self.assertEqual(hidden_layer["qfit_unresolved"], [])

    def test_markdown_summarizes_source_filter_preserved_and_unresolved_cues(self):
        audit = build_style_audit(SAMPLE_STYLE)
        markdown = build_audit_markdown(audit)

        self.assertIn("# Mapbox Outdoors style audit", markdown)
        self.assertIn("`road-primary`", markdown)
        self.assertIn("composite / road", markdown)
        self.assertIn("`paint.line-width`", markdown)
        self.assertIn("`layout.icon-image`", markdown)
        self.assertIn("Mapbox sprite references", markdown)

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
        args = build_parser().parse_args(["--style-json", "style.json", "--format", "json"])

        self.assertEqual(args.style_json, Path("style.json"))
        self.assertEqual(args.format, "json")


if __name__ == "__main__":
    unittest.main()
