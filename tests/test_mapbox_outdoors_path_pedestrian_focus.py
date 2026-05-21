import datetime as dt
import io
import json
import os
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
    comparison_visual_artifacts_from_summary,
    load_json_list,
    load_json_object,
    main,
    qgis_label_style_paths_from_comparison_summary,
    qgis_path_pedestrian_label_summary,
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
                "pedestrian_line_duplicate_name_counts": {"Englischer Viertel": 5},
                "path_line_duplicate_name_counts": {"Hofmattweg": 3},
                "step_line_duplicate_name_counts": {"Kirchsteig": 2},
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
                "minzoom": 12,
                "maxzoom": 16,
                "filter": ["==", ["get", "class"], "path"],
                "paint": {"line-color": "#d8c6a3", "line-width": 1.5, "line-dasharray": [1, 1]},
            },
            {
                "id": "road-pedestrian",
                "type": "line",
                "minzoom": 16,
                "paint": {
                    "line-color": "#f6f2e8",
                    "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.5],
                    "line-opacity": 0.65,
                },
            },
            {
                "id": "road-steps",
                "type": "line",
                "minzoom": 14,
                "maxzoom": 15,
                "paint": {"line-color": "#8f7054", "line-dasharray": [0.2, 1]},
            },
            {
                "id": "road-pedestrian-polygon",
                "type": "fill",
                "minzoom": 14,
                "filter": ["==", ["get", "class"], "pedestrian"],
                "paint": {"fill-color": "#f6f2e8", "fill-opacity": 0.4},
            },
            {
                "id": "road-label-z12-to-z15",
                "type": "symbol",
                "minzoom": 12,
                "maxzoom": 15,
                "filter": [
                    "all",
                    ["has", "name"],
                    ["match", ["get", "class"], ["street", "street_limited", "track"], True, False],
                ],
                "paint": {"text-color": "#333333"},
            },
            {
                "id": "road-label-z15-plus",
                "type": "symbol",
                "minzoom": 15,
                "filter": [
                    "all",
                    ["has", "name"],
                    ["match", ["get", "class"], ["path", "pedestrian"], False, True],
                ],
                "paint": {"text-color": "#333333"},
            },
            {
                "id": "path-pedestrian-label",
                "type": "symbol",
                "minzoom": 12,
                "filter": [
                    "all",
                    ["has", "name"],
                    ["any", ["!", ["has", "layer"]], [">=", ["get", "layer"], 0]],
                    ["match", ["get", "class"], ["pedestrian"], True, False],
                ],
                "paint": {"text-color": "#333333"},
            },
        ],
    }


def _source_style():
    return {
        "version": 8,
        "layers": [
            {
                "id": "road-path",
                "type": "line",
                "minzoom": 12,
                "filter": ["==", ["get", "class"], "path"],
                "paint": {
                    "line-color": "hsl(0, 0%, 95%)",
                    "line-width": ["interpolate", ["linear"], ["zoom"], 12, 1, 18, 4],
                    "line-dasharray": ["step", ["zoom"], ["literal", [4, 0.3]], 15, ["literal", [1, 0.3]]],
                },
            },
            {
                "id": "road-pedestrian",
                "type": "line",
                "minzoom": 16,
                "paint": {
                    "line-color": "hsl(0, 0%, 95%)",
                    "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.5, 18, 12],
                },
            },
            {
                "id": "road-pedestrian-polygon-pattern",
                "type": "fill",
                "minzoom": 16,
                "paint": {
                    "fill-pattern": "pedestrian-polygon",
                    "fill-opacity": ["interpolate", ["linear"], ["zoom"], 16, 0, 17, 1],
                },
            },
            {
                "id": "path-pedestrian-label",
                "type": "symbol",
                "minzoom": 12,
                "filter": ["==", ["get", "class"], "pedestrian"],
            },
        ],
    }


def _qgis_thinning_settings():
    return {
        "limit_number_of_labels_enabled": False,
        "maximum_number_labels": 0,
        "minimum_feature_size": 1.25,
        "allow_duplicate_removal": True,
        "minimum_distance_to_duplicate": 20,
        "minimum_distance_to_duplicate_unit": "Millimeters",
        "label_margin_distance": 1.5,
        "label_margin_distance_unit": "Millimeters",
    }


