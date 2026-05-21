import argparse
import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import _path  # noqa: F401

from qfit.validation.mapbox_outdoors_visual_crops import (
    VisualCropAnnotationInputs,
    _computed_crop_color_movement_group_records,
    _crop_color_metric,
    _three_channel_color_values,
    annotate_visual_crop_report_with_comparison_delta,
    build_run_directory,
    build_summary_markdown,
    build_visual_crop_paths,
    find_hotspot_crop_boxes,
    generate_visual_crop_report,
    main,
    parse_crop_size,
    write_report,
)


def _require_pillow():
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - local dependency guard
        raise unittest.SkipTest("Pillow is not available")
    return Image


class _FakeImage:
    def __init__(self, module, size, color=(0, 0, 0), pixels=None):
        self._module = module
        self.width, self.height = size
        self.size = size
        self._base = int(sum(color) / max(1, len(color)))
        self._pixels = dict(pixels or {})

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        return False

    def close(self):
        pass

    def convert(self, _mode):
        return _FakeImage(self._module, self.size, (self._base, self._base, self._base), self._pixels)

    def crop(self, box):
        left, top, right, bottom = box
        pixels = {
            (x - left, y - top): value
            for (x, y), value in self._pixels.items()
            if left <= x < right and top <= y < bottom
        }
        return _FakeImage(
            self._module,
            (right - left, bottom - top),
            (self._base, self._base, self._base),
            pixels,
        )

    def putpixel(self, point, color):
        self._pixels[point] = int(sum(color) / max(1, len(color)))

    def save(self, path, *args, **_kwargs):
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake image")
        self._module.images[output_path] = self.convert("RGB")

    def brightness_sum(self):
        pixel_count = self.width * self.height
        override_delta = sum(value - self._base for value in self._pixels.values())
        return self._base * pixel_count + override_delta


class _FakeImageModule:
    def __init__(self):
        self.images = {}

    def new(self, _mode, size, color):
        return _FakeImage(self, size, color)

    def open(self, path):
        return self.images[Path(path)].convert("RGB")


class _FakeImageStatModule:
    class Stat:
        def __init__(self, image):
            self.sum = [image.brightness_sum()]
            pixel_count = max(1, image.width * image.height)
            self.mean = [self.sum[0] / pixel_count]


class _EmptyColorStatModule:
    class Stat:
        def __init__(self, _image):
            self.mean = []


class _TwoChannelColorSumStatModule:
    class Stat:
        def __init__(self, image):
            pixel_count = max(1, image.width * image.height)
            self.mean = []
            self.sum = [10.0 * pixel_count, 20.0 * pixel_count]


def _fake_image_modules():
    return _FakeImageModule(), _FakeImageStatModule


def _fake_contact_sheet(*, entries, output_path, **_kwargs):
    if not entries:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("fake contact sheet", encoding="utf-8")
    return output_path


def _box_contains(box, point):
    return box[0] <= point[0] < box[2] and box[1] <= point[1] < box[3]


def _save_rgb_image(path, image_module, color, *, size=(12, 12)):
    image = image_module.new("RGB", size, color)
    try:
        image.save(path)
    finally:
        image.close()


def _write_visual_triplet(root, image_module, *, camera_name="chamonix-trails-z14-outdoors"):
    comparison_root = root / "comparison"
    summary_path = comparison_root / "all-cameras" / "run" / "summary.json"
    camera_dir = comparison_root / camera_name / "run"
    browser_path = camera_dir / "mapbox-gl-reference.png"
    qgis_path = camera_dir / "qgis-vector-render.png"
    diff_path = camera_dir / "mapbox-gl-vs-qgis-diff.png"
    camera_dir.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    _save_rgb_image(browser_path, image_module, (255, 255, 255))
    _save_rgb_image(qgis_path, image_module, (128, 128, 128))
    diff = image_module.new("RGB", (12, 12), (0, 0, 0))
    diff.putpixel((9, 9), (255, 255, 255))
    diff.save(diff_path)
    diff.close()
    comparison_summary = {
        "generated_at": "2026-05-20T20:00:00+00:00",
        "style_url": "mapbox://styles/mapbox/outdoors-v12",
        "cameras": [
            {
                "camera": camera_name,
                "status": "passed",
                "artifact_status": "metrics_available",
                "metrics": {
                    "changed_pixel_ratio": 0.9820199652777778,
                    "normalized_mean_absolute_channel_delta": 0.03523346722948439,
                    "normalized_rms_channel_delta": 0.07608105570020197,
                    "ssim_status": "unavailable",
                },
                "outputs": {
                    "browser_reference": str(browser_path),
                    "qgis_vector_render": str(qgis_path),
                    "diff": str(diff_path),
                },
            }
        ],
    }
    return comparison_summary, summary_path


def _path_pedestrian_focus_report(
    camera_name="chamonix-trails-z14-outdoors",
    *,
    comparison_summary_jsons=None,
):
    if comparison_summary_jsons is None:
        comparison_summary_jsons = ["debug/comparison/summary.json"]
    return {
        "input_artifacts": {
            "comparison_summary_jsons": comparison_summary_jsons,
        },
        "cameras": [
            {
                "camera": camera_name,
                "source_qgis_stroke_control_comparisons": [
                    {
                        "source_layer_id": "road-path-trail",
                        "decoded_candidate_count": 74,
                        "decoded_candidate_type_counts": {"trail": 69, "hiking": 5},
                        "source_sampled_controls": {"line-width": 0.26458333333333334},
                        "qgis_controls": [
                            {
                                "layer_id": "road-path-trail-below-z16",
                                "controls": {"line-width": 0.42333333333333334},
                            }
                        ],
                        "qgis_control_deltas": [
                            {
                                "layer_id": "road-path-trail-below-z16",
                                "deltas": {
                                    "line-width_delta_mm": 0.15875,
                                    "line-width_ratio": 1.6,
                                    "line-dasharray_match": True,
                                },
                            }
                        ],
                    },
                    {
                        "source_layer_id": "bridge-path",
                        "decoded_candidate_count": 0,
                        "decoded_candidate_type_counts": {},
                        "source_sampled_controls": {
                            "line-width": 1.0583333333333333,
                            "line-dasharray": [1, 0.25],
                        },
                        "qgis_controls": [
                            {
                                "layer_id": "bridge-path",
                                "controls": {
                                    "line-width": 0.26458333333333334,
                                    "line-dasharray": [4, 0.3],
                                },
                            }
                        ],
                        "qgis_control_deltas": [
                            {
                                "layer_id": "bridge-path",
                                "deltas": {
                                    "line-width_delta_mm": -0.79375,
                                    "line-width_ratio": 0.25,
                                    "line-dasharray_match": False,
                                },
                            }
                        ],
                    },
                    {
                        "source_layer_id": "road-steps",
                        "decoded_candidate_count": 3,
                        "decoded_candidate_type_counts": {"steps": 3},
                        "source_sampled_controls": {
                            "line-width": 0.5291666666666667,
                            "line-dasharray": [0.3, 0.3],
                        },
                        "qgis_controls": [
                            {
                                "layer_id": "road-steps",
                                "controls": {
                                    "line-width": 0.5291666666666667,
                                    "line-dasharray": [1, 0],
                                },
                            }
                        ],
                        "qgis_control_deltas": [
                            {
                                "layer_id": "road-steps",
                                "deltas": {
                                    "line-width_delta_mm": 0.0,
                                    "line-width_ratio": 1.0,
                                    "line-dasharray_match": False,
                                },
                            }
                        ],
                    },
                ],
            }
        ]
    }


