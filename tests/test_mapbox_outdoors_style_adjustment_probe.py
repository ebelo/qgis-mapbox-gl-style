import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests import _path  # noqa: F401

from qfit.validation import mapbox_outdoors_style_adjustment_probe as probe_module
from qfit.validation.mapbox_outdoors_style_adjustment_probe import (
    StyleAdjustment,
    StyleAdjustmentProbeConfig,
    apply_style_adjustments,
    build_style_adjustment_aggregate_report,
    build_style_adjustment_probe_report,
    load_style_adjustment_variants,
    render_aggregate_markdown_summary,
    render_markdown_summary,
)


STYLE = {
    "version": 8,
    "sources": {
        "composite": {
            "type": "vector",
            "url": "mapbox://mapbox.mapbox-streets-v8",
        }
    },
    "layers": [
        {"id": "background", "type": "background", "paint": {"background-color": "#ffffff"}},
        {"id": "contour-minor", "type": "line", "paint": {"line-color": "#555555"}},
        {"id": "road-label", "type": "symbol", "layout": {"text-size": 10}},
    ],
}


class MapboxOutdoorsStyleAdjustmentProbeTests(unittest.TestCase):
    def test_load_style_adjustment_variants_reads_structured_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "variants.json"
            path.write_text(
                json.dumps({
                    "variants": [
                        {
                            "name": "contour-strong",
                            "adjustments": [
                                {
                                    "layer_id": "contour-minor",
                                    "paint": {"line-opacity": 0.68},
                                    "layout": {"line-cap": "round"},
                                    "minzoom": 16,
                                }
                            ],
                        }
                    ]
                }),
                encoding="utf-8",
            )

            variants = load_style_adjustment_variants(path)

        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].name, "contour-strong")
        adjustment = variants[0].adjustments[0]
        self.assertEqual(adjustment.layer_id, "contour-minor")
        self.assertEqual(adjustment.paint["line-opacity"], 0.68)
        self.assertEqual(adjustment.layout["line-cap"], "round")
        self.assertEqual(adjustment.minzoom, 16.0)

    def test_load_style_adjustment_variants_rejects_empty_adjustments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "variants.json"
            path.write_text(
                json.dumps({
                    "variants": [
                        {
                            "name": "empty",
                            "adjustments": [{"layer_id": "contour-minor"}],
                        }
                    ]
                }),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_style_adjustment_variants(path)

    def test_apply_style_adjustments_updates_layer_without_mutating_source(self):
        adjusted, matched, missing = apply_style_adjustments(
            STYLE,
            adjustments=(
                StyleAdjustment(
                    layer_id="contour-minor",
                    paint={"line-opacity": 0.68},
                    minzoom=16.0,
                ),
                StyleAdjustment(
                    layer_id="road-label",
                    layout={"text-size": 11},
                    filter=["==", ["get", "class"], "path"],
                ),
                StyleAdjustment(layer_id="missing", paint={"line-opacity": 0.2}),
            ),
        )

        layers = {layer["id"]: layer for layer in adjusted["layers"]}
        self.assertEqual(matched, ["contour-minor", "road-label"])
        self.assertEqual(missing, ["missing"])
        self.assertEqual(layers["contour-minor"]["paint"]["line-opacity"], 0.68)
        self.assertEqual(layers["contour-minor"]["minzoom"], 16.0)
        self.assertEqual(layers["road-label"]["layout"]["text-size"], 11)
        self.assertEqual(layers["road-label"]["filter"], ["==", ["get", "class"], "path"])
        self.assertNotIn("line-opacity", STYLE["layers"][1]["paint"])

    def test_build_report_uses_existing_manifest_and_adjusts_variants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            baseline_dir = root / "baseline"
            baseline_dir.mkdir()
            browser_path = baseline_dir / "mapbox-gl-reference.png"
            qgis_path = baseline_dir / "qgis-vector-render.png"
            style_path = baseline_dir / "qgis-preprocessed-style.json"
            manifest_path = baseline_dir / "manifest.json"
            browser_path.write_bytes(b"browser")
            qgis_path.write_bytes(b"baseline-qgis")
            style_path.write_text(json.dumps(STYLE), encoding="utf-8")
            manifest_path.write_text(
                json.dumps({
                    "camera": {
                        "name": "unit-camera",
                        "description": "Unit camera",
                        "longitude": 7.0,
                        "latitude": 46.0,
                        "zoom": 18.0,
                        "width": 3,
                        "height": 1,
                        "style_owner": "mapbox",
                        "style_id": "outdoors-v12",
                    },
                    "outputs": {
                        "browser_reference": str(browser_path),
                        "qgis_vector_render": str(qgis_path),
                        "qgis_preprocessed_style": str(style_path),
                    },
                    "metrics": {
                        "normalized_mean_absolute_channel_delta": 0.10,
                        "normalized_rms_channel_delta": 0.20,
                        "changed_pixel_ratio": 0.30,
                    },
                }),
                encoding="utf-8",
            )

            def fake_renderer(**kwargs):
                output_path = kwargs["output_path"]
                style_definition = kwargs["style_definition"]
                layers = {layer["id"]: layer for layer in style_definition["layers"]}
                if layers["contour-minor"]["paint"].get("line-opacity") == 0.68:
                    output_path.write_bytes(b"adjusted")
                else:
                    output_path.write_bytes(b"rerender-control")

            def fake_diff_builder(*, candidate_path, output_path, **_kwargs):
                output_path.write_bytes(b"diff")
                if candidate_path.read_bytes() == b"adjusted":
                    return {
                        "changed_pixel_count": 1,
                        "changed_pixel_ratio": 0.4,
                        "normalized_mean_absolute_channel_delta": 0.08,
                        "normalized_rms_channel_delta": 0.18,
                        "mean_absolute_channel_delta": 8.0,
                        "rms_channel_delta": 18.0,
                    }
                return {
                    "changed_pixel_count": 0,
                    "changed_pixel_ratio": 0.3,
                    "normalized_mean_absolute_channel_delta": 0.10,
                    "normalized_rms_channel_delta": 0.20,
                    "mean_absolute_channel_delta": 10.0,
                    "rms_channel_delta": 20.0,
                }

            def fake_image_delta_metrics(*, candidate_path, crop_box, **_kwargs):
                if candidate_path.read_bytes() == b"adjusted":
                    return {
                        "box": list(crop_box),
                        "mean_absolute_channel_delta": 3.0,
                        "mean_luminance_delta": -1.0,
                        "rms_channel_delta": 4.0,
                    }
                return {
                    "box": list(crop_box),
                    "mean_absolute_channel_delta": 5.0,
                    "mean_luminance_delta": 2.0,
                    "rms_channel_delta": 6.0,
                }

            def fake_changed_bbox(*, candidate_path, **_kwargs):
                if candidate_path.read_bytes() == b"adjusted":
                    return [1, 0, 2, 1]
                return None

            with patch(
                "qfit.validation.mapbox_outdoors_style_adjustment_probe.image_delta_metrics",
                fake_image_delta_metrics,
            ), patch(
                "qfit.validation.mapbox_outdoors_style_adjustment_probe.image_changed_bbox",
                fake_changed_bbox,
            ):
                report = build_style_adjustment_probe_report(
                    StyleAdjustmentProbeConfig(
                        baseline_manifest=manifest_path,
                        output_root=root / "style-adjustment-probe",
                        variants=(
                            probe_module.StyleAdjustmentVariant(
                                "contour-strong",
                                (StyleAdjustment("contour-minor", paint={"line-opacity": 0.68}),),
                            ),
                        ),
                        crop_boxes=((1, 0, 2, 1),),
                        token="test-token",
                        now=dt.datetime(2026, 5, 24, 14, 30, tzinfo=dt.timezone.utc),
                    ),
                    qgis_renderer=fake_renderer,
                    diff_builder=fake_diff_builder,
                )

            control = report["variants"][0]
            variant = report["variants"][1]
            self.assertEqual(report["generated"], "2026-05-24T14:30:00+00:00")
            self.assertTrue(control["is_rerender_control"])
            self.assertFalse(variant["is_rerender_control"])
            self.assertEqual(variant["matched_layer_ids"], ["contour-minor"])
            self.assertEqual(variant["diff_bbox_vs_baseline_qgis"], [1, 0, 2, 1])
            self.assertAlmostEqual(
                variant["metric_delta_vs_baseline"]["normalized_mean_absolute_channel_delta"],
                -0.02,
            )
            self.assertAlmostEqual(
                variant["metric_delta_vs_rerender_control"]["normalized_rms_channel_delta"],
                -0.02,
            )
            self.assertEqual(
                variant["crop_delta_vs_rerender_control"][0]["mean_absolute_channel_delta"],
                -2.0,
            )
            summary_path = (
                root
                / "style-adjustment-probe"
                / "comparison-camera"
                / "20260524T143000Z"
                / "summary.md"
            )
            self.assertTrue(summary_path.exists())
            self.assertIn("style-adjustment probe", summary_path.read_text(encoding="utf-8"))

    def test_markdown_summary_lists_improving_variants(self):
        markdown = render_markdown_summary({
            "generated": "2026-05-24T14:30:00+00:00",
            "camera": {"name": "unit-camera"},
            "inputs": {"baseline_manifest": "debug/manifest.json"},
            "baseline": {"metrics": {"normalized_mean_absolute_channel_delta": 0.1}},
            "crop_boxes": [[0, 0, 1, 1], [1, 1, 2, 2]],
            "rerender_control_variant": "qgis-rerender-control",
            "variants": [
                {
                    "name": "contour-strong",
                    "metrics": {"normalized_mean_absolute_channel_delta": 0.08},
                    "metric_delta_vs_baseline": {
                        "normalized_mean_absolute_channel_delta": -0.02,
                        "normalized_rms_channel_delta": -0.01,
                    },
                    "metric_delta_vs_rerender_control": {
                        "normalized_mean_absolute_channel_delta": -0.03,
                        "normalized_rms_channel_delta": -0.02,
                    },
                    "crop_delta_vs_baseline": [
                        {
                            "mean_absolute_channel_delta": -1.0,
                            "rms_channel_delta": -2.0,
                            "mean_luminance_delta": -3.0,
                        },
                        {
                            "mean_absolute_channel_delta": 4.0,
                            "rms_channel_delta": 5.0,
                            "mean_luminance_delta": 6.0,
                        },
                    ],
                    "crop_delta_vs_rerender_control": [
                        {
                            "mean_absolute_channel_delta": -1.5,
                            "rms_channel_delta": -2.5,
                        },
                        {
                            "mean_absolute_channel_delta": 4.5,
                            "rms_channel_delta": 5.5,
                        },
                    ],
                }
            ],
        })

        self.assertIn("## Crop movement", markdown)
        self.assertIn(
            "| `contour-strong` | 2 | `[1, 1, 2, 2]` | 4.000000000 | 5.000000000 | "
            "6.000000000 | 4.500000000 | 5.500000000 |",
            markdown,
        )
        self.assertIn("Whole-image mean/RMS improving variants: `contour-strong`.", markdown)
        self.assertIn("Control-adjusted whole-image mean/RMS improving variants: `contour-strong`.", markdown)

    def test_aggregate_report_groups_repeated_variant_camera_deltas(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first_report = root / "first-style-adjustment-probe.json"
            second_report = root / "second-style-adjustment-probe.json"
            third_report = root / "third-style-adjustment-probe.json"
            first_report.write_text(
                json.dumps({
                    "camera": {"name": "valais-geneva-outdoors"},
                    "rerender_control_variant": "qgis-rerender-control",
                    "crop_boxes": [[0, 0, 1, 1], [1, 1, 2, 2]],
                    "variants": [
                        {"name": "qgis-rerender-control", "is_rerender_control": True},
                        {
                            "name": "landcover-opacity-70",
                            "is_rerender_control": False,
                            "metric_delta_vs_rerender_control": {
                                "normalized_mean_absolute_channel_delta": -0.0001,
                                "normalized_rms_channel_delta": -0.0002,
                            },
                            "crop_delta_vs_rerender_control": [
                                {
                                    "mean_absolute_channel_delta": -1.0,
                                    "rms_channel_delta": -2.0,
                                    "mean_luminance_delta": -3.0,
                                },
                                {
                                    "mean_absolute_channel_delta": 1.0,
                                    "rms_channel_delta": 2.0,
                                    "mean_luminance_delta": 3.0,
                                },
                            ],
                        },
                    ],
                }),
                encoding="utf-8",
            )
            second_report.write_text(
                json.dumps({
                    "camera": {"name": "valais-geneva-outdoors"},
                    "rerender_control_variant": "qgis-rerender-control",
                    "crop_boxes": [[0, 0, 1, 1], [1, 1, 2, 2]],
                    "variants": [
                        {
                            "name": "landcover-opacity-70",
                            "is_rerender_control": False,
                            "metric_delta_vs_rerender_control": {
                                "normalized_mean_absolute_channel_delta": 0.00005,
                                "normalized_rms_channel_delta": 0.0001,
                            },
                            "crop_delta_vs_rerender_control": [
                                {
                                    "mean_absolute_channel_delta": 0.5,
                                    "rms_channel_delta": 0.5,
                                    "mean_luminance_delta": 1.0,
                                }
                            ],
                        },
                    ],
                }),
                encoding="utf-8",
            )
            third_report.write_text(
                json.dumps({
                    "camera": {"name": "geneva-airport-motorway-z14-outdoors"},
                    "variants": [
                        {
                            "name": "landcover-opacity-70",
                            "metric_delta_vs_rerender_control": {
                                "normalized_mean_absolute_channel_delta": -0.0002,
                            },
                            "metric_delta_vs_baseline": {
                                "normalized_mean_absolute_channel_delta": -0.0003,
                                "normalized_rms_channel_delta": -0.0004,
                            },
                        },
                    ],
                }),
                encoding="utf-8",
            )

            aggregate = build_style_adjustment_aggregate_report(
                (first_report, second_report, third_report),
                now=dt.datetime(2026, 5, 24, 15, 0, tzinfo=dt.timezone.utc),
            )

        rows = {
            (row["variant"], row["camera"], row["delta_source"]): row
            for row in aggregate["rows"]
        }
        valais_row = rows[("landcover-opacity-70", "valais-geneva-outdoors", "rerender_control")]
        self.assertEqual(aggregate["generated"], "2026-05-24T15:00:00+00:00")
        self.assertEqual(valais_row["runs"], 2)
        self.assertEqual(valais_row["improving_runs"], 1)
        self.assertEqual(valais_row["worsening_runs"], 1)
        self.assertAlmostEqual(valais_row["mean_delta_average"], -0.000025)
        self.assertAlmostEqual(valais_row["rms_delta_range"], 0.0003)
        self.assertEqual(
            rows[("landcover-opacity-70", "geneva-airport-motorway-z14-outdoors", "baseline")][
                "improving_runs"
            ],
            1,
        )
        total_rows = {
            (row["variant"], row["delta_source"]): row
            for row in aggregate["variant_totals"]
        }
        valais_total = total_rows[("landcover-opacity-70", "rerender_control")]
        self.assertEqual(valais_total["runs"], 2)
        self.assertEqual(valais_total["camera_count"], 1)
        self.assertAlmostEqual(valais_total["mean_delta_range"], 0.00015)
        crop_rows = {
            (row["variant"], row["camera"], row["delta_source"], row["crop"]): row
            for row in aggregate["crop_rows"]
        }
        first_crop = crop_rows[("landcover-opacity-70", "valais-geneva-outdoors", "rerender_control", 1)]
        self.assertEqual(first_crop["runs"], 2)
        self.assertEqual(first_crop["crop_box"], "[0, 0, 1, 1]")
        self.assertAlmostEqual(first_crop["mean_delta_average"], -0.25)
        self.assertAlmostEqual(first_crop["luminance_delta_average"], -1.0)
        self.assertEqual(first_crop["improving_runs"], 1)
        self.assertEqual(first_crop["worsening_runs"], 1)
        second_crop = crop_rows[("landcover-opacity-70", "valais-geneva-outdoors", "rerender_control", 2)]
        self.assertEqual(second_crop["runs"], 1)
        self.assertEqual(second_crop["crop_box"], "[1, 1, 2, 2]")
        self.assertAlmostEqual(second_crop["mean_delta_average"], 1.0)
        self.assertAlmostEqual(second_crop["rms_delta_average"], 2.0)
        self.assertAlmostEqual(second_crop["luminance_delta_average"], 3.0)
        self.assertEqual(second_crop["improving_runs"], 0)
        self.assertEqual(second_crop["worsening_runs"], 1)

    def test_aggregate_report_ignores_boolean_metric_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report_path = root / "style-adjustment-probe.json"
            report_path.write_text(
                json.dumps({
                    "camera": {"name": "unit-camera"},
                    "variants": [
                        {
                            "name": "boolean-noise",
                            "metric_delta_vs_baseline": {
                                "normalized_mean_absolute_channel_delta": False,
                                "normalized_rms_channel_delta": True,
                            },
                        }
                    ],
                }),
                encoding="utf-8",
            )

            aggregate = build_style_adjustment_aggregate_report((report_path,))

        self.assertEqual(aggregate["rows"], [])
        self.assertEqual(aggregate["variant_totals"], [])
        self.assertEqual(aggregate["crop_rows"], [])

    def test_aggregate_markdown_summary_surfaces_mixed_signal(self):
        markdown = render_aggregate_markdown_summary({
            "generated": "2026-05-24T15:00:00+00:00",
            "input_reports": ["debug/first.json", "debug/second.json"],
            "variant_totals": [
                {
                    "variant": "landcover-opacity-70",
                    "delta_source": "rerender_control",
                    "runs": 2,
                    "camera_count": 1,
                    "mean_delta_average": -0.000025,
                    "rms_delta_average": -0.00005,
                    "mean_delta_range": 0.00015,
                    "rms_delta_range": 0.0003,
                    "improving_runs": 1,
                    "worsening_runs": 1,
                    "other_runs": 0,
                }
            ],
            "rows": [
                {
                    "variant": "landcover-opacity-70",
                    "camera": "valais-geneva-outdoors",
                    "delta_source": "rerender_control",
                    "runs": 2,
                    "mean_delta_average": -0.000025,
                    "rms_delta_average": -0.00005,
                    "mean_delta_range": 0.00015,
                    "rms_delta_range": 0.0003,
                    "improving_runs": 1,
                    "worsening_runs": 1,
                    "other_runs": 0,
                }
            ],
            "crop_rows": [
                {
                    "variant": "landcover-opacity-70",
                    "camera": "valais-geneva-outdoors",
                    "delta_source": "rerender_control",
                    "crop": 2,
                    "crop_box": "[1, 1, 2, 2]",
                    "runs": 1,
                    "mean_delta_average": 1.0,
                    "rms_delta_average": 2.0,
                    "luminance_delta_average": 3.0,
                    "mean_delta_range": 0.0,
                    "rms_delta_range": 0.0,
                    "improving_runs": 0,
                    "worsening_runs": 1,
                    "other_runs": 0,
                }
            ],
        })

        self.assertIn("style-adjustment aggregate", markdown)
        self.assertIn("`landcover-opacity-70`", markdown)
        self.assertIn("| `landcover-opacity-70` | `valais-geneva-outdoors` |", markdown)
        self.assertIn("## Aggregated crop movement", markdown)
        self.assertIn(
            "| `landcover-opacity-70` | `valais-geneva-outdoors` | `rerender_control` | "
            "2 | `[1, 1, 2, 2]` | 1 | 1.000000000 | 2.000000000 | 3.000000000 |",
            markdown,
        )
        self.assertIn("0.000300000", markdown)
        self.assertIn("## Key", markdown)

    def test_main_builds_config_and_prints_latest_summary(self):
        captured = {}

        def fake_report_builder(config):
            captured["config"] = config
            return {"inputs": {"baseline_manifest": "debug/manifest.json"}}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            variant_json = root / "variants.json"
            variant_json.write_text(
                json.dumps({
                    "variants": [
                        {
                            "name": "contour-strong",
                            "adjustments": [
                                {"layer_id": "contour-minor", "paint": {"line-opacity": 0.68}}
                            ],
                        }
                    ]
                }),
                encoding="utf-8",
            )
            output_root = root / "style-adjustment-probe"
            run_dir = output_root / "comparison-camera" / "20260524T143000Z"
            run_dir.mkdir(parents=True)
            stdout = io.StringIO()
            with patch.object(probe_module, "DEFAULT_OUTPUT_ROOT", output_root):
                with patch.object(probe_module, "resolve_mapbox_token", return_value="resolved-token"):
                    with patch.object(probe_module, "build_style_adjustment_probe_report", fake_report_builder):
                        with redirect_stdout(stdout):
                            result = probe_module.main([
                                "--baseline-manifest",
                                str(root / "manifest.json"),
                                "--variant-json",
                                str(variant_json),
                                "--crop-box",
                                "1,2,5,8",
                                "--no-rerender-control",
                            ])

        config = captured["config"]
        self.assertIsNone(result)
        self.assertIsInstance(config, StyleAdjustmentProbeConfig)
        self.assertEqual(config.baseline_manifest, root / "manifest.json")
        self.assertEqual(config.output_root, output_root)
        self.assertEqual(config.token, "resolved-token")
        self.assertFalse(config.include_rerender_control)
        self.assertEqual(config.crop_boxes, ((1, 2, 5, 8),))
        self.assertEqual(config.variants[0].name, "contour-strong")
        self.assertEqual(config.variants[0].adjustments[0].layer_id, "contour-minor")
        self.assertIn("Baseline manifest: debug/manifest.json", stdout.getvalue())
        self.assertIn("Run directory:", stdout.getvalue())
        self.assertIn("Summary:", stdout.getvalue())

    def test_main_aggregate_mode_writes_markdown_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            report_path = root / "style-adjustment-probe.json"
            output_path = root / "aggregate.md"
            report_path.write_text(
                json.dumps({
                    "camera": {"name": "valais-geneva-outdoors"},
                    "rerender_control_variant": "qgis-rerender-control",
                    "variants": [
                        {
                            "name": "landcover-opacity-70",
                            "metric_delta_vs_rerender_control": {
                                "normalized_mean_absolute_channel_delta": -0.0001,
                                "normalized_rms_channel_delta": -0.0002,
                            },
                        }
                    ],
                }),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = probe_module.main([
                    "--aggregate-report",
                    str(report_path),
                    "--aggregate-output",
                    str(output_path),
                ])
            output_markdown = output_path.read_text(encoding="utf-8")

        self.assertIsNone(result)
        self.assertIn("Aggregate summary:", stdout.getvalue())
        self.assertIn("landcover-opacity-70", output_markdown)


if __name__ == "__main__":
    unittest.main()