def _qgis_label_styles():
    return [
        {
            "style_name": "road-label-z12-to-z15",
            "layer_name": "road",
            "geometry_type": 1,
            "filter_expression": '"name" IS NOT NULL',
            "min_zoom_level": 12,
            "max_zoom_level": 14,
            "label_settings": {
                "field_name": '"name"',
                "placement": 3,
                "priority": 4,
                "repeat_distance": 39.6875,
                "repeat_distance_unit": 0,
                "label_per_part": False,
                "merge_lines": True,
                "text_size": 2.6458333333333335,
                "text_color": "#606060",
                "buffer_enabled": True,
                "buffer_size": 0.5291666666666667,
                "buffer_color": "#ffffff",
                "thinning_settings": _qgis_thinning_settings(),
            },
        },
        {
            "style_name": "road-label-z15-plus",
            "layer_name": "road",
            "geometry_type": 1,
            "filter_expression": '"name" IS NOT NULL',
            "min_zoom_level": 15,
            "max_zoom_level": -1,
            "label_settings": {
                "field_name": '"name"',
                "placement": 3,
                "priority": 4,
                "repeat_distance": 105.83333333333333,
                "repeat_distance_unit": 0,
                "label_per_part": False,
                "merge_lines": True,
                "text_size": 2.6458333333333335,
                "text_color": "#606060",
                "buffer_enabled": True,
                "buffer_size": 0.5291666666666667,
                "buffer_color": "#ffffff",
            },
        },
        {
            "style_name": "path-pedestrian-label",
            "layer_name": "road",
            "geometry_type": 1,
            "filter_expression": '"class" = \'pedestrian\'',
            "min_zoom_level": 12,
            "max_zoom_level": -1,
            "label_settings": {
                "field_name": '"name"',
                "placement": 3,
                "priority": 3,
                "repeat_distance": 105.83333333333333,
                "repeat_distance_unit": 0,
                "label_per_part": False,
                "merge_lines": True,
                "text_size": 2.38125,
                "text_color": "#000000",
                "buffer_enabled": True,
                "buffer_size": 0.5291666666666667,
                "buffer_color": "#ffffff",
                "thinning_settings": _qgis_thinning_settings(),
            },
        },
        {
            "style_name": "poi-label",
            "layer_name": "poi_label",
            "geometry_type": 0,
            "filter_expression": "",
            "min_zoom_level": 14,
            "max_zoom_level": -1,
            "label_settings": {"field_name": '"name"'},
        },
    ]


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

    def test_load_json_list_requires_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "report.json"
            json_path.write_text('["ok"]\n', encoding="utf-8")
            self.assertEqual(load_json_list(json_path), ["ok"])

            json_path.write_text('{"not": "a list"}\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json_list(json_path)

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

    def test_qgis_label_style_paths_from_comparison_summary_reads_manifest_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            comparison_root = root / "comparison"
            run_dir = comparison_root / "camera" / "20260520T003504Z"
            run_dir.mkdir(parents=True)
            label_path = run_dir / "qgis-label-styles.json"
            manifest_path = run_dir / "manifest.json"
            label_path.write_text(json.dumps(_qgis_label_styles()), encoding="utf-8")
            manifest_path.write_text(
                json.dumps({"outputs": {"qgis_label_styles": str(label_path)}}),
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

            paths = qgis_label_style_paths_from_comparison_summary(
                comparison_summary,
                summary_path=comparison_root / "all-cameras" / "summary.json",
            )

            self.assertEqual(paths, {"chamonix-trails-z14-outdoors": label_path.resolve()})

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

    def test_comparison_visual_artifacts_from_summary_reads_metrics_and_output_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            comparison_root = Path(tmpdir) / "comparison"
            summary_path = comparison_root / "all-cameras" / "20260520T003504Z" / "summary.json"
            browser_path = comparison_root / "chamonix-trails-z14-outdoors" / "run" / "mapbox-gl-reference.png"
            qgis_path = comparison_root / "chamonix-trails-z14-outdoors" / "run" / "qgis-vector-render.png"
            diff_path = comparison_root / "chamonix-trails-z14-outdoors" / "run" / "mapbox-gl-vs-qgis-diff.png"
            contact_sheet_path = summary_path.parent / "contact-sheet.jpg"
            comparison_summary = {
                "contact_sheet": "contact-sheet.jpg",
                "cameras": [
                    {
                        "camera": "chamonix-trails-z14-outdoors",
                        "status": "passed",
                        "artifact_status": "metrics_available",
                        "metrics": {
                            "changed_pixel_ratio": 0.982,
                            "normalized_mean_absolute_channel_delta": 0.0352,
                            "normalized_rms_channel_delta": 0.0761,
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

            artifacts = comparison_visual_artifacts_from_summary(
                comparison_summary,
                summary_path=summary_path,
            )

            camera_artifacts = artifacts["chamonix-trails-z14-outdoors"]
            self.assertEqual(camera_artifacts["browser_reference"], browser_path.resolve())
            self.assertEqual(camera_artifacts["qgis_vector_render"], qgis_path.resolve())
            self.assertEqual(camera_artifacts["diff"], diff_path.resolve())
            self.assertEqual(camera_artifacts["contact_sheet"], contact_sheet_path.resolve())
            self.assertEqual(camera_artifacts["changed_pixel_ratio"], 0.982)
            self.assertEqual(camera_artifacts["ssim_status"], "unavailable")

    def test_qgis_path_pedestrian_style_summary_counts_current_style_controls(self):
        summary = qgis_path_pedestrian_style_summary(_qgis_preprocessed_style(), camera_zoom=14.25)

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
        self.assertEqual(summary["qgis_path_pedestrian_visible_layer_count"], 3)
        self.assertEqual(summary["qgis_path_pedestrian_visible_line_layer_count"], 2)
        self.assertEqual(summary["qgis_path_pedestrian_visible_fill_layer_count"], 1)
        self.assertEqual(summary["qgis_path_pedestrian_visible_filter_layer_count"], 2)
        self.assertEqual(summary["qgis_path_pedestrian_visible_line_width_layer_count"], 1)
        self.assertEqual(summary["qgis_path_pedestrian_visible_line_color_layer_count"], 2)
        self.assertEqual(summary["qgis_path_pedestrian_visible_fill_color_layer_count"], 1)
        self.assertEqual(summary["qgis_path_pedestrian_visible_line_dasharray_layer_count"], 2)
        self.assertEqual(summary["qgis_path_pedestrian_visible_line_opacity_layer_count"], 0)
        self.assertEqual(
            summary["qgis_path_pedestrian_layer_ids"],
            ["road-path", "road-pedestrian", "road-steps", "road-pedestrian-polygon"],
        )
        self.assertEqual(
            summary["qgis_path_pedestrian_visible_layer_ids"],
            ["road-path", "road-steps", "road-pedestrian-polygon"],
        )
        self.assertEqual(summary["qgis_path_pedestrian_label_source_layer_count"], 3)
        self.assertEqual(summary["qgis_path_pedestrian_visible_label_source_layer_count"], 2)
        label_source_details = {
            detail["id"]: detail
            for detail in summary["qgis_path_pedestrian_label_source_details"]
        }
        self.assertEqual(
            label_source_details["path-pedestrian-label"]["duplicate_label_categories"],
            ["pedestrian"],
        )
        self.assertEqual(
            label_source_details["road-label-z15-plus"]["duplicate_label_categories"],
            [],
        )
        visible_label_source_ids = [
            detail["id"] for detail in summary["qgis_path_pedestrian_visible_label_source_details"]
        ]
        self.assertEqual(visible_label_source_ids, ["road-label-z12-to-z15", "path-pedestrian-label"])
        details_by_id = {detail["id"]: detail for detail in summary["qgis_path_pedestrian_layer_details"]}
        self.assertEqual(details_by_id["road-path"]["type"], "line")
        self.assertEqual(details_by_id["road-path"]["minzoom"], 12)
        self.assertEqual(details_by_id["road-path"]["maxzoom"], 16)
        self.assertEqual(details_by_id["road-path"]["filter"], ["==", ["get", "class"], "path"])
        self.assertEqual(details_by_id["road-path"]["line-width"], 1.5)
        self.assertEqual(details_by_id["road-path"]["line-color"], "#d8c6a3")
        self.assertEqual(details_by_id["road-path"]["line-dasharray"], [1, 1])
        self.assertEqual(details_by_id["road-pedestrian-polygon"]["fill-color"], "#f6f2e8")
        self.assertEqual(details_by_id["road-pedestrian-polygon"]["fill-opacity"], 0.4)
        visible_detail_ids = [
            detail["id"] for detail in summary["qgis_path_pedestrian_visible_layer_details"]
        ]
        self.assertEqual(visible_detail_ids, ["road-path", "road-steps", "road-pedestrian-polygon"])
        visible_details_by_id = {
            detail["id"]: detail for detail in summary["qgis_path_pedestrian_visible_layer_details"]
        }
        self.assertEqual(visible_details_by_id["road-path"]["type"], "line")
        self.assertEqual(visible_details_by_id["road-path"]["line-width"], 1.5)
        self.assertEqual(visible_details_by_id["road-pedestrian-polygon"]["type"], "fill")
        self.assertEqual(visible_details_by_id["road-pedestrian-polygon"]["fill-opacity"], 0.4)
        self.assertIn("road-path=1.5", summary["qgis_path_pedestrian_line_width_samples"])
        self.assertIn('road-path="#d8c6a3"', summary["qgis_path_pedestrian_line_color_samples"])
        self.assertIn('road-pedestrian-polygon="#f6f2e8"', summary["qgis_path_pedestrian_fill_color_samples"])
        self.assertIn("road-path=1.5", summary["qgis_path_pedestrian_visible_line_width_samples"])
        self.assertNotIn(
            'road-pedestrian="#f6f2e8"',
            summary["qgis_path_pedestrian_visible_line_color_samples"],
        )
        self.assertIn(
            'road-pedestrian-polygon="#f6f2e8"',
            summary["qgis_path_pedestrian_visible_fill_color_samples"],
        )

    def test_qgis_style_summary_treats_all_layers_as_visible_without_camera_zoom(self):
        summary = qgis_path_pedestrian_style_summary(_qgis_preprocessed_style())

        self.assertEqual(
            summary["qgis_path_pedestrian_visible_layer_count"],
            summary["qgis_path_pedestrian_layer_count"],
        )
        self.assertEqual(
            summary["qgis_path_pedestrian_visible_line_layer_count"],
            summary["qgis_path_pedestrian_line_layer_count"],
        )
        self.assertEqual(
            summary["qgis_path_pedestrian_visible_filter_layer_count"],
            summary["qgis_path_pedestrian_filter_layer_count"],
        )
        self.assertEqual(
            summary["qgis_path_pedestrian_visible_line_width_layer_count"],
            summary["qgis_path_pedestrian_line_width_layer_count"],
        )
        self.assertEqual(
            summary["qgis_path_pedestrian_visible_line_dasharray_layer_count"],
            summary["qgis_path_pedestrian_line_dasharray_layer_count"],
        )

    def test_qgis_style_summary_evaluates_zoom_step_and_comparison_label_filters(self):
        style = {
            "version": 8,
            "layers": [
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": [
                        "step",
                        ["zoom"],
                        ["match", ["get", "class"], ["pedestrian"], True, False],
                        12,
                        ["match", ["get", "class"], ["path"], True, False],
                        15,
                        ["match", ["get", "class"], ["path", "pedestrian"], False, True],
                    ],
                },
                {
                    "id": "path-pedestrian-label",
                    "type": "symbol",
                    "filter": [
                        "all",
                        [">", ["get", "layer"], -1],
                        ["<=", ["get", "layer"], 0],
                        ["<", ["get", "layer"], 1],
                        ["match", ["get", "class"], ["pedestrian"], True, False],
                    ],
                },
            ],
        }

        low_zoom_summary = qgis_path_pedestrian_style_summary(style, camera_zoom=11.5)
        mid_zoom_summary = qgis_path_pedestrian_style_summary(style, camera_zoom=12.5)
        high_zoom_summary = qgis_path_pedestrian_style_summary(style, camera_zoom=15.5)

        low_zoom_sources = {
            detail["id"]: detail
            for detail in low_zoom_summary["qgis_path_pedestrian_visible_label_source_details"]
        }
        mid_zoom_sources = {
            detail["id"]: detail
            for detail in mid_zoom_summary["qgis_path_pedestrian_visible_label_source_details"]
        }
        high_zoom_sources = {
            detail["id"]: detail
            for detail in high_zoom_summary["qgis_path_pedestrian_visible_label_source_details"]
        }
        self.assertEqual(low_zoom_sources["road-label"]["duplicate_label_categories"], ["pedestrian"])
        self.assertEqual(mid_zoom_sources["road-label"]["duplicate_label_categories"], ["path", "step"])
        self.assertEqual(high_zoom_sources["road-label"]["duplicate_label_categories"], [])
        self.assertEqual(
            high_zoom_sources["path-pedestrian-label"]["duplicate_label_categories"],
            ["pedestrian"],
        )

    def test_qgis_style_summary_tracks_split_path_pedestrian_label_source_variants(self):
        style = {
            "version": 8,
            "layers": [
                {
                    "id": "path-pedestrian-label-below-z15",
                    "type": "symbol",
                    "minzoom": 12,
                    "maxzoom": 15,
                    "filter": ["==", ["get", "class"], "pedestrian"],
                },
                {
                    "id": "path-pedestrian-label-z15-plus",
                    "type": "symbol",
                    "minzoom": 15,
                    "filter": ["match", ["get", "class"], ["path", "pedestrian"], True, False],
                },
            ],
        }

        mid_zoom_summary = qgis_path_pedestrian_style_summary(style, camera_zoom=14.25)
        high_zoom_summary = qgis_path_pedestrian_style_summary(style, camera_zoom=18.0)

        mid_zoom_sources = {
            detail["id"]: detail
            for detail in mid_zoom_summary["qgis_path_pedestrian_visible_label_source_details"]
        }
        high_zoom_sources = {
            detail["id"]: detail
            for detail in high_zoom_summary["qgis_path_pedestrian_visible_label_source_details"]
        }
        self.assertEqual(
            mid_zoom_sources["path-pedestrian-label-below-z15"]["duplicate_label_categories"],
            ["pedestrian"],
        )
        self.assertEqual(
            high_zoom_sources["path-pedestrian-label-z15-plus"]["duplicate_label_categories"],
            ["pedestrian", "path", "step"],
        )

    def test_qgis_style_summary_keeps_full_layer_id_lists(self):
        style = {
            "version": 8,
            "layers": [
                {
                    "id": f"road-path-test-{index}",
                    "type": "line",
                    "paint": {"line-width": index + 1},
                }
                for index in range(10)
            ],
        }

        summary = qgis_path_pedestrian_style_summary(style)

        expected_ids = [f"road-path-test-{index}" for index in range(10)]
        self.assertEqual(summary["qgis_path_pedestrian_layer_ids"], expected_ids)
        self.assertEqual(summary["qgis_path_pedestrian_visible_layer_ids"], expected_ids)
        self.assertEqual(
            [detail["id"] for detail in summary["qgis_path_pedestrian_layer_details"]],
            expected_ids,
        )
        self.assertEqual(
            [detail["line-width"] for detail in summary["qgis_path_pedestrian_layer_details"]],
            list(range(1, 11)),
        )

    def test_qgis_path_pedestrian_label_summary_counts_visible_road_and_path_labels(self):
        summary = qgis_path_pedestrian_label_summary(_qgis_label_styles(), camera_zoom=18.0)

        self.assertEqual(summary["qgis_label_style_status"], "available")
        self.assertEqual(summary["qgis_path_pedestrian_label_style_count"], 3)
        self.assertEqual(summary["qgis_path_pedestrian_visible_label_style_count"], 2)
        self.assertEqual(
            summary["qgis_path_pedestrian_label_style_names"],
            ["road-label-z12-to-z15", "road-label-z15-plus", "path-pedestrian-label"],
        )
        self.assertEqual(
            summary["qgis_path_pedestrian_visible_label_style_names"],
            ["road-label-z15-plus", "path-pedestrian-label"],
        )
        details = {
            detail["style_name"]: detail for detail in summary["qgis_path_pedestrian_visible_label_details"]
        }
        self.assertEqual(details["road-label-z15-plus"]["repeat_distance"], 105.83333333333333)
        self.assertTrue(details["road-label-z15-plus"]["merge_lines"])
        self.assertEqual(details["path-pedestrian-label"]["text_size"], 2.38125)
        self.assertEqual(
            details["path-pedestrian-label"]["thinning_settings"],
            _qgis_thinning_settings(),
        )

    def test_build_path_pedestrian_focus_report_cross_links_feature_counts_and_qgis_style(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            source_style=_source_style(),
            qgis_styles_by_camera={"chamonix-trails-z14-outdoors": _qgis_preprocessed_style()},
            qgis_label_styles_by_camera={"chamonix-trails-z14-outdoors": _qgis_label_styles()},
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        self.assertEqual(report["generated"], "2026-05-20T01:35:00+00:00")
        self.assertEqual(report["camera_count"], 1)
        self.assertEqual(report["source_style_camera_count"], 1)
        self.assertEqual(report["source_style_input_count"], 1)
        self.assertEqual(report["qgis_style_camera_count"], 1)
        self.assertEqual(report["qgis_style_input_count"], 1)
        self.assertEqual(report["qgis_label_style_camera_count"], 1)
        self.assertEqual(report["qgis_label_style_input_count"], 1)
        [camera] = report["cameras"]
        self.assertEqual(camera["camera"], "chamonix-trails-z14-outdoors")
        self.assertEqual(camera["pedestrian_path_polygon_count"], 10)
        self.assertEqual(camera["pedestrian_line_count"], 115)
        self.assertEqual(camera["path_line_count"], 236)
        self.assertEqual(camera["step_line_count"], 19)
        self.assertEqual(camera["top_path_line_types"], ["trail=69", "footway=56", "piste=48"])
        self.assertEqual(camera["top_step_structures"], ["none=16", "tunnel=2", "bridge=1"])
        self.assertEqual(camera["top_pedestrian_line_duplicate_names"], ["Englischer Viertel=5"])
        self.assertEqual(camera["top_path_line_duplicate_names"], ["Hofmattweg=3"])
        self.assertEqual(camera["top_step_line_duplicate_names"], ["Kirchsteig=2"])
        self.assertEqual(camera["source_style_status"], "available")
        self.assertEqual(camera["source_path_pedestrian_layer_count"], 3)
        self.assertEqual(camera["source_path_pedestrian_visible_layer_count"], 1)
        self.assertEqual(
            camera["source_path_pedestrian_visible_layer_ids"],
            ["road-path"],
        )
        self.assertEqual(camera["qgis_path_pedestrian_layer_count"], 4)
        self.assertEqual(camera["qgis_path_pedestrian_visible_layer_count"], 3)
        self.assertEqual(camera["qgis_path_pedestrian_label_style_count"], 3)
        self.assertEqual(camera["qgis_path_pedestrian_visible_label_style_count"], 2)
        self.assertEqual(
            camera["duplicate_label_diagnostic"]["duplicate_name_categories"],
            [
                {"category": "pedestrian", "top_duplicates": ["Englischer Viertel=5"]},
                {"category": "path", "top_duplicates": ["Hofmattweg=3"]},
                {"category": "step", "top_duplicates": ["Kirchsteig=2"]},
            ],
        )
        self.assertEqual(
            camera["duplicate_label_diagnostic"]["visible_merge_line_label_styles"],
            ["road-label-z12-to-z15", "path-pedestrian-label"],
        )
        self.assertEqual(
            camera["duplicate_label_diagnostic"]["visible_label_repeat_distances"],
            [
                "road-label-z12-to-z15=39.6875",
                "path-pedestrian-label=105.83333333333333",
            ],
        )
        self.assertEqual(
            camera["duplicate_label_diagnostic"]["visible_label_source_category_matches"],
            ["path-pedestrian-label: pedestrian"],
        )
        self.assertEqual(
            camera["duplicate_label_diagnostic"]["unmatched_duplicate_name_categories"],
            ["path", "step"],
        )
        self.assertEqual(
            camera["source_qgis_stroke_control_comparisons"],
            [
                {
                    "source_layer_id": "road-path",
                    "source_controls": {
                        "line-color": "hsl(0, 0%, 95%)",
                        "line-width": ["interpolate", ["linear"], ["zoom"], 12, 1, 18, 4],
                        "line-dasharray": [
                            "step",
                            ["zoom"],
                            ["literal", [4, 0.3]],
                            15,
                            ["literal", [1, 0.3]],
                        ],
                    },
                    "source_sampled_controls": {
                        "line-width": 0.5622395833333333,
                        "line-color": "hsl(0, 0%, 95%)",
                        "line-dasharray": [4, 0.3],
                    },
                    "qgis_layer_ids": ["road-path"],
                    "qgis_controls": [
                        {
                            "layer_id": "road-path",
                            "controls": {
                                "line-color": "#d8c6a3",
                                "line-width": 1.5,
                                "line-dasharray": [1, 1],
                            },
                        }
                    ],
                    "qgis_control_deltas": [
                        {
                            "layer_id": "road-path",
                            "deltas": {
                                "line-width_delta_mm": 0.9377604166666667,
                                "line-width_ratio": 2.667901806391848,
                                "line-dasharray_match": False,
                                "line-color_match": False,
                            },
                        }
                    ],
                }
            ],
        )

    def test_build_path_pedestrian_focus_report_pairs_qgis_variant_strokes_with_source_ids(self):
        road_report = {
            "generated": "2026-05-20T01:09:13+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "cameras": [
                {
                    "status": "decoded",
                    "camera": "zermatt-trails-z18-outdoors",
                    "camera_zoom": 18.0,
                    "tile_zoom": 18,
                    "pedestrian_line_candidate_count": 1,
                }
            ],
        }
        source_style = {
            "version": 8,
            "layers": [
                {
                    "id": "road-pedestrian",
                    "type": "line",
                    "minzoom": 16,
                    "paint": {
                        "line-color": "hsl(0, 0%, 95%)",
                        "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.5, 18, 12],
                        "line-dasharray": ["literal", [1, 0.2]],
                    },
                }
            ],
        }
        qgis_style = {
            "version": 8,
            "layers": [
                {
                    "id": "road-pedestrian-z18-plus",
                    "type": "line",
                    "minzoom": 18,
                    "paint": {
                        "line-color": "#f6f2e8",
                        "line-width": 2.328099173553719,
                        "line-dasharray": [1, 0.2],
                        "line-opacity": 0.65,
                    },
                }
            ],
        }

        report = build_path_pedestrian_focus_report(
            road_report,
            source_style=source_style,
            qgis_styles_by_camera={"zermatt-trails-z18-outdoors": qgis_style},
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        [camera] = report["cameras"]
        self.assertEqual(
            camera["source_qgis_stroke_control_comparisons"],
            [
                {
                    "source_layer_id": "road-pedestrian",
                    "source_controls": {
                        "line-color": "hsl(0, 0%, 95%)",
                        "line-width": ["interpolate", ["linear"], ["zoom"], 14, 0.5, 18, 12],
                        "line-dasharray": ["literal", [1, 0.2]],
                    },
                    "source_sampled_controls": {
                        "line-width": 3.0,
                        "line-width_raw_mm": 3.175,
                        "line-width_capped": True,
                        "line-color": "hsl(0, 0%, 95%)",
                        "line-dasharray": [1, 0.2],
                    },
                    "qgis_layer_ids": ["road-pedestrian-z18-plus"],
                    "qgis_controls": [
                        {
                            "layer_id": "road-pedestrian-z18-plus",
                            "controls": {
                                "line-width": 2.328099173553719,
                                "line-color": "#f6f2e8",
                                "line-dasharray": [1, 0.2],
                                "line-opacity": 0.65,
                            },
                        }
                    ],
                    "qgis_control_deltas": [
                        {
                            "layer_id": "road-pedestrian-z18-plus",
                            "deltas": {
                                "line-width_delta_mm": -0.6719008264462811,
                                "line-width_ratio": 0.7760330578512397,
                                "line-dasharray_match": True,
                                "line-color_match": False,
                            },
                        }
                    ],
                }
            ],
        )
        markdown = build_summary_markdown(report)
        self.assertIn("line-width_raw_mm=3.175", markdown)
        self.assertIn("line-width_capped=true", markdown)

    def test_build_path_pedestrian_focus_report_pairs_split_road_path_strokes_with_source_layer(self):
        road_report = _road_feature_report()
        road_report["cameras"][0]["camera_zoom"] = 18.0
        qgis_style = {
            "version": 8,
            "layers": [
                {
                    "id": "road-path-z16-plus",
                    "type": "line",
                    "minzoom": 16,
                    "paint": {
                        "line-color": "hsl(0, 0%, 95%)",
                        "line-width": 1.0583333333333333,
                        "line-dasharray": [1, 0.3],
                    },
                }
            ],
        }

        report = build_path_pedestrian_focus_report(
            road_report,
            source_style=_source_style(),
            qgis_styles_by_camera={"chamonix-trails-z14-outdoors": qgis_style},
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        [camera] = report["cameras"]
        comparisons = {
            comparison["source_layer_id"]: comparison
            for comparison in camera["source_qgis_stroke_control_comparisons"]
        }
        self.assertEqual(comparisons["road-path"]["qgis_layer_ids"], ["road-path-z16-plus"])
        self.assertEqual(
            comparisons["road-path"]["source_sampled_controls"],
            {
                "line-width": 1.0583333333333333,
                "line-color": "hsl(0, 0%, 95%)",
                "line-dasharray": [1, 0.3],
            },
        )
        self.assertEqual(
            comparisons["road-path"]["qgis_controls"],
            [
                {
                    "layer_id": "road-path-z16-plus",
                    "controls": {
                        "line-width": 1.0583333333333333,
                        "line-color": "hsl(0, 0%, 95%)",
                        "line-dasharray": [1, 0.3],
                    },
                }
            ],
        )
        self.assertEqual(
            comparisons["road-path"]["qgis_control_deltas"],
            [
                {
                    "layer_id": "road-path-z16-plus",
                    "deltas": {
                        "line-width_delta_mm": 0.0,
                        "line-width_ratio": 1.0,
                        "line-dasharray_match": True,
                        "line-color_match": True,
                    },
                }
            ],
        )

    def test_build_path_pedestrian_focus_report_marks_unsampled_source_color_expressions(self):
        source_style = {
            "version": 8,
            "layers": [
                {
                    "id": "road-path",
                    "type": "line",
                    "minzoom": 12,
                    "paint": {
                        "line-color": ["match", ["get", "type"], "piste", "#2365d1", "#f2f2f2"],
                        "line-width": 2,
                    },
                }
            ],
        }

        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            source_style=source_style,
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        [camera] = report["cameras"]
        [comparison] = camera["source_qgis_stroke_control_comparisons"]
        self.assertEqual(
            comparison["source_sampled_controls"],
            {
                "line-width": 0.5291666666666667,
                "line-color": "expression-not-sampled",
            },
        )

    def test_build_path_pedestrian_focus_report_does_not_mark_minimum_width_floor_as_cap(self):
        source_style = {
            "version": 8,
            "layers": [
                {
                    "id": "road-path",
                    "type": "line",
                    "minzoom": 12,
                    "paint": {"line-width": 0.1},
                }
            ],
        }

        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            source_style=source_style,
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        [camera] = report["cameras"]
        [comparison] = camera["source_qgis_stroke_control_comparisons"]
        self.assertEqual(comparison["source_sampled_controls"], {"line-width": 0.1})

    def test_build_path_pedestrian_focus_report_summarizes_pedestrian_cap_relationship(self):
        road_report = {
            "generated": "2026-05-20T01:09:13+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "cameras": [
                {
                    "status": "decoded",
                    "camera": "zermatt-trails-z18-outdoors",
                    "camera_zoom": 18.0,
                    "tile_zoom": 18,
                    "pedestrian_line_candidate_count": 1,
                }
            ],
        }
        source_style = {
            "version": 8,
            "layers": [
                {
                    "id": "road-pedestrian",
                    "type": "line",
                    "minzoom": 12,
                    "paint": {"line-width": 12},
                },
                {
                    "id": "road-pedestrian-case",
                    "type": "line",
                    "minzoom": 14,
                    "paint": {"line-width": 14.5},
                },
            ],
        }
        qgis_style = {
            "version": 8,
            "layers": [
                {
                    "id": "road-pedestrian-case-z18-plus-pale-casing",
                    "type": "line",
                    "minzoom": 18,
                    "paint": {"line-width": 3.0},
                },
                {
                    "id": "road-pedestrian-case-z18-plus",
                    "type": "line",
                    "minzoom": 18,
                    "paint": {"line-width": 2.4},
                },
                {
                    "id": "road-pedestrian-z18-plus",
                    "type": "line",
                    "minzoom": 18,
                    "paint": {"line-width": 1.92},
                },
            ],
        }

        report = build_path_pedestrian_focus_report(
            road_report,
            source_style=source_style,
            qgis_styles_by_camera={"zermatt-trails-z18-outdoors": qgis_style},
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        [camera] = report["cameras"]
        self.assertEqual(
            camera["pedestrian_core_case_cap_relationships"],
            [
                {
                    "source_core_layer_id": "road-pedestrian",
                    "source_case_layer_id": "road-pedestrian-case",
                    "cap_limit_mm": 3.0,
                    "source_core_width_mm": 3.0,
                    "source_core_raw_width_mm": 3.175,
                    "source_core_capped": True,
                    "source_case_width_mm": 3.0,
                    "source_case_raw_width_mm": 3.8364583333333333,
                    "source_case_capped": True,
                    "source_both_widths_capped": True,
                    "source_case_over_core_mm": 0.0,
                    "source_case_to_core_ratio": 1.0,
                    "qgis_core_layer_id": "road-pedestrian-z18-plus",
                    "qgis_core_width_mm": 1.92,
                    "qgis_case_layer_id": "road-pedestrian-case-z18-plus",
                    "qgis_case_width_mm": 2.4,
                    "qgis_pale_casing_layer_id": "road-pedestrian-case-z18-plus-pale-casing",
                    "qgis_pale_casing_width_mm": 3.0,
                    "qgis_case_over_core_mm": 0.48,
                    "qgis_case_to_core_ratio": 1.25,
                    "qgis_ratio_preserving_core_width_mm_at_cap": 2.4,
                }
            ],
        )
        markdown = build_summary_markdown(report)
        self.assertIn("## Pedestrian core/case cap relationships", markdown)
        self.assertIn("source_both_widths_capped=true", markdown)
        self.assertIn("qgis_ratio_preserving_core_width_mm_at_cap=2.4", markdown)

    def test_build_path_pedestrian_focus_report_adds_visual_artifacts_to_focused_cameras(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            qgis_styles_by_camera={"chamonix-trails-z14-outdoors": _qgis_preprocessed_style()},
            visual_artifacts_by_camera={
                "chamonix-trails-z14-outdoors": {
                    "changed_pixel_ratio": 0.982,
                    "browser_reference": Path("debug/comparison/chamonix/mapbox-gl-reference.png"),
                },
                "switzerland-alps-z5-outdoors": {
                    "changed_pixel_ratio": 0.943,
                    "browser_reference": Path("debug/comparison/switzerland/mapbox-gl-reference.png"),
                },
            },
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        [camera] = report["cameras"]
        self.assertEqual(camera["camera"], "chamonix-trails-z14-outdoors")
        self.assertEqual(camera["visual_artifacts"]["changed_pixel_ratio"], 0.982)
        self.assertEqual(
            camera["visual_artifacts"]["browser_reference"],
            "debug/comparison/chamonix/mapbox-gl-reference.png",
        )

    def test_build_path_pedestrian_focus_report_marks_missing_qgis_style(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        [camera] = report["cameras"]
        self.assertEqual(report["source_style_camera_count"], 0)
        self.assertEqual(report["source_style_input_count"], 0)
        self.assertEqual(camera["source_style_status"], "missing")
        self.assertEqual(camera["source_path_pedestrian_layer_count"], 0)
        self.assertEqual(camera["source_path_pedestrian_visible_layer_count"], 0)
        self.assertEqual(camera["qgis_style_status"], "missing")
        self.assertEqual(camera["qgis_label_style_status"], "missing")
        self.assertEqual(camera["qgis_path_pedestrian_layer_count"], 0)
        self.assertEqual(camera["qgis_path_pedestrian_visible_layer_count"], 0)
        self.assertEqual(camera["qgis_path_pedestrian_visible_filter_layer_count"], 0)
        self.assertEqual(camera["qgis_path_pedestrian_layer_details"], [])
        self.assertEqual(camera["qgis_path_pedestrian_visible_layer_details"], [])
        self.assertEqual(camera["qgis_path_pedestrian_label_details"], [])
        self.assertEqual(camera["qgis_path_pedestrian_visible_label_details"], [])
        self.assertEqual(camera["source_qgis_stroke_control_comparisons"], [])
        self.assertIn("Source style cameras: 0/0 matched", build_summary_markdown(report))

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
            source_style=_source_style(),
            qgis_styles_by_camera={"chamonix-trails-z14-outdoors": _qgis_preprocessed_style()},
            qgis_label_styles_by_camera={"chamonix-trails-z14-outdoors": _qgis_label_styles()},
            visual_artifacts_by_camera={
                "chamonix-trails-z14-outdoors": {
                    "status": "passed",
                    "artifact_status": "metrics_available",
                    "changed_pixel_ratio": 0.982,
                    "normalized_mean_absolute_channel_delta": 0.0352,
                    "normalized_rms_channel_delta": 0.0761,
                    "browser_reference": "debug/comparison/chamonix/mapbox-gl-reference.png",
                    "qgis_vector_render": "debug/comparison/chamonix/qgis-vector-render.png",
                    "diff": "debug/comparison/chamonix/mapbox-gl-vs-qgis-diff.png",
                    "contact_sheet": "debug/comparison/all-cameras/contact-sheet.jpg",
                },
            },
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        markdown = build_summary_markdown(report)

        self.assertIn("# Mapbox Outdoors path/pedestrian focus", markdown)
        self.assertIn("Focused cameras: 1", markdown)
        self.assertIn("Source style cameras: 1/1 matched", markdown)
        self.assertIn("QGIS preprocessed style cameras: 1/1 matched", markdown)
        self.assertIn("QGIS label style cameras: 1/1 matched", markdown)
        self.assertIn("Top pedestrian types", markdown)
        self.assertIn("Duplicate pedestrian labels", markdown)
        self.assertIn("QGIS labels", markdown)
        self.assertIn("| chamonix-trails-z14-outdoors | 14.25 | 14 |", markdown)
        self.assertIn('"path_lines=236"', markdown)
        self.assertIn('"pedestrian=115"', markdown)
        self.assertIn('"trail=69"', markdown)
        self.assertIn('"Englischer Viertel=5"', markdown)
        self.assertIn('"Hofmattweg=3"', markdown)
        self.assertIn('"Kirchsteig=2"', markdown)
        self.assertIn('"status=available"', markdown)
        self.assertIn('"total=4"', markdown)
        self.assertIn('"visible=3"', markdown)
        self.assertIn('"total=3"', markdown)
        self.assertIn('"visible=2"', markdown)
        self.assertIn('"line_colors=3"', markdown)
        self.assertIn('"visible_filters=2"', markdown)
        self.assertIn('"visible_widths=1"', markdown)
        self.assertIn('"visible_dashes=2"', markdown)
        self.assertIn('"fill_colors=1"', markdown)
        self.assertIn('"road-path=1.5"', markdown)
        self.assertIn('"road-path=[1,1]"', markdown)
        self.assertIn('"road-pedestrian-polygon=\\"#f6f2e8\\""', markdown)
        self.assertIn('"road-pedestrian-polygon"', markdown)
        self.assertIn("## Visible source Mapbox layer details", markdown)
        self.assertIn("| road-path | line | z>=12 |", markdown)
        self.assertIn('"line-width=[\\"interpolate\\",[\\"linear\\"],[\\"zoom\\"],12,1,18,4]"', markdown)
        self.assertIn("## Source vs QGIS stroke controls", markdown)
        self.assertIn("Source sampled controls evaluate zoom expressions at the camera zoom", markdown)
        self.assertIn("expression colors are marked as expression-not-sampled", markdown)
        self.assertIn("QGIS deltas compare visible QGIS controls against source sampled controls", markdown)
        self.assertIn("| chamonix-trails-z14-outdoors | road-path |", markdown)
        self.assertIn('"line-width=0.5622395833333333"', markdown)
        self.assertIn('"line-dasharray=[4,0.3]"', markdown)
        self.assertIn('["road-path"]', markdown)
        self.assertIn('road-path: line-width=1.5', markdown)
        self.assertIn('line-dasharray=[1,1]', markdown)
        self.assertIn("road-path: line-width_delta_mm=0.9377604166666667", markdown)
        self.assertIn("line-dasharray_match=false", markdown)
        self.assertIn("## Visible QGIS layer details", markdown)
        self.assertIn("### chamonix-trails-z14-outdoors", markdown)
        self.assertIn("| road-path | line | 12<=z<16 |", markdown)
        self.assertIn('"line-width=1.5"', markdown)
        self.assertIn('"line-dasharray=[1,1]"', markdown)
        self.assertIn("| road-pedestrian-polygon | fill | z>=14 |", markdown)
        self.assertIn("## Visible QGIS label details", markdown)
        self.assertIn("| road-label-z12-to-z15 | road | 12<=z<=14 |", markdown)
        self.assertIn('"repeat_distance=105.83333333333333"', markdown)
        self.assertIn("| path-pedestrian-label | road | z>=12 |", markdown)
        self.assertIn("## Visible QGIS label thinning details", markdown)
        self.assertIn(
            (
                "| chamonix-trails-z14-outdoors | path-pedestrian-label | True | 20 | "
                "Millimeters | 1.5 | Millimeters | False | 0 | 1.25 |"
            ),
            markdown,
        )
        self.assertIn("## Duplicate label diagnostics", markdown)
        self.assertIn("Visible source-label category matches", markdown)
        self.assertIn('"pedestrian: Englischer Viertel=5"', markdown)
        self.assertIn('"path: Hofmattweg=3"', markdown)
        self.assertIn('"road-label-z12-to-z15"', markdown)
        self.assertIn('"path-pedestrian-label=105.83333333333333"', markdown)
        self.assertIn('"path-pedestrian-label: pedestrian"', markdown)
        self.assertIn('"path"', markdown)
        self.assertIn('"step"', markdown)
        self.assertIn("## Visual comparison artifacts", markdown)
        self.assertIn("| chamonix-trails-z14-outdoors | passed | metrics_available | 0.982 | 0.0352 | 0.0761 |", markdown)
        self.assertIn("`debug/comparison/chamonix/mapbox-gl-reference.png`", markdown)
        self.assertIn("`debug/comparison/chamonix/qgis-vector-render.png`", markdown)
        self.assertIn("`debug/comparison/chamonix/mapbox-gl-vs-qgis-diff.png`", markdown)
        self.assertIn("`debug/comparison/all-cameras/contact-sheet.jpg`", markdown)

    def test_build_summary_markdown_includes_visual_artifacts(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            visual_artifacts_by_camera={
                "chamonix-trails-z14-outdoors": {
                    "status": "passed",
                    "artifact_status": "metrics_available",
                    "changed_pixel_ratio": 0.982,
                    "normalized_mean_absolute_channel_delta": 0.0352,
                    "normalized_rms_channel_delta": 0.0761,
                    "browser_reference": "debug/comparison/chamonix/mapbox-gl-reference.png",
                    "qgis_vector_render": "debug/comparison/chamonix/qgis-vector-render.png",
                    "diff": "debug/comparison/chamonix/mapbox-gl-vs-qgis-diff.png",
                    "contact_sheet": "debug/comparison/all-cameras/contact-sheet.jpg",
                }
            },
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
        )

        markdown = build_summary_markdown(report)

        self.assertIn("## Visual comparison artifacts", markdown)
        self.assertIn("| chamonix-trails-z14-outdoors | passed | metrics_available | 0.982 |", markdown)
        self.assertIn("`debug/comparison/chamonix/mapbox-gl-reference.png`", markdown)
        self.assertIn("`debug/comparison/chamonix/qgis-vector-render.png`", markdown)
        self.assertIn("`debug/comparison/chamonix/mapbox-gl-vs-qgis-diff.png`", markdown)
        self.assertIn("`debug/comparison/all-cameras/contact-sheet.jpg`", markdown)

    def test_build_summary_markdown_omits_duplicate_label_rows_without_duplicate_names(self):
        markdown = build_summary_markdown(
            {
                "generated": "now",
                "cameras": [
                    {
                        "camera": "merge-line-only",
                        "duplicate_label_diagnostic": {
                            "has_duplicate_feature_names": False,
                            "duplicate_name_categories": [],
                            "visible_merge_line_label_styles": ["road-label-z15-plus"],
                            "visible_label_repeat_distances": [
                                "road-label-z15-plus=105.83333333333333"
                            ],
                        },
                    }
                ],
            }
        )

        self.assertIn("| merge-line-only |", markdown)
        self.assertNotIn("## Duplicate label diagnostics", markdown)

    def test_build_summary_markdown_marks_unmatched_source_stroke_rows(self):
        markdown = build_summary_markdown(
            {
                "generated": "now",
                "cameras": [
                    {
                        "camera": "unmatched-source-stroke",
                        "source_qgis_stroke_control_comparisons": [
                            {
                                "source_layer_id": "road-path",
                                "source_controls": {"line-width": 1.0},
                                "qgis_layer_ids": [],
                                "qgis_controls": [],
                                "qgis_control_deltas": [],
                            }
                        ],
                    }
                ],
            }
        )

        self.assertIn(
            "Rows with empty QGIS columns identify source strokes with no visible QGIS counterpart.",
            markdown,
        )
        self.assertIn(
            "| unmatched-source-stroke | road-path | [\"line-width=1.0\"] | [] | [] | [] | [] |",
            markdown,
        )

    def test_build_summary_markdown_handles_no_focus_rows(self):
        markdown = build_summary_markdown({"generated": "now", "cameras": []})

        self.assertIn("No decoded cameras include path/pedestrian focus features.", markdown)

    def test_build_summary_markdown_includes_input_artifacts(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            qgis_styles_by_camera={"chamonix-trails-z14-outdoors": _qgis_preprocessed_style()},
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
            input_artifacts={
                "road_features_json": "debug/roads/road-features.json",
                "source_style_json": "debug/source/mapbox-outdoors-v12.json",
                "comparison_summary_jsons": ["debug/comparison/summary.json"],
                "qgis_style_cameras": ["chamonix-trails-z14-outdoors"],
                "qgis_label_style_cameras": ["chamonix-trails-z14-outdoors"],
            },
        )

        markdown = build_summary_markdown(report)

        self.assertEqual(
            report["input_artifacts"]["road_features_json"],
            "debug/roads/road-features.json",
        )
        self.assertIn("Road features input: `debug/roads/road-features.json`", markdown)
        self.assertIn("Source style input: `debug/source/mapbox-outdoors-v12.json`", markdown)
        self.assertIn("Comparison summary inputs: `debug/comparison/summary.json`", markdown)
        self.assertIn("QGIS style input cameras: `chamonix-trails-z14-outdoors`", markdown)
        self.assertIn("QGIS label style input cameras: `chamonix-trails-z14-outdoors`", markdown)

    def test_build_report_serializes_path_input_artifacts(self):
        report = build_path_pedestrian_focus_report(
            _road_feature_report(),
            generated_at=dt.datetime(2026, 5, 20, 1, 35, tzinfo=dt.timezone.utc),
            input_artifacts={
                "road_features_json": Path("/tmp/road-features.json"),
                "comparison_summary_jsons": [Path("/tmp/comparison/summary.json")],
                "nested": {"style": Path("/tmp/qgis-preprocessed-style.json")},
            },
        )

        json.dumps(report)
        self.assertEqual(report["input_artifacts"]["road_features_json"], "/tmp/road-features.json")
        self.assertEqual(report["input_artifacts"]["comparison_summary_jsons"], ["/tmp/comparison/summary.json"])
        self.assertEqual(report["input_artifacts"]["nested"], {"style": "/tmp/qgis-preprocessed-style.json"})

    def test_build_summary_markdown_ignores_non_mapping_visible_details(self):
        markdown = build_summary_markdown(
            {
                "generated": "now",
                "cameras": [
                    {
                        "camera": "bad-details",
                        "qgis_path_pedestrian_visible_layer_details": ["not-a-detail"],
                    }
                ],
            }
        )

        self.assertIn("| bad-details |", markdown)
        self.assertNotIn("## Visible QGIS layer details", markdown)

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
            source_style_path = Path(tmpdir) / "source-style.json"
            style_path = Path(tmpdir) / "qgis-preprocessed-style.json"
            label_path = Path(tmpdir) / "qgis-label-styles.json"
            output_root = Path(tmpdir) / "out"
            road_features_path.write_text(json.dumps(_road_feature_report()), encoding="utf-8")
            source_style_path.write_text(json.dumps(_source_style()), encoding="utf-8")
            style_path.write_text(json.dumps(_qgis_preprocessed_style()), encoding="utf-8")
            label_path.write_text(json.dumps(_qgis_label_styles()), encoding="utf-8")

            stdout = io.StringIO()
            with patch(
                "qfit.validation.mapbox_outdoors_path_pedestrian_focus.DEFAULT_OUTPUT_ROOT",
                output_root,
            ), redirect_stdout(stdout):
                result = main(
                    [
                        "--road-features-json",
                        str(road_features_path),
                        "--source-style-json",
                        str(source_style_path),
                        "--qgis-style-json",
                        f"chamonix-trails-z14-outdoors={style_path}",
                        "--qgis-label-styles-json",
                        f"chamonix-trails-z14-outdoors={label_path}",
                    ]
                )

            self.assertEqual(result, 0)
            summary_path = Path(stdout.getvalue().strip())
            self.assertTrue(summary_path.exists())
            self.assertEqual(summary_path.parent.parent, output_root)
            report_path = summary_path.parent / "path-pedestrian-focus.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["camera_count"], 1)
            self.assertEqual(report["source_style_camera_count"], 1)
            self.assertEqual(
                report["input_artifacts"]["source_style_json"],
                str(source_style_path.resolve()),
            )
            self.assertEqual(
                report["input_artifacts"]["qgis_style_cameras"],
                ["chamonix-trails-z14-outdoors"],
            )
            self.assertEqual(
                report["input_artifacts"]["qgis_label_style_cameras"],
                ["chamonix-trails-z14-outdoors"],
            )
            self.assertEqual(report["qgis_label_style_camera_count"], 1)

    def test_main_records_resolved_external_relative_input_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            road_features_path = root / "road-features.json"
            output_root = root / "out"
            road_features_path.write_text(json.dumps(_road_feature_report()), encoding="utf-8")

            stdout = io.StringIO()
            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                with patch(
                    "qfit.validation.mapbox_outdoors_path_pedestrian_focus.DEFAULT_OUTPUT_ROOT",
                    output_root,
                ), redirect_stdout(stdout):
                    result = main(["--road-features-json", road_features_path.name])
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(result, 0)
            report_path = Path(stdout.getvalue().strip()).parent / "path-pedestrian-focus.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(
                report["input_artifacts"]["road_features_json"],
                str(road_features_path.resolve()),
            )
            self.assertIsNone(report["input_artifacts"]["source_style_json"])

    def test_main_loads_qgis_styles_from_comparison_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            road_features_path = root / "road-features.json"
            run_dir = root / "comparison" / "chamonix-trails-z14-outdoors" / "20260520T003504Z"
            run_dir.mkdir(parents=True)
            style_path = run_dir / "qgis-preprocessed-style.json"
            label_path = run_dir / "qgis-label-styles.json"
            manifest_path = run_dir / "manifest.json"
            browser_path = run_dir / "mapbox-gl-reference.png"
            qgis_path = run_dir / "qgis-vector-render.png"
            diff_path = run_dir / "mapbox-gl-vs-qgis-diff.png"
            comparison_summary_path = root / "comparison" / "all-cameras" / "summary.json"
            comparison_summary_path.parent.mkdir(parents=True)
            contact_sheet_path = comparison_summary_path.parent / "contact-sheet.jpg"
            road_features_path.write_text(json.dumps(_road_feature_report()), encoding="utf-8")
            style_path.write_text(json.dumps(_qgis_preprocessed_style()), encoding="utf-8")
            label_path.write_text(json.dumps(_qgis_label_styles()), encoding="utf-8")
            manifest_path.write_text(
                json.dumps(
                    {
                        "outputs": {
                            "qgis_preprocessed_style": str(style_path),
                            "qgis_label_styles": str(label_path),
                        }
                    }
                ),
                encoding="utf-8",
            )
            comparison_summary_path.write_text(
                json.dumps(
                    {
                        "contact_sheet": str(contact_sheet_path),
                        "cameras": [
                            {
                                "camera": "chamonix-trails-z14-outdoors",
                                "status": "passed",
                                "artifact_status": "metrics_available",
                                "manifest": str(manifest_path),
                                "metrics": {
                                    "changed_pixel_ratio": 0.982,
                                    "normalized_mean_absolute_channel_delta": 0.0352,
                                    "normalized_rms_channel_delta": 0.0761,
                                },
                                "outputs": {
                                    "browser_reference": str(browser_path),
                                    "qgis_vector_render": str(qgis_path),
                                    "diff": str(diff_path),
                                },
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
            ), patch(
                "qfit.validation.mapbox_outdoors_path_pedestrian_focus.load_json_object",
                wraps=load_json_object,
            ) as loaded_json, redirect_stdout(stdout):
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
            self.assertEqual(report["qgis_label_style_camera_count"], 1)
            self.assertEqual(report["qgis_label_style_input_count"], 1)
            self.assertEqual(
                report["input_artifacts"]["comparison_summary_jsons"],
                [str(comparison_summary_path)],
            )
            self.assertEqual(report["input_artifacts"]["qgis_style_cameras"], ["chamonix-trails-z14-outdoors"])
            self.assertEqual(
                report["input_artifacts"]["qgis_label_style_cameras"],
                ["chamonix-trails-z14-outdoors"],
            )
            [camera] = report["cameras"]
            self.assertEqual(camera["qgis_path_pedestrian_visible_label_style_count"], 2)
            self.assertEqual(camera["visual_artifacts"]["browser_reference"], str(browser_path.resolve()))
            self.assertEqual(camera["visual_artifacts"]["qgis_vector_render"], str(qgis_path.resolve()))
            self.assertEqual(camera["visual_artifacts"]["diff"], str(diff_path.resolve()))
            self.assertEqual(camera["visual_artifacts"]["contact_sheet"], str(contact_sheet_path.resolve()))
            self.assertEqual(camera["visual_artifacts"]["normalized_rms_channel_delta"], 0.0761)
            loaded_paths = [call.args[0] for call in loaded_json.call_args_list]
            self.assertEqual(loaded_paths.count(comparison_summary_path), 1)

    def test_main_reports_untrusted_visual_artifact_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            road_features_path = root / "road-features.json"
            comparison_summary_path = root / "comparison" / "all-cameras" / "summary.json"
            comparison_summary_path.parent.mkdir(parents=True)
            road_features_path.write_text(json.dumps(_road_feature_report()), encoding="utf-8")
            comparison_summary_path.write_text(
                json.dumps(
                    {
                        "cameras": [
                            {
                                "camera": "chamonix-trails-z14-outdoors",
                                "outputs": {"browser_reference": "/tmp/outside.png"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stderr = io.StringIO()

            with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "--road-features-json",
                        str(road_features_path),
                        "--comparison-summary-json",
                        str(comparison_summary_path),
                    ]
                )

            self.assertEqual(raised.exception.code, 2)
            self.assertIn("Comparison artifact path must stay under", stderr.getvalue())
            self.assertIn("/tmp/outside.png", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_main_reports_missing_input_without_traceback(self):
        stderr = io.StringIO()

        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["--road-features-json", "/missing/road-features.json"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("Road features JSON not found", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