def _comparison_delta_report(candidate_summary_json, *, camera_name="chamonix-trails-z14-outdoors"):
    return {
        "input_artifacts": {
            "candidate": {
                "summary_json": str(candidate_summary_json),
            }
        },
        "cameras": [
            {
                "camera": camera_name,
                "baseline_status": "passed",
                "candidate_status": "passed",
                "mean_delta_direction": "improved",
                "rms_delta_direction": "worsened",
                "metrics": {
                    "changed_pixel_ratio": {
                        "delta": -0.0000008680555555,
                    },
                    "normalized_mean_absolute_channel_delta": {
                        "delta": -0.00015750385802469,
                    },
                    "normalized_rms_channel_delta": {
                        "delta": 0.00047919108424084,
                    },
                },
            }
        ],
    }


def _style_audit_report():
    return {
        "layers": [
            {
                "id": "landcover",
                "qfit_simplifies": [
                    {
                        "property": "paint.fill-color",
                        "from": (
                            '["match",["get","class"],"wood",'
                            '"hsla(103,50%,60%,0.8)","hsl(98,48%,67%)"]'
                        ),
                        "to": '"hsl(98,48%,67%)"',
                    },
                    {
                        "property": "paint.fill-opacity",
                        "from": (
                            '["interpolate",["exponential",1.5],["zoom"],8,0.8,12,0]'
                        ),
                        "to": "0.8",
                    },
                    {
                        "property": "layout.visibility",
                        "from": '"visible"',
                        "to": '"none"',
                    },
                ],
            },
            {
                "id": "contour",
                "qgis_converter_warnings": {
                    "by_message": [{"count": 1, "message": "Example contour warning"}],
                },
            },
        ],
        "summary": {
            "terrain_landcover_palette_candidates": [
                {
                    "layer": "landcover",
                    "source_layer": "landcover",
                    "type": "fill",
                    "zoom_band": "z0-z12",
                    "filter_operator_signature": "all, get, match",
                    "terrain_landcover_palette_control_properties": [
                        "filter",
                        "paint.fill-color",
                        "paint.fill-opacity",
                    ],
                    "airport_special_landuse_control_properties": [
                        "paint.unused-airport-color",
                    ],
                    "qfit_simplified_control_properties": [
                        "paint.fill-color",
                        "paint.fill-opacity",
                    ],
                    "qgis_dependent_control_properties": ["filter"],
                },
                {
                    "layer": "contour",
                    "source_layer": "contour",
                    "type": "line",
                    "zoom_band": "z11+",
                    "terrain_landcover_palette_control_properties": [
                        "filter",
                        "paint.line-color",
                    ],
                    "qgis_dependent_control_properties": ["filter"],
                },
            ],
            "terrain_landcover_palette_candidates_by_source_layer": [
                {"source_layer": "landcover", "count": 1},
                {"source_layer": "contour", "count": 1},
            ],
            "terrain_landcover_palette_candidates_by_type": [
                {"type": "fill", "count": 1},
                {"type": "line", "count": 1},
            ],
            "terrain_landcover_palette_simplified_by_property": [
                {"property": "paint.fill-opacity", "count": 1},
            ],
            "terrain_landcover_palette_qgis_dependent_by_property": [
                {"property": "filter", "count": 2},
            ],
            "airport_special_landuse_candidates": [
                {
                    "layer": "landuse-other-z10-plus-airport",
                    "source_layer": "landuse",
                    "type": "fill",
                    "zoom_band": "z10+",
                    "airport_special_landuse_control_properties": [
                        "filter",
                        "paint.fill-color",
                    ],
                    "qfit_simplified_control_properties": ["filter"],
                    "qgis_dependent_control_properties": ["filter"],
                },
            ],
            "airport_special_landuse_candidates_by_source_layer": [
                {"source_layer": "landuse", "count": 1},
            ],
            "airport_special_landuse_candidates_by_type": [
                {"type": "fill", "count": 1},
            ],
            "airport_special_landuse_simplified_by_property": [
                {"property": "filter", "count": 1},
            ],
            "airport_special_landuse_qgis_dependent_by_property": [
                {"property": "filter", "count": 1},
            ],
        }
    }


