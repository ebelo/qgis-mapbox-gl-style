import datetime as dt
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests import _path  # noqa: F401

from qfit.validation import mapbox_outdoors_rendered_layer_mask as mask_module
from qfit.validation.mapbox_outdoors_rendered_layer_mask import (
    RenderedLayerMaskConfig,
    RenderedLayerMaskVariant,
    apply_transparent_layer_mask,
    build_qgis_render_child_script,
    build_rendered_layer_mask_report,
    image_changed_bbox,
    image_delta_metrics,
    parse_crop_box,
    parse_variant_spec,
    render_markdown_summary,
    render_qgis_vector_in_subprocess,
)
from qfit.validation.mapbox_outdoors_comparison import MapboxComparisonCamera


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
        {"id": "landuse-cemetery", "type": "fill", "paint": {"fill-color": "#c8dca8"}},
        {"id": "road-path", "type": "line", "paint": {"line-color": "#ff9900"}},
        {"id": "poi-label", "type": "symbol", "paint": {"text-color": "#333333"}},
    ],
}


class MapboxOutdoorsRenderedLayerMaskTests(unittest.TestCase):
    def test_parse_variant_spec_requires_named_layer_ids(self):
        variant = parse_variant_spec("cemetery=landuse-cemetery,landuse-z10-cemetery")

        self.assertEqual(variant.name, "cemetery")
        self.assertEqual(variant.layer_ids, ("landuse-cemetery", "landuse-z10-cemetery"))
        with self.assertRaises(Exception):
            parse_variant_spec("cemetery")
        with self.assertRaises(Exception):
            parse_variant_spec("cemetery=")

    def test_parse_crop_box_validates_non_empty_regions(self):
        self.assertEqual(parse_crop_box("1,2,5,8"), (1, 2, 5, 8))

        with self.assertRaises(Exception):
            parse_crop_box("1,2,1,8")
        with self.assertRaises(Exception):
            parse_crop_box("-1,2,5,8")

    def test_apply_transparent_layer_mask_sets_type_specific_opacity(self):
        masked, matched, missing = apply_transparent_layer_mask(
            STYLE,
            layer_ids=("landuse-cemetery", "road-path", "poi-label", "missing"),
        )

        self.assertEqual(matched, ["landuse-cemetery", "road-path", "poi-label"])
        self.assertEqual(missing, ["missing"])
        layers = {layer["id"]: layer for layer in masked["layers"]}
        self.assertEqual(layers["landuse-cemetery"]["paint"]["fill-opacity"], 0.0)
        self.assertEqual(layers["road-path"]["paint"]["line-opacity"], 0.0)
        self.assertEqual(layers["poi-label"]["paint"]["text-opacity"], 0.0)
        self.assertEqual(layers["poi-label"]["paint"]["icon-opacity"], 0.0)
        self.assertNotIn("fill-opacity", STYLE["layers"][1]["paint"])

    def test_image_delta_metrics_reports_signed_and_absolute_crop_movement(self):
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover - local dependency guard
            self.skipTest("Pillow is not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reference_path = root / "reference.png"
            candidate_path = root / "candidate.png"
            Image.new("RGB", (2, 1), (10, 20, 30)).save(reference_path)
            Image.new("RGB", (2, 1), (10, 20, 30)).save(candidate_path)
            with Image.open(candidate_path) as candidate:
                candidate.putpixel((1, 0), (20, 10, 30))
                candidate.save(candidate_path)

            metrics = image_delta_metrics(
                reference_path=reference_path,
                candidate_path=candidate_path,
                crop_box=(1, 0, 2, 1),
            )

            self.assertEqual(metrics["pixel_count"], 1)
            self.assertEqual(metrics["changed_pixel_count"], 1)
            self.assertEqual(metrics["mean_delta_rgb"], [10.0, -10.0, 0.0])
            self.assertAlmostEqual(metrics["mean_absolute_channel_delta"], 20.0 / 3.0)
            self.assertAlmostEqual(metrics["mean_luminance_delta"], -5.026)
            self.assertEqual(
                image_changed_bbox(reference_path=reference_path, candidate_path=candidate_path),
                [1, 0, 2, 1],
            )

    def test_build_report_uses_existing_manifest_and_masks_variants(self):
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
                        "zoom": 14.0,
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
                        "normalized_mean_absolute_channel_delta": 0.0,
                        "normalized_rms_channel_delta": 0.0,
                        "changed_pixel_ratio": 0.0,
                    },
                }),
                encoding="utf-8",
            )

            def fake_renderer(**kwargs):
                output_path = kwargs["output_path"]
                style_definition = kwargs["style_definition"]
                layers = {layer["id"]: layer for layer in style_definition["layers"]}
                if layers["landuse-cemetery"]["paint"].get("fill-opacity") == 0.0:
                    output_path.write_bytes(b"masked-cemetery")
                else:
                    output_path.write_bytes(b"rerender-control")

            def fake_diff_builder(*, reference_path, candidate_path, output_path):
                output_path.write_bytes(b"diff")
                if candidate_path.read_bytes() == b"masked-cemetery":
                    return {
                        "changed_pixel_count": 1,
                        "mean_absolute_channel_delta": 10.0 / 9.0,
                        "normalized_mean_absolute_channel_delta": 0.02,
                        "normalized_rms_channel_delta": 0.03,
                        "rms_channel_delta": 1.0,
                    }
                return {
                    "changed_pixel_count": 0,
                    "mean_absolute_channel_delta": 0.0,
                    "normalized_mean_absolute_channel_delta": 0.0,
                    "normalized_rms_channel_delta": 0.0,
                    "rms_channel_delta": 0.0,
                }

            def fake_image_delta_metrics(*, candidate_path, crop_box, **_kwargs):
                if candidate_path.read_bytes() == b"masked-cemetery":
                    return {
                        "box": list(crop_box),
                        "mean_absolute_channel_delta": 10.0 / 3.0,
                        "mean_luminance_delta": 2.0,
                        "rms_channel_delta": 4.0,
                    }
                return {
                    "box": list(crop_box),
                    "mean_absolute_channel_delta": 0.0,
                    "mean_luminance_delta": 0.0,
                    "rms_channel_delta": 0.0,
                }

            def fake_changed_bbox(*, candidate_path, **_kwargs):
                if candidate_path.read_bytes() == b"masked-cemetery":
                    return [2, 0, 3, 1]
                return None

            with patch(
                "qfit.validation.mapbox_outdoors_rendered_layer_mask.image_delta_metrics",
                fake_image_delta_metrics,
            ), patch(
                "qfit.validation.mapbox_outdoors_rendered_layer_mask.image_changed_bbox",
                fake_changed_bbox,
            ):
                report = build_rendered_layer_mask_report(
                    RenderedLayerMaskConfig(
                        baseline_manifest=manifest_path,
                        output_root=root / "mask-output",
                        variants=(RenderedLayerMaskVariant("cemetery", ("landuse-cemetery",)),),
                        crop_boxes=((2, 0, 3, 1),),
                        token="test-token",
                        now=dt.datetime(2026, 5, 22, 7, 30, tzinfo=dt.timezone.utc),
                    ),
                    qgis_renderer=fake_renderer,
                    diff_builder=fake_diff_builder,
                )

            control = report["variants"][0]
            variant = report["variants"][1]
            self.assertTrue(control["is_rerender_control"])
            self.assertEqual(control["name"], "qgis-rerender-control")
            self.assertTrue(variant["render_changed"])
            self.assertEqual(variant["matched_layer_ids"], ["landuse-cemetery"])
            self.assertEqual(variant["diff_bbox_vs_baseline_qgis"], [2, 0, 3, 1])
            self.assertEqual(variant["diff_bbox_vs_rerender_control_qgis"], [2, 0, 3, 1])
            self.assertEqual(
                variant["qgis_movement_vs_rerender_control_metrics"]["mean_absolute_channel_delta"],
                10.0 / 9.0,
            )
            self.assertEqual(
                variant["crop_delta_vs_baseline"][0]["mean_absolute_channel_delta"],
                10.0 / 3.0,
            )
            self.assertEqual(
                variant["crop_delta_vs_rerender_control"][0]["mean_absolute_channel_delta"],
                10.0 / 3.0,
            )
            summary_path = (
                root
                / "mask-output"
                / "comparison-camera"
                / "20260522T073000Z"
                / "summary.md"
            )
            self.assertTrue(summary_path.exists())
            self.assertIn("rendered-layer mask probe", summary_path.read_text(encoding="utf-8"))

    def test_markdown_summary_lists_render_moving_variants(self):
        markdown = render_markdown_summary({
            "generated": "2026-05-22T07:30:00+00:00",
            "camera": {"name": "unit-camera"},
            "inputs": {"baseline_manifest": "debug/manifest.json"},
            "baseline": {"metrics": {"normalized_mean_absolute_channel_delta": 0.1}},
            "crop_boxes": [[0, 0, 1, 1]],
            "rerender_control_variant": "qgis-rerender-control",
            "variants": [
                {
                    "name": "qgis-rerender-control",
                    "target_layer_ids": [],
                    "render_changed": True,
                    "metrics": {},
                    "crop_delta_vs_rerender_control": [{
                        "mean_absolute_channel_delta": -9.0,
                        "rms_channel_delta": -8.0,
                    }],
                },
                {
                    "name": "moving",
                    "target_layer_ids": ["layer"],
                    "render_changed": True,
                    "metrics": {"normalized_mean_absolute_channel_delta": 0.2},
                    "qgis_movement_vs_rerender_control_metrics": {
                        "mean_absolute_channel_delta": 0.5,
                    },
                    "metric_delta_vs_baseline": {
                        "normalized_mean_absolute_channel_delta": 0.1,
                    },
                    "crop_delta_vs_baseline": [{
                        "mean_absolute_channel_delta": 1.0,
                        "rms_channel_delta": 2.0,
                        "mean_luminance_delta": 3.0,
                    }],
                    "crop_delta_vs_rerender_control": [{
                        "mean_absolute_channel_delta": -1.0,
                        "rms_channel_delta": -2.0,
                    }],
                },
                {
                    "name": "worsening",
                    "target_layer_ids": ["other-layer"],
                    "render_changed": False,
                    "metrics": {},
                    "crop_delta_vs_rerender_control": [{
                        "mean_absolute_channel_delta": 1.5,
                        "rms_channel_delta": 2.5,
                    }],
                },
                {
                    "name": "mixed",
                    "target_layer_ids": ["mixed-layer"],
                    "render_changed": False,
                    "metrics": {},
                    "crop_delta_vs_rerender_control": [{
                        "mean_absolute_channel_delta": -0.5,
                        "rms_channel_delta": 0.25,
                    }],
                },
                {
                    "name": "control-only",
                    "target_layer_ids": ["label-layer"],
                    "render_changed": False,
                    "metrics": {},
                    "crop_delta_vs_baseline": [{
                        "mean_absolute_channel_delta": 0.0,
                        "rms_channel_delta": 0.0,
                    }],
                    "crop_delta_vs_rerender_control": [{
                        "mean_absolute_channel_delta": -0.75,
                        "rms_channel_delta": -0.5,
                    }],
                }
            ],
        })

        self.assertIn("`moving`", markdown)
        self.assertIn("## Crop movement", markdown)
        self.assertIn("| `moving` | 1 | `[0, 0, 1, 1]` | 1.000000000 | 2.000000000 | 3.000000000 |", markdown)
        self.assertIn("Control-adjusted render-moving variants: `moving`.", markdown)
        self.assertIn(
            "Control-adjusted crop-improving variants: `moving` crop 1 "
            "(mean/RMS -1.000000000/-2.000000000), `control-only` crop 1 "
            "(mean/RMS -0.750000000/-0.500000000).",
            markdown,
        )
        self.assertIn(
            "Control-only crop-improving variants: `control-only` crop 1 "
            "(mean/RMS -0.750000000/-0.500000000).",
            markdown,
        )
        self.assertIn(
            "Control-adjusted crop-worsening variants: `worsening` crop 1 "
            "(mean/RMS 1.500000000/2.500000000).",
            markdown,
        )
        self.assertIn(
            "Control-adjusted crop-mixed variants: `mixed` crop 1 "
            "(mean/RMS -0.500000000/0.250000000).",
            markdown,
        )
        self.assertNotIn("qgis-rerender-control` crop", markdown)

    def test_qgis_child_script_keeps_token_out_of_source(self):
        script = build_qgis_render_child_script()

        self.assertIn("render_qgis_vector", script)
        self.assertIn("MAPBOX_ACCESS_TOKEN", script)
        self.assertNotIn("test-token", script)

    def test_subprocess_renderer_passes_token_only_through_environment(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs["env"]
            captured["cwd"] = kwargs["cwd"]
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("qfit.validation.mapbox_outdoors_rendered_layer_mask.subprocess.run", fake_run):
                render_qgis_vector_in_subprocess(
                    camera=MapboxComparisonCamera(
                        name="unit-camera",
                        description="Unit camera",
                        longitude=7.0,
                        latitude=46.0,
                        zoom=14.0,
                        width=3,
                        height=1,
                    ),
                    token="test-token",
                    output_path=Path(tmpdir) / "render.png",
                    style_definition=STYLE,
                )
        self.assertEqual(captured["env"]["MAPBOX_ACCESS_TOKEN"], "test-token")
        self.assertNotIn("test-token", " ".join(str(part) for part in captured["command"]))
        self.assertTrue(str(captured["cwd"]).endswith("qfit"))

    def test_main_builds_config_and_prints_latest_summary(self):
        captured = {}

        def fake_report_builder(config):
            captured["config"] = config
            return {"inputs": {"baseline_manifest": "debug/manifest.json"}}

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            output_root = root / "rendered-layer-mask"
            run_dir = output_root / "comparison-camera" / "20260522T080000Z"
            run_dir.mkdir(parents=True)
            stdout = io.StringIO()
            with patch.object(mask_module, "DEFAULT_OUTPUT_ROOT", output_root):
                with patch.object(mask_module, "resolve_mapbox_token", return_value="resolved-token"):
                    with patch.object(mask_module, "build_rendered_layer_mask_report", fake_report_builder):
                        with redirect_stdout(stdout):
                            result = mask_module.main([
                                "--baseline-manifest",
                                str(root / "manifest.json"),
                                "--variant",
                                "cemetery=landuse-cemetery,landuse-z10-cemetery",
                                "--crop-box",
                                "1,2,5,8",
                                "--no-rerender-control",
                            ])

        config = captured["config"]
        self.assertEqual(result, 0)
        self.assertIsInstance(config, RenderedLayerMaskConfig)
        self.assertEqual(config.baseline_manifest, root / "manifest.json")
        self.assertEqual(config.output_root, output_root)
        self.assertEqual(config.token, "resolved-token")
        self.assertFalse(config.include_rerender_control)
        self.assertEqual(config.crop_boxes, ((1, 2, 5, 8),))
        self.assertEqual(config.variants[0].name, "cemetery")
        self.assertEqual(config.variants[0].layer_ids, ("landuse-cemetery", "landuse-z10-cemetery"))
        self.assertIn("Baseline manifest: debug/manifest.json", stdout.getvalue())
        self.assertIn("Run directory:", stdout.getvalue())
        self.assertIn("Summary:", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
