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
                "camera": camera_name,
                "outputs": {
                    "browser_reference": str(browser_path),
                    "qgis_vector_render": str(qgis_path),
                    "diff": str(diff_path),
                },
            }
        ],
    }
    return comparison_summary, summary_path


def _path_pedestrian_focus_report(camera_name="chamonix-trails-z14-outdoors"):
    return {
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
                ],
            }
        ]
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
            self.assertEqual(report["crop_count"], 1)
            self.assertTrue(paths.contact_sheet_path.exists())
            self.assertTrue(paths.json_path.exists())
            self.assertTrue(paths.summary_path.exists())
            loaded = json.loads(paths.json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["camera_count"], 1)

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
                path_pedestrian_focus_report=_path_pedestrian_focus_report(),
                path_pedestrian_focus_report_path=focus_path,
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
            self.assertEqual(stroke_cues[0]["source_layer_id"], "road-path-trail")
            self.assertEqual(stroke_cues[0]["candidate_types"], ["trail=69", "hiking=5"])
            self.assertEqual(dash_cues[0]["source_dasharray"], [1, 0.25])

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
                "crop_size": {"width": 4, "height": 4},
                "crops_per_camera": 1,
                "camera_count": 1,
                "crop_count": 1,
                "contact_sheet": "debug/crops/crop-sheet.jpg",
                "cameras": [
                    {
                        "camera": "zermatt-trails-z18-outdoors",
                        "status": "cropped",
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
        self.assertIn("zermatt-trails-z18-outdoors", markdown)
        self.assertIn("debug/crops/crop-sheet.jpg", markdown)

    def test_build_summary_markdown_lists_path_pedestrian_focus_cues(self):
        markdown = build_summary_markdown(
            {
                "generated": "2026-05-20T20:00:00+00:00",
                "comparison_summary_json": "debug/comparison/summary.json",
                "path_pedestrian_focus_json": "debug/focus/path-pedestrian-focus.json",
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

        self.assertIn("Path/pedestrian focus input: `debug/focus/path-pedestrian-focus.json`", markdown)
        self.assertIn("## Path/pedestrian focus cues", markdown)
        self.assertIn("road-path-trail->road-path-trail-below-z16", markdown)
        self.assertIn("candidates=74 (trail=69, hiking=5)", markdown)
        self.assertIn("dash=[1,0.25]!=[4,0.3]", markdown)

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
        self.assertIn("| zermatt-piste-z17-outdoors | - | missing_diff | - | - | - | - |", markdown)

    def test_main_writes_visual_crop_report_from_json_input(self):
        image_module, image_stat_module = _fake_image_modules()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_summary, summary_path = _write_visual_triplet(root, image_module)
            summary_path.write_text(json.dumps(comparison_summary), encoding="utf-8")
            focus_path = root / "focus" / "path-pedestrian-focus.json"
            focus_path.parent.mkdir(parents=True)
            focus_path.write_text(json.dumps(_path_pedestrian_focus_report()), encoding="utf-8")
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
            self.assertEqual(report["path_pedestrian_focus_json"], str(focus_path))
            self.assertIn("path_pedestrian_focus", report["cameras"][0])
            self.assertTrue((report_path.parent / "crop-sheet.jpg").exists())

    def test_main_reports_bad_crop_count_without_traceback(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--comparison-summary-json", "/missing/summary.json", "--crops-per-camera", "0"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--crops-per-camera must be greater than zero", stderr.getvalue())
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