class MapboxOutdoorsVisualCropsTest(unittest.TestCase):
    def test_build_run_directory_and_paths_are_predictable(self):
        run_dir = build_run_directory(
            output_root=Path("/tmp/visual-crops"),
            now=dt.datetime(2026, 5, 20, 21, 0, tzinfo=dt.timezone.utc),
        )
        paths = build_visual_crop_paths(run_dir)

        self.assertEqual(run_dir, Path("/tmp/visual-crops/20260520T210000Z"))
        self.assertEqual(paths.json_path, run_dir / "visual-crops.json")
        self.assertEqual(paths.summary_path, run_dir / "summary.md")
        self.assertEqual(paths.contact_sheet_path, run_dir / "crop-sheet.jpg")

    def test_parse_crop_size_requires_positive_width_and_height(self):
        self.assertEqual(parse_crop_size("320x240"), (320, 240))

        with self.assertRaises(argparse.ArgumentTypeError):
            parse_crop_size("0x240")

        with self.assertRaises(argparse.ArgumentTypeError):
            parse_crop_size("320-by-240")

    def test_three_channel_color_values_handles_short_stat_outputs(self):
        self.assertEqual(_three_channel_color_values([]), [0.0, 0.0, 0.0])
        self.assertEqual(_three_channel_color_values([5]), [5.0, 5.0, 5.0])
        self.assertEqual(_three_channel_color_values([5, 6]), [5.0, 6.0, 0.0])
        self.assertEqual(_three_channel_color_values([5, 6, 7, 8]), [5.0, 6.0, 7.0])

    def test_crop_color_metric_handles_empty_stat_outputs(self):
        image_module = _FakeImageModule()
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "crop.png"
            _save_rgb_image(image_path, image_module, (5, 5, 5))

            metric = _crop_color_metric(
                image_path=image_path,
                image_module=image_module,
                image_stat_module=_EmptyColorStatModule,
            )

        self.assertEqual(metric["mean_rgb"], [0.0, 0.0, 0.0])
        self.assertEqual(metric["luminance"], 0.0)

    def test_crop_color_metric_pads_two_channel_fallback_stats(self):
        image_module = _FakeImageModule()
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "crop.png"
            _save_rgb_image(image_path, image_module, (5, 5, 5))

            metric = _crop_color_metric(
                image_path=image_path,
                image_module=image_module,
                image_stat_module=_TwoChannelColorSumStatModule,
            )

        self.assertEqual(metric["mean_rgb"], [10.0, 20.0, 0.0])
        self.assertEqual(metric["luminance"], 16.43)

    def test_find_hotspot_crop_boxes_prefers_bright_non_overlapping_regions(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            diff_path = Path(tmpdir) / "diff.png"
            diff = image_module.new("RGB", (12, 12), (0, 0, 0))
            for point in ((9, 9), (10, 9), (2, 2)):
                diff.putpixel(point, (255, 255, 255))
            diff.save(diff_path)
            diff.close()

            boxes = find_hotspot_crop_boxes(
                diff_path,
                crop_size=(4, 4),
                crop_count=2,
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

        self.assertEqual(len(boxes), 2)
        self.assertTrue(_box_contains(boxes[0]["box"], (9, 9)))
        self.assertTrue(_box_contains(boxes[0]["box"], (10, 9)))
        self.assertTrue(_box_contains(boxes[1]["box"], (2, 2)))

    def test_find_hotspot_crop_boxes_returns_empty_for_blank_diff(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            diff_path = Path(tmpdir) / "diff.png"
            _save_rgb_image(diff_path, image_module, (0, 0, 0), size=(8, 8))

            boxes = find_hotspot_crop_boxes(
                diff_path,
                crop_size=(20, 20),
                crop_count=1,
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

        self.assertEqual(boxes, [])

    def test_find_hotspot_crop_boxes_returns_empty_for_zero_crop_count(self):
        boxes = find_hotspot_crop_boxes(Path("/missing/diff.png"), crop_count=0)

        self.assertEqual(boxes, [])

    def test_generate_visual_crop_report_writes_triplet_crops_and_contact_sheet(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_root = root / "comparison"
            summary_path = comparison_root / "all-cameras" / "run" / "summary.json"
            camera_dir = comparison_root / "chamonix-trails-z14-outdoors" / "run"
            browser_path = camera_dir / "mapbox-gl-reference.png"
            qgis_path = camera_dir / "qgis-vector-render.png"
            diff_path = camera_dir / "mapbox-gl-vs-qgis-diff.png"
            camera_dir.mkdir(parents=True)
            summary_path.parent.mkdir(parents=True)
            _save_rgb_image(browser_path, image_module, (255, 255, 255))
            _save_rgb_image(qgis_path, image_module, (128, 128, 128))
            diff = image_module.new("RGB", (12, 12), (0, 0, 0))
            diff.putpixel((9, 9), (255, 255, 255))
            diff.save(diff_path)
            diff.close()
            comparison_summary = {
                "cameras": [
                    {
                        "camera": "chamonix-trails-z14-outdoors",
                        "status": "passed",
                        "artifact_status": "metrics_available",
                        "metrics": {
                            "changed_pixel_ratio": 0.9820199652777778,
                            "normalized_mean_absolute_channel_delta": 0.03523346722948439,
                            "normalized_rms_channel_delta": 0.07608105570020197,
                            "ssim_status": "unavailable",
                        },
                        "outputs": {
                            "browser_reference": str(browser_path),
                            "qgis_vector_render": str(qgis_path),
                            "diff": str(diff_path),
                        },
                    }
                ],
            }
            run_dir = root / "debug" / "run"
            paths = build_visual_crop_paths(run_dir)

            with patch(
                "qfit.validation.mapbox_outdoors_visual_crops.build_all_cameras_contact_sheet",
                side_effect=_fake_contact_sheet,
            ):
                report = generate_visual_crop_report(
                    comparison_summary,
                    comparison_summary_path=summary_path,
                    paths=paths,
                    crop_size=(4, 4),
                    crops_per_camera=1,
                    trusted_output_root=root / "debug",
                    image_module=image_module,
                    image_stat_module=image_stat_module,
                )
            write_report(report, paths, trusted_output_root=root / "debug")

            crop = report["cameras"][0]["crops"][0]
            outputs = crop["outputs"]
            for path_text in outputs.values():
                self.assertTrue((Path.cwd() / path_text).exists())
            self.assertEqual(
                crop["color_metrics"],
                {
                    "browser_reference": {
                        "mean_rgb": [255.0, 255.0, 255.0],
                        "luminance": 255.0,
                    },
                    "qgis_vector_render": {
                        "mean_rgb": [128.0, 128.0, 128.0],
                        "luminance": 128.0,
                    },
                    "delta": {
                        "mean_rgb": [-127.0, -127.0, -127.0],
                        "luminance": -127.0,
                        "luminance_direction": "darker",
                        "dominant_rgb_delta": {
                            "channel": "red",
                            "delta": -127.0,
                            "direction": "lower",
                        },
                    },
                },
            )
            self.assertEqual(report["crop_count"], 1)
            self.assertEqual(
                report["crop_color_movement_groups"],
                [
                    {
                        "movement": "darker + red lower",
                        "luminance_direction": "darker",
                        "dominant_rgb_channel": "red",
                        "dominant_rgb_direction": "lower",
                        "crop_count": 1,
                        "max_abs_rgb_delta": 127.0,
                        "max_abs_luminance_delta": 127.0,
                        "cameras": {"chamonix-trails-z14-outdoors": 1},
                        "representative_crop": {
                            "camera": "chamonix-trails-z14-outdoors",
                            "crop": 1,
                            "score": 127.0,
                            "box": [6, 6, 10, 10],
                            "diff": outputs["diff"],
                            "qgis_minus_mapbox_rgb": [-127.0, -127.0, -127.0],
                            "max_abs_rgb_delta": 127.0,
                            "qgis_minus_mapbox_luminance": -127.0,
                        },
                    }
                ],
            )
            self.assertTrue(paths.contact_sheet_path.exists())
            self.assertTrue(paths.json_path.exists())
            self.assertTrue(paths.summary_path.exists())
            loaded = json.loads(paths.json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["camera_count"], 1)
            self.assertEqual(
                loaded["crop_color_movement_groups"],
                report["crop_color_movement_groups"],
            )
            self.assertEqual(
                loaded["cameras"][0]["comparison"],
                {
                    "artifact_status": "metrics_available",
                    "changed_pixel_ratio": 0.9820199652777778,
                    "normalized_mean_absolute_channel_delta": 0.03523346722948439,
                    "normalized_rms_channel_delta": 0.07608105570020197,
                    "ssim_status": "unavailable",
                    "status": "passed",
                },
            )
            summary = paths.summary_path.read_text(encoding="utf-8")
            self.assertIn("## Largest crop color deltas", summary)
            self.assertIn(
                f"| chamonix-trails-z14-outdoors | 1 | [6,6,10,10] | `{outputs['diff']}` | -127.0, -127.0, -127.0 | -127.0 | 127.0 | darker; red -127.0 |",
                summary,
            )
            self.assertIn("## Crop color metrics", summary)
            self.assertIn("## Crop color movement groups", summary)
            self.assertIn(
                (
                    f"| darker + red lower | 1 | 127.0 | 127.0 | chamonix-trails-z14-outdoors=1 | "
                    f"chamonix-trails-z14-outdoors crop 1 [6,6,10,10] `{outputs['diff']}` | "
                    "-127.0, -127.0, -127.0 | -127.0 |"
                ),
                summary,
            )
            self.assertIn("| chamonix-trails-z14-outdoors | 1 | 255.0, 255.0, 255.0 | 128.0, 128.0, 128.0 | -127.0, -127.0, -127.0 | -127.0 | darker; red -127.0 |", summary)

    def test_generate_visual_crop_report_marks_missing_required_artifacts(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            summary_path = root / "comparison" / "all-cameras" / "run" / "summary.json"
            summary_path.parent.mkdir(parents=True)
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                {"cameras": [{"camera": "missing-camera", "status": "passed"}]},
                comparison_summary_path=summary_path,
                paths=paths,
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            self.assertEqual(report["camera_count"], 1)
            self.assertEqual(report["crop_count"], 0)
            self.assertIsNone(report["contact_sheet"])
            self.assertEqual(report["cameras"][0]["status"], "missing_required_artifacts")

    def test_generate_visual_crop_report_marks_blank_diffs_without_crops(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            diff_path = Path(comparison_summary["cameras"][0]["outputs"]["diff"])
            _save_rgb_image(diff_path, image_module, (0, 0, 0))
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            self.assertEqual(report["crop_count"], 0)
            self.assertEqual(report["cameras"][0]["status"], "no_hotspot_crops")
            self.assertEqual(report["cameras"][0]["crops"], [])

    def test_generate_visual_crop_report_attaches_path_pedestrian_focus_cues(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            focus_path = root / "focus" / "path-pedestrian-focus.json"
            focus_path.parent.mkdir(parents=True)
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    path_pedestrian_focus_report=_path_pedestrian_focus_report(
                        comparison_summary_jsons=[str(summary_path)]
                    ),
                    path_pedestrian_focus_report_path=focus_path,
                ),
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            focus_cues = report["cameras"][0]["path_pedestrian_focus"]
            stroke_cues = focus_cues["stroke_width_deltas"]
            dash_cues = focus_cues["dash_mismatches"]
            self.assertEqual(report["path_pedestrian_focus_json"], str(focus_path))
            self.assertEqual(
                report["path_pedestrian_focus_comparison_summary_jsons"],
                [str(summary_path)],
            )
            self.assertIs(report["path_pedestrian_focus_comparison_match"], True)
            self.assertEqual(stroke_cues[0]["source_layer_id"], "road-path-trail")
            self.assertEqual(stroke_cues[0]["candidate_types"], ["trail=69", "hiking=5"])
            self.assertEqual(dash_cues[0]["source_layer_id"], "road-steps")
            self.assertEqual(dash_cues[0]["source_dasharray"], [0.3, 0.3])

    def test_generate_visual_crop_report_attaches_style_audit_area_fill_focus(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            style_audit_path = root / "style-audit" / "audit.json"
            style_audit_path.parent.mkdir(parents=True)
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    style_audit_report=_style_audit_report(),
                    style_audit_report_path=style_audit_path,
                ),
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            self.assertEqual(report["style_audit_json"], str(style_audit_path))
            focus = report["style_audit_area_fill_focus"]
            self.assertEqual([row["key"] for row in focus], ["terrain_landcover", "airport_special_landuse"])
            self.assertEqual(focus[0]["candidate_count"], 2)
            self.assertEqual(
                focus[0]["filter_signatures"],
                [{"filter_operator_signature": "all, get, match", "count": 1}],
            )
            focus[0]["sample_candidates"].append({"source_layer": "landcover", "type": "fill"})
            markdown = build_summary_markdown(report)
            self.assertEqual(
                focus[0]["sample_candidates"][0],
                {
                    "layer": "landcover",
                    "source_layer": "landcover",
                    "type": "fill",
                    "zoom_band": "z0-z12",
                    "filter_operator_signature": "all, get, match",
                    "control_properties": ["filter", "paint.fill-color", "paint.fill-opacity"],
                    "qfit_simplifications": [
                        {
                            "property": "paint.fill-color",
                            "from": (
                                '["match",["get","class"],"wood",'
                                '"hsla(103,50%,60%,0.8)","hsl(98,48%,67%)"]'
                            ),
                            "to": '"hsl(98,48%,67%)"',
                        },
                        {
                            "property": "paint.fill-opacity",
                            "from": (
                                '["interpolate",["exponential",1.5],["zoom"],8,0.8,12,0]'
                            ),
                            "to": "0.8",
                        },
                    ],
                    "qfit_simplified_properties": ["paint.fill-color", "paint.fill-opacity"],
                    "qgis_dependent_properties": ["filter"],
                },
            )
            self.assertIn("Style audit input: `", markdown)
            self.assertIn("## Style audit area-fill focus", markdown)
            self.assertIn("Terrain/landcover", markdown)
            self.assertIn("landcover=1", markdown)
            self.assertIn("all, get, match=1", markdown)
            self.assertIn("landuse-other-z10-plus-airport", markdown)
            self.assertIn(
                "landcover (landcover/fill; filter-ops: all, get, match; controls=filter, paint.fill-color, "
                "paint.fill-opacity; qfit=paint.fill-color, paint.fill-opacity; "
                "simplifies=paint.fill-color",
                markdown,
            )
            self.assertIn(
                'simplifies=paint.fill-color: ["match",["get","class"],"wood",'
                '"hsla(103,50%,60%,0.8)","hsl(98,48%,67%)"] -> "hsl(98,48%,67%)"',
                markdown,
            )
            self.assertIn(r" \| paint.fill-opacity:", markdown)
            self.assertNotIn("layout.visibility", markdown)
            self.assertIn("unknown-layer (landcover/fill)", markdown)
            self.assertIn("qgis-warnings=Example contour warning", markdown)
            self.assertNotIn("None (", markdown)

    def test_generate_visual_crop_report_keeps_zero_candidate_style_audit_sections(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            style_audit_report = _style_audit_report()
            summary = style_audit_report["summary"]
            summary["airport_special_landuse_candidates"] = []
            summary["airport_special_landuse_candidates_by_source_layer"] = []
            summary["airport_special_landuse_candidates_by_type"] = []
            summary["airport_special_landuse_simplified_by_property"] = []
            summary["airport_special_landuse_qgis_dependent_by_property"] = []
            style_audit_path = root / "style-audit" / "audit.json"
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    style_audit_report=style_audit_report,
                    style_audit_report_path=style_audit_path,
                ),
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            focus = report["style_audit_area_fill_focus"]
            self.assertEqual(
                [row["key"] for row in focus],
                ["terrain_landcover", "airport_special_landuse"],
            )
            self.assertEqual(focus[1]["candidate_count"], 0)
            self.assertEqual(focus[1]["sample_candidates"], [])
            markdown = build_summary_markdown(report)
            self.assertIn("| Airport/special landuse | 0 | - | - | - | - | - | - |", markdown)

    def test_generate_visual_crop_report_samples_representative_area_fill_candidates(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            style_audit_report = _style_audit_report()
            style_audit_report["summary"]["terrain_landcover_palette_candidates"] = [
                {"layer": "landcover", "source_layer": "landcover", "type": "fill"},
                {"layer": "landuse", "source_layer": "landuse", "type": "fill"},
                {"layer": "national-park", "source_layer": "landuse_overlay", "type": "fill"},
                {"layer": "wetland", "source_layer": "landuse_overlay", "type": "fill"},
                {
                    "layer": "wetland-pattern",
                    "source_layer": "landuse_overlay",
                    "type": "fill",
                    "qgis_dependent_control_properties": ["filter", "paint.fill-pattern"],
                    "qgis_converter_warnings": {
                        "count": 1,
                        "warnings": ["wetland-pattern: Could not retrieve sprite 'wetland'"],
                    },
                },
                {"layer": "contour-line", "source_layer": "contour", "type": "line"},
                {
                    "layer": "national-park_tint-band",
                    "source_layer": "landuse_overlay",
                    "type": "line",
                },
                {"layer": "pitch-outline", "source_layer": "landuse", "type": "line"},
            ]
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    style_audit_report=style_audit_report,
                    style_audit_report_path=root / "style-audit" / "audit.json",
                ),
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            terrain_samples = report["style_audit_area_fill_focus"][0]["sample_candidates"]
            self.assertEqual(
                [sample["layer"] for sample in terrain_samples],
                [
                    "landcover",
                    "landuse",
                    "wetland-pattern",
                    "contour-line",
                    "national-park_tint-band",
                ],
            )
            self.assertNotIn("wetland (", build_summary_markdown(report))
            self.assertIn("wetland-pattern", build_summary_markdown(report))
            self.assertIn("contour-line", build_summary_markdown(report))
            self.assertIn(
                "qgis-warnings=Could not retrieve sprite 'wetland'",
                build_summary_markdown(report),
            )

    def test_generate_visual_crop_report_attaches_matching_comparison_delta_context(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            delta_path = root / "delta" / "comparison-delta.json"
            delta_path.parent.mkdir(parents=True)
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )
            report = annotate_visual_crop_report_with_comparison_delta(
                report,
                comparison_summary_path=summary_path,
                comparison_delta_report=_comparison_delta_report(summary_path),
                comparison_delta_report_path=delta_path,
            )
            markdown = build_summary_markdown(report)

            self.assertEqual(report["comparison_delta_json"], str(delta_path))
            self.assertEqual(
                report["comparison_delta_candidate_summary_json"],
                str(summary_path),
            )
            self.assertIs(report["comparison_delta_candidate_summary_match"], True)
            self.assertEqual(
                report["cameras"][0]["comparison_delta"],
                {
                    "baseline_status": "passed",
                    "candidate_status": "passed",
                    "changed_pixel_ratio_delta": -0.0000008680555555,
                    "mean_delta": -0.00015750385802469,
                    "mean_delta_direction": "improved",
                    "rms_delta": 0.00047919108424084,
                    "rms_delta_direction": "worsened",
                },
            )
            self.assertIn("Comparison delta input: `", markdown)
            self.assertIn("Comparison delta candidate match: `True`", markdown)
            self.assertIn("| Camera | Comparison status | Artifact status | Changed ratio | Mean delta | RMS delta | Mean movement | RMS movement |", markdown)
            self.assertIn("| chamonix-trails-z14-outdoors | passed | metrics_available | 0.9820 | 0.0352 | 0.0761 | -0.000157504 | +0.000479191 |", markdown)

    def test_generate_visual_crop_report_suppresses_mismatched_comparison_delta_context(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            delta_path = root / "delta" / "comparison-delta.json"
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )
            report = annotate_visual_crop_report_with_comparison_delta(
                report,
                comparison_summary_path=summary_path,
                comparison_delta_report=_comparison_delta_report(root / "stale" / "summary.json"),
                comparison_delta_report_path=delta_path,
            )
            markdown = build_summary_markdown(report)

            self.assertIs(report["comparison_delta_candidate_summary_match"], False)
            self.assertNotIn("comparison_delta", report["cameras"][0])
            self.assertIn("Comparison delta candidate match: `False`", markdown)
            self.assertNotIn("Mean movement", markdown)

    def test_generate_visual_crop_report_requires_comparison_delta_candidate_summary(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            delta_path = root / "delta" / "comparison-delta.json"
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )
            delta_report = _comparison_delta_report(summary_path)
            del delta_report["input_artifacts"]
            report = annotate_visual_crop_report_with_comparison_delta(
                report,
                comparison_summary_path=summary_path,
                comparison_delta_report=delta_report,
                comparison_delta_report_path=delta_path,
            )
            markdown = build_summary_markdown(report)

            self.assertEqual(report["comparison_delta_json"], str(delta_path))
            self.assertNotIn("comparison_delta_candidate_summary_match", report)
            self.assertNotIn("comparison_delta", report["cameras"][0])
            self.assertNotIn("Mean movement", markdown)

    def test_generate_visual_crop_report_can_filter_to_focus_cue_cameras(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            second_summary, _summary_path = _write_visual_triplet(
                root,
                image_module,
                camera_name="zermatt-trails-z18-outdoors",
            )
            comparison_summary["cameras"].append(second_summary["cameras"][0])
            focus_path = root / "focus" / "path-pedestrian-focus.json"
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    path_pedestrian_focus_report=_path_pedestrian_focus_report(
                        comparison_summary_jsons=[str(summary_path)]
                    ),
                    path_pedestrian_focus_report_path=focus_path,
                ),
                focus_cue_cameras_only=True,
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            self.assertIs(report["focus_cue_cameras_only"], True)
            self.assertEqual(report["camera_count"], 1)
            self.assertEqual(
                [camera["camera"] for camera in report["cameras"]],
                ["chamonix-trails-z14-outdoors"],
            )
            self.assertIn("path_pedestrian_focus", report["cameras"][0])
            self.assertIn(
                "Camera filter: `candidate-backed path/pedestrian focus cues`",
                build_summary_markdown(report),
            )

    def test_generate_visual_crop_report_filters_zero_candidate_focus_cues(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            focus_path = root / "focus" / "path-pedestrian-focus.json"
            focus_report = _path_pedestrian_focus_report(comparison_summary_jsons=[str(summary_path)])
            comparisons = focus_report["cameras"][0]["source_qgis_stroke_control_comparisons"]
            comparisons[0]["decoded_candidate_count"] = 0
            comparisons[0]["decoded_candidate_type_counts"] = {}
            comparisons[2]["decoded_candidate_count"] = 0
            comparisons[2]["decoded_candidate_type_counts"] = {}
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    path_pedestrian_focus_report=focus_report,
                    path_pedestrian_focus_report_path=focus_path,
                ),
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            self.assertIs(report["path_pedestrian_focus_comparison_match"], True)
            self.assertNotIn("path_pedestrian_focus", report["cameras"][0])

    def test_generate_visual_crop_report_suppresses_mismatched_focus_cues(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            focus_path = root / "focus" / "path-pedestrian-focus.json"
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    path_pedestrian_focus_report=_path_pedestrian_focus_report(),
                    path_pedestrian_focus_report_path=focus_path,
                ),
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            self.assertEqual(
                report["path_pedestrian_focus_comparison_summary_jsons"],
                ["debug/comparison/summary.json"],
            )
            self.assertIs(report["path_pedestrian_focus_comparison_match"], False)
            self.assertNotIn("path_pedestrian_focus", report["cameras"][0])

    def test_generate_visual_crop_report_rejects_focus_filter_with_mismatched_focus_cues(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            paths = build_visual_crop_paths(root / "debug" / "run")

            with self.assertRaisesRegex(
                ValueError,
                "Focus cue camera filtering requires .* to match the comparison summary",
            ):
                generate_visual_crop_report(
                    comparison_summary,
                    comparison_summary_path=summary_path,
                    paths=paths,
                    annotation_inputs=VisualCropAnnotationInputs(
                        path_pedestrian_focus_report=_path_pedestrian_focus_report(),
                        path_pedestrian_focus_report_path=root / "focus" / "path-pedestrian-focus.json",
                    ),
                    focus_cue_cameras_only=True,
                    crop_size=(4, 4),
                    crops_per_camera=1,
                    trusted_output_root=root / "debug",
                    image_module=image_module,
                    image_stat_module=image_stat_module,
                )

    def test_generate_visual_crop_report_matches_focus_comparison_runs(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            focus_path = root / "focus" / "path-pedestrian-focus.json"
            focus_report = _path_pedestrian_focus_report(comparison_summary_jsons=[])
            focus_report["input_artifacts"] = {
                "comparison_summary_runs": [{"path": str(summary_path)}],
            }
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    path_pedestrian_focus_report=focus_report,
                    path_pedestrian_focus_report_path=focus_path,
                ),
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            self.assertEqual(
                report["path_pedestrian_focus_comparison_summary_jsons"],
                [str(summary_path)],
            )
            self.assertIs(report["path_pedestrian_focus_comparison_match"], True)

    def test_generate_visual_crop_report_preserves_empty_focus_comparison_paths(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    path_pedestrian_focus_report=_path_pedestrian_focus_report(
                        comparison_summary_jsons=[]
                    ),
                    path_pedestrian_focus_report_path=root / "focus" / "path-pedestrian-focus.json",
                ),
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            self.assertNotIn("path_pedestrian_focus_comparison_summary_jsons", report)
            self.assertNotIn("path_pedestrian_focus_comparison_match", report)

    def test_generate_visual_crop_report_omits_focus_comparison_without_input_artifacts(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            paths = build_visual_crop_paths(root / "debug" / "run")

            report = generate_visual_crop_report(
                comparison_summary,
                comparison_summary_path=summary_path,
                paths=paths,
                annotation_inputs=VisualCropAnnotationInputs(
                    path_pedestrian_focus_report={"cameras": []},
                    path_pedestrian_focus_report_path=root / "focus" / "path-pedestrian-focus.json",
                ),
                crop_size=(4, 4),
                crops_per_camera=1,
                trusted_output_root=root / "debug",
                image_module=image_module,
                image_stat_module=image_stat_module,
            )

            self.assertEqual(
                report["path_pedestrian_focus_json"],
                str(root / "focus" / "path-pedestrian-focus.json"),
            )
            self.assertNotIn("path_pedestrian_focus_comparison_summary_jsons", report)
            self.assertNotIn("path_pedestrian_focus_comparison_match", report)

    def test_generate_visual_crop_report_rejects_output_outside_trusted_root(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = build_visual_crop_paths(root / "outside" / "run")

            with self.assertRaisesRegex(ValueError, "Visual crop output must stay under"):
                generate_visual_crop_report(
                    {"cameras": []},
                    comparison_summary_path=root / "comparison" / "all-cameras" / "summary.json",
                    paths=paths,
                    trusted_output_root=root / "debug",
                    image_module=image_module,
                    image_stat_module=image_stat_module,
                )

    def test_write_report_rejects_output_outside_trusted_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = build_visual_crop_paths(root / "outside" / "run")

            with self.assertRaisesRegex(ValueError, "Visual crop output must stay under"):
                write_report({"cameras": []}, paths, trusted_output_root=root / "debug")

    def test_build_summary_markdown_lists_crop_paths(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "comparison_summary_run": {
                    "path": "debug/comparison/summary.json",
                    "generated_at": "2026-05-20T20:00:00+00:00",
                    "style_url": "mapbox://styles/mapbox/outdoors-v12",
                },
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": 1,
                "crop_count": 1,
                "contact_sheet": "debug/crops/crop-sheet.jpg",
                "cameras": [
                    {
                        "camera": "zermatt-trails-z18-outdoors",
                        "status": "cropped",
                        "comparison": {
                            "status": "passed",
                            "artifact_status": "metrics_available",
                            "changed_pixel_ratio": 0.9820199652777778,
                            "normalized_mean_absolute_channel_delta": 0.03523346722948439,
                            "normalized_rms_channel_delta": 0.07608105570020197,
                        },
                        "crops": [
                            {
                                "index": 1,
                                "box": [8, 8, 12, 12],
                                "score": 255,
                                "outputs": {
                                    "browser_reference": "debug/crops/mapbox.png",
                                    "qgis_vector_render": "debug/crops/qgis.png",
                                    "diff": "debug/crops/diff.png",
                                },
                            }
                        ],
                    }
                ],
            }
        )

        self.assertIn("# Mapbox Outdoors visual crop report", markdown)
        self.assertIn(
            "Comparison summary run: `debug/comparison/summary.json` "
            "(generated_at=2026-05-20T20:00:00+00:00, "
            "style_url=mapbox://styles/mapbox/outdoors-v12)",
            markdown,
        )
        self.assertIn("zermatt-trails-z18-outdoors", markdown)
        self.assertIn(
            "| zermatt-trails-z18-outdoors | passed | metrics_available | 0.9820 | 0.0352 | 0.0761 | cropped | 1 |",
            markdown,
        )
        self.assertIn("debug/crops/crop-sheet.jpg", markdown)

    def test_build_summary_markdown_ranks_largest_crop_color_deltas(self):
        cameras = []
        for index in range(1, 7):
            cameras.append(
                {
                    "camera": f"camera-{index}",
                    "status": "cropped",
                    "crops": [
                        {
                            "index": 1,
                            "box": [0, 0, 4, 4],
                            "score": 255,
                            "outputs": {},
                            "color_metrics": {
                                "delta": {
                                    "mean_rgb": [index, 0.0, 0.0],
                                    "luminance": 1.0,
                                }
                            },
                        }
                    ],
                }
            )

        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": len(cameras),
                "crop_count": len(cameras),
                "cameras": cameras,
            }
        )

        ranked_section = markdown.split("## Largest crop color deltas", 1)[1].split(
            "## Crop color metrics",
            1,
        )[0]
        self.assertIn("| camera-6 | 1 | [0,0,4,4] | - | 6.0, 0.0, 0.0 | +1.0 | 6.0 |", ranked_section)
        self.assertIn("| camera-2 | 1 | [0,0,4,4] | - | 2.0, 0.0, 0.0 | +1.0 | 2.0 |", ranked_section)
        self.assertLess(ranked_section.index("camera-6"), ranked_section.index("camera-2"))
        self.assertNotIn("camera-1", ranked_section)
        self.assertIn("| camera-1 | 1 | - | - | 1.0, 0.0, 0.0 | +1.0 |", markdown)

    def test_build_summary_markdown_groups_crop_color_movements(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 2,
                "camera_count": 3,
                "crop_count": 4,
                "cameras": [
                    {
                        "camera": "geneva",
                        "status": "cropped",
                        "crops": [
                            {
                                "index": 1,
                                "color_metrics": {
                                    "delta": {
                                        "mean_rgb": [4.0, 0.0, 12.0],
                                        "luminance": 6.0,
                                        "luminance_direction": "lighter",
                                        "dominant_rgb_delta": {
                                            "channel": "blue",
                                            "delta": 12.0,
                                            "direction": "higher",
                                        },
                                    }
                                },
                            },
                            {
                                "index": 2,
                                "color_metrics": {
                                    "delta": {
                                        "mean_rgb": [1.0, 0.0, 5.0],
                                        "luminance": 2.0,
                                        "luminance_direction": "lighter",
                                        "dominant_rgb_delta": {
                                            "channel": "blue",
                                            "delta": 5.0,
                                            "direction": "higher",
                                        },
                                    }
                                },
                            },
                        ],
                    },
                    {
                        "camera": "zermatt",
                        "status": "cropped",
                        "crops": [
                            {
                                "index": 1,
                                "color_metrics": {
                                    "delta": {
                                        "mean_rgb": [2.0, 0.0, 16.0],
                                        "luminance": 8.0,
                                        "luminance_direction": "lighter",
                                        "dominant_rgb_delta": {
                                            "channel": "blue",
                                            "delta": 16.0,
                                            "direction": "higher",
                                        },
                                    }
                                },
                            }
                        ],
                    },
                    {
                        "camera": "lausanne",
                        "status": "cropped",
                        "crops": [
                            {
                                "index": 1,
                                "color_metrics": {
                                    "delta": {
                                        "mean_rgb": [-18.0, -1.0, -16.0],
                                        "luminance": -6.0,
                                        "luminance_direction": "darker",
                                        "dominant_rgb_delta": {
                                            "channel": "red",
                                            "delta": -18.0,
                                            "direction": "lower",
                                        },
                                    }
                                },
                            }
                        ],
                    },
                ],
            }
        )

        movement_section = markdown.split("## Crop color movement groups", 1)[1].split(
            "## Crop color metrics",
            1,
        )[0]
        self.assertIn(
            (
                "| lighter + blue higher | 3 | 16.0 | 8.0 | geneva=2, zermatt=1 | "
                "zermatt crop 1 | 2.0, 0.0, 16.0 | +8.0 |"
            ),
            movement_section,
        )
        self.assertIn(
            (
                "| darker + red lower | 1 | 18.0 | 6.0 | lausanne=1 | lausanne crop 1 | "
                "-18.0, -1.0, -16.0 | -6.0 |"
            ),
            movement_section,
        )
        self.assertLess(
            movement_section.index("lighter + blue higher"),
            movement_section.index("darker + red lower"),
        )

    def test_computed_crop_color_movement_groups_keep_representative_crop(self):
        report = {
            "cameras": [
                {
                    "camera": "geneva",
                    "crops": [
                        {
                            "index": 1,
                            "box": [0, 0, 4, 4],
                            "outputs": {"diff": "debug/geneva-diff.png"},
                            "color_metrics": {
                                "delta": {
                                    "mean_rgb": [4.0, 0.0, 12.0],
                                    "luminance": 6.0,
                                    "luminance_direction": "lighter",
                                    "dominant_rgb_delta": {
                                        "channel": "blue",
                                        "delta": 12.0,
                                        "direction": "higher",
                                    },
                                }
                            },
                        }
                    ],
                },
                {
                    "camera": "zermatt",
                    "crops": [
                        {
                            "index": 2,
                            "box": [4, 4, 8, 8],
                            "outputs": {"diff": "debug/zermatt-diff.png"},
                            "color_metrics": {
                                "delta": {
                                    "mean_rgb": [2.0, 0.0, 16.0],
                                    "luminance": 8.0,
                                    "luminance_direction": "lighter",
                                    "dominant_rgb_delta": {
                                        "channel": "blue",
                                        "delta": 16.0,
                                        "direction": "higher",
                                    },
                                }
                            },
                        }
                    ],
                },
            ],
        }

        records = _computed_crop_color_movement_group_records(report)
        report["cameras"][1]["crops"][0]["box"][0] = 99

        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["representative_crop"],
            {
                "camera": "zermatt",
                "crop": 2,
                "score": 16.0,
                "box": [4, 4, 8, 8],
                "diff": "debug/zermatt-diff.png",
                "qgis_minus_mapbox_rgb": [2.0, 0.0, 16.0],
                "max_abs_rgb_delta": 16.0,
                "qgis_minus_mapbox_luminance": 8.0,
            },
        )

    def test_build_summary_markdown_shows_stored_movement_group_representative(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": 1,
                "crop_count": 1,
                "crop_color_movement_groups": [
                    {
                        "movement": "lighter + blue higher",
                        "crop_count": 2,
                        "max_abs_rgb_delta": 16.0,
                        "max_abs_luminance_delta": 8.0,
                        "cameras": {"zermatt": 2},
                        "representative_crop": {
                            "camera": "zermatt",
                            "crop": 2,
                            "box": [4, 4, 8, 8],
                            "diff": "debug/zermatt-diff.png",
                        },
                    }
                ],
                "cameras": [],
            }
        )

        self.assertIn(
            (
                "| lighter + blue higher | 2 | 16.0 | 8.0 | zermatt=2 | "
                "zermatt crop 2 [4,4,8,8] `debug/zermatt-diff.png` | - | - |"
            ),
            markdown,
        )

    def test_build_summary_markdown_keeps_representative_camera_visible(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": 4,
                "crop_count": 8,
                "crop_color_movement_groups": [
                    {
                        "movement": "lighter + red higher",
                        "crop_count": 8,
                        "max_abs_rgb_delta": 11.8,
                        "max_abs_luminance_delta": 9.0,
                        "cameras": {
                            "zermatt-piste-z17-outdoors": 3,
                            "geneva-airport-motorway-z14-outdoors": 2,
                            "valais-geneva-outdoors": 2,
                            "switzerland-alps-z5-outdoors": 1,
                        },
                        "representative_crop": {
                            "camera": "switzerland-alps-z5-outdoors",
                            "crop": 1,
                            "box": [210, 600, 630, 900],
                            "diff": "debug/switzerland-diff.png",
                            "qgis_minus_mapbox_rgb": [11.8, 8.9, 1.9],
                            "qgis_minus_mapbox_luminance": 9.0,
                        },
                    }
                ],
                "cameras": [],
            }
        )

        self.assertIn(
            (
                "| lighter + red higher | 8 | 11.8 | 9.0 | "
                "zermatt-piste-z17-outdoors=3, geneva-airport-motorway-z14-outdoors=2, "
                "valais-geneva-outdoors=2, switzerland-alps-z5-outdoors=1 (representative) | "
                "switzerland-alps-z5-outdoors crop 1 [210,600,630,900] `debug/switzerland-diff.png` | "
                "11.8, 8.9, 1.9 | +9.0 |"
            ),
            markdown,
        )

    def test_build_summary_markdown_handles_malformed_color_delta_values(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": 1,
                "crop_count": 2,
                "cameras": [
                    {
                        "camera": "bad-color",
                        "status": "cropped",
                        "crops": [
                            {
                                "index": 1,
                                "color_metrics": {
                                    "delta": {
                                        "mean_rgb": ["bad", 2, 3],
                                        "luminance": 9,
                                    },
                                },
                            },
                            {
                                "index": 2,
                                "color_metrics": {
                                    "delta": {
                                        "mean_rgb": ["bad"],
                                        "luminance": "bad",
                                    },
                                },
                            },
                        ],
                    },
                ],
            }
        )

        ranked_section = markdown.split("## Largest crop color deltas", 1)[1].split(
            "## Crop color metrics",
            1,
        )[0]
        self.assertIn("| bad-color | 1 | - | - | - | +9.0 | - |", ranked_section)
        self.assertNotIn("| bad-color | 2 |", ranked_section)

    def test_build_summary_markdown_lists_path_pedestrian_focus_cues(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "path_pedestrian_focus_json": "debug/focus/path-pedestrian-focus.json",
                "path_pedestrian_focus_comparison_summary_jsons": [
                    "debug/comparison/summary.json",
                ],
                "path_pedestrian_focus_comparison_match": True,
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": 1,
                "crop_count": 0,
                "cameras": [
                    {
                        "camera": "chamonix-trails-z14-outdoors",
                        "status": "no_hotspot_crops",
                        "path_pedestrian_focus": {
                            "stroke_width_deltas": [
                                {
                                    "source_layer_id": "road-path-trail",
                                    "qgis_layer_id": "road-path-trail-below-z16",
                                    "decoded_candidate_count": 74,
                                    "candidate_types": ["trail=69", "hiking=5"],
                                    "line_width_delta_mm": 0.15875,
                                    "line_width_ratio": 1.6,
                                },
                                {
                                    "source_layer_id": "road-path-bg",
                                    "qgis_layer_id": "road-path-bg-below-z16-outdoor",
                                    "candidate_types": ["trail=69"],
                                    "line_width_delta_mm": 0.079375,
                                }
                            ],
                            "dash_mismatches": [
                                {
                                    "source_layer_id": "bridge-path",
                                    "qgis_layer_id": "bridge-path",
                                    "decoded_candidate_count": 2,
                                    "source_dasharray": [1, 0.25],
                                    "qgis_dasharray": [4, 0.3],
                                }
                            ],
                        },
                    }
                ],
            }
        )

        self.assertIn("Path/pedestrian focus input: `debug/focus/path-pedestrian-focus.json`", markdown)
        self.assertIn(
            "Path/pedestrian focus comparison inputs: `debug/comparison/summary.json`",
            markdown,
        )
        self.assertIn("Path/pedestrian focus comparison match: `True`", markdown)
        self.assertIn("## Path/pedestrian focus cues", markdown)
        self.assertIn("road-path-trail->road-path-trail-below-z16", markdown)
        self.assertIn("candidates=74 (trail=69, hiking=5)", markdown)
        self.assertIn("candidate_types=trail=69", markdown)
        self.assertNotIn("candidates=None", markdown)
        self.assertIn("dash=[1,0.25]!=[4,0.3]", markdown)

    def test_build_summary_markdown_omits_zero_candidate_focus_cues(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "path_pedestrian_focus_json": "debug/focus/path-pedestrian-focus.json",
                "path_pedestrian_focus_comparison_summary_jsons": [
                    "debug/comparison/summary.json",
                ],
                "path_pedestrian_focus_comparison_match": True,
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": 1,
                "crop_count": 0,
                "cameras": [
                    {
                        "camera": "zermatt-trails-z18-outdoors",
                        "status": "cropped",
                        "path_pedestrian_focus": {
                            "stroke_width_deltas": [
                                {
                                    "source_layer_id": "bridge-steps",
                                    "qgis_layer_id": "bridge-steps",
                                    "decoded_candidate_count": 0,
                                    "line_width_delta_mm": -1.3229166666666665,
                                }
                            ],
                            "dash_mismatches": [
                                {
                                    "source_layer_id": "bridge-path",
                                    "qgis_layer_id": "bridge-path",
                                    "decoded_candidate_count": 0,
                                    "source_dasharray": [1, 0.25],
                                    "qgis_dasharray": [4, 0.3],
                                }
                            ],
                        },
                    }
                ],
            }
        )

        self.assertIn("Path/pedestrian focus comparison match: `True`", markdown)
        self.assertNotIn("## Path/pedestrian focus cues", markdown)
        self.assertNotIn("bridge-steps->bridge-steps", markdown)

    def test_build_summary_markdown_omits_missing_focus_comparison_match(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "path_pedestrian_focus_json": "debug/focus/path-pedestrian-focus.json",
                "path_pedestrian_focus_comparison_summary_jsons": [
                    "debug/comparison/summary.json",
                ],
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": 0,
                "crop_count": 0,
                "cameras": [],
            }
        )

        self.assertIn(
            "Path/pedestrian focus comparison inputs: `debug/comparison/summary.json`",
            markdown,
        )
        self.assertNotIn("Path/pedestrian focus comparison match:", markdown)
        self.assertNotIn("`None`", markdown)

    def test_build_summary_markdown_omits_mismatched_focus_cues(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/current-summary.json",
                "path_pedestrian_focus_json": "debug/focus/path-pedestrian-focus.json",
                "path_pedestrian_focus_comparison_summary_jsons": [
                    "debug/comparison/stale-summary.json",
                ],
                "path_pedestrian_focus_comparison_match": False,
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": 1,
                "crop_count": 0,
                "cameras": [
                    {
                        "camera": "chamonix-trails-z14-outdoors",
                        "status": "no_hotspot_crops",
                        "path_pedestrian_focus": {
                            "stroke_width_deltas": [
                                {
                                    "source_layer_id": "road-path-trail",
                                    "qgis_layer_id": "road-path-trail-below-z16",
                                    "decoded_candidate_count": 74,
                                }
                            ],
                            "dash_mismatches": [],
                        },
                    }
                ],
            }
        )

        self.assertIn("Path/pedestrian focus comparison match: `False`", markdown)
        self.assertNotIn("## Path/pedestrian focus cues", markdown)
        self.assertNotIn("road-path-trail->road-path-trail-below-z16", markdown)

    def test_build_summary_markdown_lists_empty_camera_status(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "crop_size": None,
                "crops_per_camera": 1,
                "camera_count": 1,
                "crop_count": 0,
                "cameras": [{"camera": "zermatt-piste-z17-outdoors", "status": "missing_diff"}],
            }
        )

        self.assertIn("Crop size: `-`", markdown)
        self.assertIn(
            "| zermatt-piste-z17-outdoors | - | - | - | - | - | missing_diff | - | - | - | - | - | - |",
            markdown,
        )

    def test_main_writes_visual_crop_report_from_json_input(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            summary_path.write_text(json.dumps(comparison_summary), encoding="utf-8")
            focus_path = root / "focus" / "path-pedestrian-focus.json"
            focus_path.parent.mkdir(parents=True)
            focus_path.write_text(
                json.dumps(
                    _path_pedestrian_focus_report(
                        comparison_summary_jsons=[str(summary_path)]
                    )
                ),
                encoding="utf-8",
            )
            delta_path = root / "delta" / "comparison-delta.json"
            delta_path.parent.mkdir(parents=True)
            delta_path.write_text(
                json.dumps(_comparison_delta_report(summary_path)),
                encoding="utf-8",
            )
            style_audit_path = root / "style-audit" / "audit.json"
            style_audit_path.parent.mkdir(parents=True)
            style_audit_path.write_text(
                json.dumps(_style_audit_report()),
                encoding="utf-8",
            )
            output_root = root / "debug"
            stdout = io.StringIO()

            with patch(
                "qfit.validation.mapbox_outdoors_visual_crops.DEFAULT_OUTPUT_ROOT",
                output_root,
            ), patch(
                "qfit.validation.mapbox_outdoors_visual_crops._image_modules",
                return_value=(image_module, image_stat_module),
            ), patch(
                "qfit.validation.mapbox_outdoors_visual_crops.build_all_cameras_contact_sheet",
                side_effect=_fake_contact_sheet,
            ), contextlib.redirect_stdout(stdout):
                result = main(
                    [
                        "--comparison-summary-json",
                        str(summary_path),
                        "--path-pedestrian-focus-json",
                        str(focus_path),
                        "--comparison-delta-json",
                        str(delta_path),
                        "--style-audit-json",
                        str(style_audit_path),
                        "--camera",
                        "chamonix-trails-z14-outdoors",
                        "--crops-per-camera",
                        "1",
                        "--crop-size",
                        "4x4",
                    ]
                )

            self.assertEqual(result, 0)
            report_path = Path(stdout.getvalue().strip()).parent / "visual-crops.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["crop_count"], 1)
            self.assertEqual(
                report["comparison_summary_run"],
                {
                    "path": str(summary_path),
                    "generated_at": "2026-05-20T20:00:00+00:00",
                    "style_url": "mapbox://styles/mapbox/outdoors-v12",
                },
            )
            self.assertEqual(
                report["path_pedestrian_focus_comparison_summary_jsons"],
                [str(summary_path)],
            )
            self.assertIs(report["comparison_delta_candidate_summary_match"], True)
            self.assertIn("comparison_delta", report["cameras"][0])
            self.assertEqual(report["style_audit_json"], str(style_audit_path))
            self.assertEqual(report["style_audit_area_fill_focus"][0]["candidate_count"], 2)
            self.assertIs(report["path_pedestrian_focus_comparison_match"], True)
            self.assertEqual(report["path_pedestrian_focus_json"], str(focus_path))
            self.assertEqual(
                report["cameras"][0]["comparison"]["changed_pixel_ratio"],
                0.9820199652777778,
            )
            self.assertIn("path_pedestrian_focus", report["cameras"][0])
            self.assertTrue((report_path.parent / "crop-sheet.jpg").exists())

    def test_main_reports_bad_crop_count_without_traceback(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--comparison-summary-json", "/missing/summary.json", "--crops-per-camera", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--crops-per-camera must be greater than zero", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_main_requires_focus_report_for_focus_cue_camera_filter(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--comparison-summary-json", "/missing/summary.json", "--focus-cue-cameras"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn(
            "--focus-cue-cameras requires --path-pedestrian-focus-json",
            stderr.getvalue(),
        )
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_main_reports_missing_comparison_summary_without_traceback(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--comparison-summary-json", "/missing/summary.json"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("Comparison summary JSON not found", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
