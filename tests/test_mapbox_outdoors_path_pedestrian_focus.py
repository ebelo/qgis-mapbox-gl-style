import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests import _path  # noqa: F401

from qfit.validation.mapbox_outdoors_path_pedestrian_focus import (
    build_path_pedestrian_focus_paths,
    build_path_pedestrian_focus_report,
    build_run_directory,
    build_summary_markdown,
    load_json_object,
    main,
    qgis_style_paths_from_comparison_summary,
    qgis_path_pedestrian_style_summary,
    write_report,
)


def _road_feature_report():
    path_signature = "class=path; type=trail; surface=unpaved; structure=none; layer=(missing)"
    step_signature = "class=path; type=steps; surface=paved; structure=bridge; layer=(missing)"
    return {
        "generated": "2026-05-20T01:09:13+00:00",
        "style_owner": "mapbox",
        "style_id": "outdoors-v12",
        "cameras": [
            {
                "status": "decoded",
                "camera": "chamonix-trails-z14-outdoors",
                "camera_zoom": 14.25,
                "tile_zoom": 14,
                "pedestrian_polygon_candidate_count": 10,
                "pedestrian_line_candidate_count": 115,
                "path_line_candidate_count": 236,
                "step_line_candidate_count": 19,
                "pedestrian_line_type_counts": {"pedestrian": 115},
                "path_line_type_counts": {"trail": 69, "footway": 56, "piste": 48, "path": 4},
                "step_line_structure_counts": {"none": 16, "tunnel": 2, "bridge": 1},
                "path_line_signature_counts": {path_signature: 55, "class=path; type=piste": 46},
                "step_line_signature_counts": {step_signature: 3},
            },
            {
                "status": "decoded",
                "camera": "switzerland-alps-z5-outdoors",
                "camera_zoom": 5.35,
                "tile_zoom": 5,
                "pedestrian_polygon_candidate_count": 0,
                "pedestrian_line_candidate_count": 0,
                "path_line_candidate_count": 0,
                "step_line_candidate_count": 0,
            },
        ],
    }


def _qgis_preprocessed_style():
    return {
        "version": 8,
        "layers": [
            {
                "id": "road-path",
                "type": "line",
                "filter": ["==", ["get", "class"], "path"],
                "paint": {"line-color": "#d8c6a3", "line-width": 1.5, "line-dasharray": [1, 1]},
            },
            {
                "id": "road-pedestrian",
                "type": "line",
                "paint": {
                    "line-color": "#f6f2e8",
                    "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.5],
                    "line-opacity": 0.65,
                },
            },
            {
                "id": "road-steps",
                "type": "line",
                "paint": {"line-color": "#8f7054", "line-dasharray": [0.2, 1]},
            },
            {
                "id": "road-pedestrian-polygon",
                "type": "fill",
                "filter": ["==", ["get", "class"], "pedestrian"],
                "paint": {"fill-color": "#f6f2e8"},
            },
            {
                "id": "road-label",
                "type": "symbol",
                "paint": {"text-color": "#333333"},
            },
        ],
    }


class MapboxOutdoorsPathPedestrianFocusTests(unittest.TestCase):
    def test_build_run_directory_and_paths_are_predictable(self):
        run_dir = build_run_directory(
            output_root=Path("/tmp/path-focus"),
            now=dt.datetime(2026, 5, 20, 1, 32, tzinfo=dt.timezone.utc),
        )
        paths = build_path_pedestrian_focus_paths(run_dir)

        self.assertEqual(run_dir, Path("/tmp/path-focus/20260520T013200Z"))
        self.assertEqual(paths.json_path, run_dir / "path-pedestrian-focus.json")
        self.assertEqual(paths.summary_path, run_dir / "summary.md")

    def test_load_json_object_requires_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "report.json"
            json_path.write_text('{"ok": true}\n', encoding="utf-8")
            self.assertEqual(load_json_object(json_path), {"ok": True})

            json_path.write_text('["not", "object"]\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json_object(json_path)

    def test_qgis_style_paths_from_comparison_summary_reads_manifest_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_root = root / "comparison"
            run_dir = comparison_root / "camera" / "20260520T003504Z"
            run_dir.mkdir(parents=True)
            style_path = run_dir / "qgis-preprocessed-style.json"
            manifest_path = run_dir / "manifest.json"
            style_path.write_text(json.dumps(_qgis_preprocessed_style()), encoding="utf-8")
            manifest_path.write_text(
                json.dumps({"outputs": {"qgis_preprocessed_style": str(style_path)}}),
                encoding="utf-8",
            )
            comparison_summary = {
                "cameras": [
                    {
                        "camera": "chamonix-trails-z14-outdoors",
                        "manifest": str(manifest_path),
                    }
                ]
            }

            paths = qgis_style_paths_from_comparison_summary(
                comparison_summary,
                summary_path=comparison_root / "all-cameras" / "summary.json",
            )

            self.assertEqual(paths, {"chamonix-trails-z14-outdoors": style_path.resolve()})

    def test_qgis_style_paths_from_comparison_summary_rejects_untrusted_manifest_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            comparison_root = Path(tmpdir) / "comparison"
            comparison_summary = {
                "cameras": [
                    {
                        "camera": "bad-camera",
                        "manifest": "/tmp/outside-manifest.json",
                    }
                ]
            }

            with self.assertRaises(ValueError):
                qgis_style_paths_from_comparison_summary(
                    comparison_summary,
                    summary_path=comparison_root / "all-cameras" / "summary.json",
                )

    def test_qgis_style_paths_from_comparison_summary_rejects_untrusted_style_paths_with_explicit_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            comparison_root = Path(tmpdir) / "comparison"
            run_dir = comparison_root / "camera" / "20260520T003504Z"
            run_dir.mkdir(parents=True)
            manifest_path = run_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps({"outputs": {"qgis_preprocessed_style": "/tmp/outside-style.json"}}),
                encoding="utf-8",
            )
            comparison_summary = {
                "cameras": [
                    {
                        "camera": "chamonix-trails-z14-outdoors",
                        "manifest": str(manifest_path),
                    }
                ]
            }

            with self.assertRaises(ValueError) as raised:
                qgis_style_paths_from_comparison_summary(
                    comparison_summary,
                    summary_path=comparison_root / "all-cameras" / "summary.json",
                )

            self.assertIn("/tmp/outside-style.json", str(raised.exception))

    def test_qgis_style_paths_from_comparison_summary_rejects_untrusted_style_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest_path = root / "camera" / "manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps({"outputs": {"qgis_preprocessed_style": "/tmp/outside-style.json"}}),
                encoding="utf-8",
            )
            comparison_summary = {
                "cameras": [
                    {
                        "camera": "bad-camera",
                        "manifest": str(manifest_path),
                    }
                ]
            }

            with self.assertRaisesRegex(ValueError, "/tmp/outside-style.json"):
                qgis_style_paths_from_comparison_summary(
                    comparison_summary,
                    summary_path=root / "summary.json",
                    trusted_root=root,
                )

    def test_qgis_path_pedestrian_style_summary_counts_current_style_controls(self):
        summary = qgis_path_pedestrian_style_summary(_qgis_preprocessed_style())

        self.assertEqual(summary["qgis_style_status"], "available")
        self.assertEqual(summary["qgis_path_pedestrian_layer_count"], 4)
        self.assertEqual(summary["qgis_path_pedestrian_line_layer_count"], 3)
        self.assertEqual(summary["qgis_path_pedestrian_fill_layer_count"], 1)
        self.assertEqual(summary["qgis_path_pedestrian_filter_layer_count"], 2)
        self.assertEqual(summary["qgis_path_pedestrian_line_width_layer_count"], 2)
        self.assertEqual(summary["qgis_path_pedestrian_line_color_layer_count"], 3)
        self.assertEqual(summary["qgis_path_pedestrian_fill_color_layer_count"], 1)
        self.assertEqual(summary["qgis_path_pedestrian_line_dasharray_layer_count"], 2)
        self.assertEqual(summary["qgis_path_pedestrian_line_opacity_layer_count"], 1)
        self.assertEqual(
            summary["qgis_path_pedestrian_layer_ids"],
            ["road-path", "road-pedestrian", "road-steps", "road-pedestrian-polygon"],
        )
        self.assertIn("road-path=1.5", summary["qgis_path_pedestrian_line_width_samples"])
        self.assertIn('road-path="#d8c6a3"', summary["qgis_path_pedestrian_line_color_samples"])
        self.assertIn('road-pedestrian-polygon="#f6f2e8"', summary["qgis_path_pedestrian_fill_color_samples"])

    def test_build_path_pedestrian_focus_report_cross_links_feature_counts_and_qgis_style(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            qgis_styles_by_camera={"chamonix-trails-z14-outdoors": _qgis_preprocessed_style()},
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(report["generated"], "2026-05-20T01:35:00+00:00")
        self.assertEqual(report["camera_count"], 1)
        self.assertEqual(report["qgis_style_camera_count"], 1)
        self.assertEqual(report["qgis_style_input_count"], 1)
        [camera] = report["cameras"]
        self.assertEqual(camera["camera"], "chamonix-trails-z14-outdoors")
        self.assertEqual(camera["pedestrian_path_polygon_count"], 10)
        self.assertEqual(camera["pedestrian_line_count"], 115)
        self.assertEqual(camera["path_line_count"], 236)
        self.assertEqual(camera["step_line_count"], 19)
        self.assertEqual(camera["top_path_line_types"], ["trail=69", "footway=56", "piste=48"])
        self.assertEqual(camera["top_step_structures"], ["none=16", "tunnel=2", "bridge=1"])
        self.assertEqual(camera["qgis_path_pedestrian_layer_count"], 4)

    def test_build_path_pedestrian_focus_report_marks_missing_qgis_style(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        [camera] = report["cameras"]
        self.assertEqual(camera["qgis_style_status"], "missing")
        self.assertEqual(camera["qgis_path_pedestrian_layer_count"], 0)

    def test_build_path_pedestrian_focus_report_ignores_boolean_counts(self):
        road_report = {
            "generated": "2026-05-20T01:09:13+00:00",
            "cameras": [
                {
                    "status": "decoded",
                    "camera": "bad-counts",
                    "pedestrian_polygon_candidate_count": True,
                    "pedestrian_line_candidate_count": False,
                    "path_line_candidate_count": False,
                    "step_line_candidate_count": False,
                }
            ],
        }

        report = build_path_pedestrian_focus_report(road_report)

        self.assertEqual(report["camera_count"], 0)

    def test_build_summary_markdown_includes_feature_and_style_matrix(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            qgis_styles_by_camera={"chamonix-trails-z14-outdoors": _qgis_preprocessed_style()},
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        markdown = build_summary_markdown(report)

        self.assertIn("# Mapbox Outdoors path/pedestrian focus", markdown)
        self.assertIn("Focused cameras: 1", markdown)
        self.assertIn("QGIS preprocessed style cameras: 1/1 matched", markdown)
        self.assertIn("| chamonix-trails-z14-outdoors | 14.25 | 14 |", markdown)
        self.assertIn('"path_lines=236"', markdown)
        self.assertIn('"trail=69"', markdown)
        self.assertIn('"status=available"', markdown)
        self.assertIn('"total=4"', markdown)
        self.assertIn('"line_colors=3"', markdown)
        self.assertIn('"fill_colors=1"', markdown)
        self.assertIn('"road-pedestrian-polygon=\\"#f6f2e8\\""', markdown)
        self.assertIn('"road-pedestrian-polygon"', markdown)

    def test_build_summary_markdown_handles_no_focus_rows(self):
        markdown = build_summary_markdown({"generated": "now", "cameras": []})

        self.assertIn("No decoded cameras include path/pedestrian focus features.", markdown)

    def test_write_report_writes_json_and_markdown(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            qgis_styles_by_camera={"chamonix-trails-z14-outdoors": _qgis_preprocessed_style()},
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = build_path_pedestrian_focus_paths(Path(tmpdir) / "run")

            write_report(report, paths, trusted_output_root=Path(tmpdir))

            self.assertEqual(json.loads(paths.json_path.read_text(encoding="utf-8"))["camera_count"], 1)
            self.assertIn(
                "# Mapbox Outdoors path/pedestrian focus",
                paths.summary_path.read_text(encoding="utf-8"),
            )

    def test_write_report_rejects_paths_outside_trusted_root(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            trusted_root = Path(tmpdir) / "trusted"
            paths = build_path_pedestrian_focus_paths(Path(tmpdir) / "outside" / "run")

            with self.assertRaises(ValueError):
                write_report(report, paths, trusted_output_root=trusted_root)

    def test_main_writes_report_from_json_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            road_features_path = Path(tmpdir) / "road-features.json"
            style_path = Path(tmpdir) / "qgis-preprocessed-style.json"
            output_root = Path(tmpdir) / "out"
            road_features_path.write_text(json.dumps(_road_feature_report()), encoding="utf-8")
            style_path.write_text(json.dumps(_qgis_preprocessed_style()), encoding="utf-8")

            stdout = io.StringIO()
            with patch(
                "qfit.validation.mapbox_outdoors_path_pedestrian_focus.DEFAULT_OUTPUT_ROOT",
                output_root,
            ), redirect_stdout(stdout):
                result = main(
                    [
                        "--road-features-json",
                        str(road_features_path),
                        "--qgis-style-json",
                        f"chamonix-trails-z14-outdoors={style_path}",
                    ]
                )

            self.assertEqual(result, 0)
            summary_path = Path(stdout.getvalue().strip())
            self.assertTrue(summary_path.exists())
            self.assertEqual(summary_path.parent.parent, output_root)
            report_path = summary_path.parent / "path-pedestrian-focus.json"
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["camera_count"], 1)

    def test_main_loads_qgis_styles_from_comparison_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            road_features_path = root / "road-features.json"
            run_dir = root / "comparison" / "chamonix-trails-z14-outdoors" / "20260520T003504Z"
            run_dir.mkdir(parents=True)
            style_path = run_dir / "qgis-preprocessed-style.json"
            manifest_path = run_dir / "manifest.json"
            comparison_summary_path = root / "comparison" / "all-cameras" / "summary.json"
            comparison_summary_path.parent.mkdir(parents=True)
            road_features_path.write_text(json.dumps(_road_feature_report()), encoding="utf-8")
            style_path.write_text(json.dumps(_qgis_preprocessed_style()), encoding="utf-8")
            manifest_path.write_text(
                json.dumps({"outputs": {"qgis_preprocessed_style": str(style_path)}}),
                encoding="utf-8",
            )
            comparison_summary_path.write_text(
                json.dumps(
                    {
                        "cameras": [
                            {
                                "camera": "chamonix-trails-z14-outdoors",
                                "manifest": str(manifest_path),
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output_root = root / "out"

            stdout = io.StringIO()
            with patch(
                "qfit.validation.mapbox_outdoors_path_pedestrian_focus.DEFAULT_OUTPUT_ROOT",
                output_root,
            ), redirect_stdout(stdout):
                result = main(
                    [
                        "--road-features-json",
                        str(road_features_path),
                        "--comparison-summary-json",
                        str(comparison_summary_path),
                    ]
                )

            self.assertEqual(result, 0)
            report_path = Path(stdout.getvalue().strip()).parent / "path-pedestrian-focus.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["qgis_style_camera_count"], 1)
            self.assertEqual(report["qgis_style_input_count"], 1)

    def test_main_reports_missing_input_without_traceback(self):
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--road-features-json", "/missing/road-features.json"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("Road features JSON not found", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
