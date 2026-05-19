import datetime as dt
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import _path  # noqa: F401

from qfit.validation.mapbox_outdoors_contour_features import (
    ContourFeatureConfig,
    build_all_camera_contour_feature_paths,
    build_all_camera_run_directory,
    build_all_camera_summary_markdown,
    build_contour_feature_paths,
    build_run_directory,
    build_summary_markdown,
    collect_all_camera_contour_feature_report,
    collect_contour_feature_report,
    contour_tile_record,
    decode_vector_tile_bytes,
    is_contour_label_candidate,
    iter_tile_coordinates,
    lon_lat_to_tile,
    main,
    recommended_tile_zoom,
    resolve_mapbox_token,
    tile_bounds_for_web_mercator_extent,
    web_mercator_to_lon_lat,
    write_all_camera_report,
    write_report,
)


class MapboxOutdoorsContourFeatureTests(unittest.TestCase):
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

    def test_build_paths_are_predictable(self):
        run_dir = build_run_directory(
            output_root=Path("/tmp/contours"),
            camera_name="chamonix",
            now=dt.datetime(2026, 5, 18, 11, 22, tzinfo=dt.timezone.utc),
        )
        paths = build_contour_feature_paths(run_dir)

        self.assertEqual(run_dir, Path("/tmp/contours/chamonix/20260518T112200Z"))
        self.assertEqual(paths.json_path, run_dir / "contour-features.json")
        self.assertEqual(paths.summary_path, run_dir / "summary.md")

    def test_build_all_camera_paths_are_predictable(self):
        run_dir = build_all_camera_run_directory(
            output_root=Path("/tmp/contours"),
            now=dt.datetime(2026, 5, 18, 11, 22, tzinfo=dt.timezone.utc),
        )
        paths = build_all_camera_contour_feature_paths(run_dir)

        self.assertEqual(run_dir, Path("/tmp/contours/all-cameras/20260518T112200Z"))
        self.assertEqual(paths.json_path, run_dir / "summary.json")
        self.assertEqual(paths.summary_path, run_dir / "summary.md")

    def test_tile_helpers_cover_web_mercator_extent(self):
        self.assertEqual(recommended_tile_zoom(13.75), 14)
        self.assertEqual(lon_lat_to_tile(0.0, 0.0, 1), (1, 1))
        self.assertEqual(web_mercator_to_lon_lat(0.0, 0.0), (0.0, 0.0))

        bounds = tile_bounds_for_web_mercator_extent((-1000.0, -1000.0, 1000.0, 1000.0), 1)

        self.assertEqual(bounds, {"min_x": 0, "max_x": 1, "min_y": 0, "max_y": 1})
        self.assertEqual(
            list(iter_tile_coordinates({"min_x": 2, "max_x": 3, "min_y": 4, "max_y": 4}, 5)),
            [{"z": 5, "x": 2, "y": 4}, {"z": 5, "x": 3, "y": 4}],
        )

    def test_contour_label_candidate_filter_accepts_numeric_index_values(self):
        self.assertTrue(is_contour_label_candidate({"index": 5}))
        self.assertTrue(is_contour_label_candidate({"index": 10.0}))
        self.assertTrue(is_contour_label_candidate({"index": "10"}))
        self.assertFalse(is_contour_label_candidate({"index": 1}))
        self.assertFalse(is_contour_label_candidate({"index": True}))
        self.assertFalse(is_contour_label_candidate({}))

    def test_decode_vector_tile_bytes_decompresses_gzip_payload(self):
        calls = []

        def decoder(payload):
            calls.append(payload)
            return {"contour": {"features": []}}

        decoded = decode_vector_tile_bytes(gzip.compress(b"tile-bytes"), decoder)

        self.assertEqual(decoded, {"contour": {"features": []}})
        self.assertEqual(calls, [b"tile-bytes"])

    def test_contour_tile_record_counts_candidates_and_samples_properties(self):
        def decoder(_payload):
            return {
                "contour": {
                    "features": [
                        {
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]],
                            },
                            "properties": {"ele": 1200, "index": 5, "class": "contour"},
                        },
                        {
                            "geometry": {"type": "LineString", "coordinates": [[3, 4], [5, 6]]},
                            "properties": {"ele": 1210, "index": 1},
                        },
                        {
                            "geometry": {
                                "type": "MultiPolygon",
                                "coordinates": [[[[10, 20], [11, 20], [11, 21], [10, 20]]]],
                            },
                            "properties": {"ele": 1300, "index": "10", "extra": "kept-key"},
                        },
                    ]
                }
            }

        record = contour_tile_record(
            tile={"z": 14, "x": 8504, "y": 5833},
            tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
            tile_fetcher=lambda _url: gzip.compress(b"tile"),
            tile_decoder=decoder,
        )
        rectangular_boundary_stats = {
            "feature_count": 1,
            "ring_count": 1,
            "point_count": 5,
            "segment_count": 4,
            "axis_aligned_segment_count": 4,
            "diagonal_segment_count": 0,
            "bbox_edge_segment_count": 4,
        }
        non_rectangular_boundary_stats = {
            "feature_count": 1,
            "ring_count": 1,
            "point_count": 4,
            "segment_count": 3,
            "axis_aligned_segment_count": 2,
            "diagonal_segment_count": 1,
            "bbox_edge_segment_count": 2,
        }

        self.assertEqual(record["status"], "decoded")
        self.assertEqual(record["contour_feature_count"], 3)
        self.assertEqual(record["contour_label_candidate_count"], 2)
        self.assertEqual(record["index_counts"], {"1": 1, "10": 1, "5": 1})
        self.assertEqual(record["geometry_type_counts"], {"LineString": 1, "MultiPolygon": 1, "Polygon": 1})
        self.assertEqual(record["candidate_geometry_type_counts"], {"MultiPolygon": 1, "Polygon": 1})
        self.assertEqual(
            record["candidate_label_geometry"],
            {
                "status": "polygon_only",
                "candidate_count": 2,
                "line_compatible_count": 0,
                "polygon_count": 2,
                "other_count": 0,
            },
        )
        self.assertEqual(record["candidate_polygon_shape_counts"], {"non_rectangular": 1, "rectangular": 1})
        self.assertEqual(
            record["candidate_polygon_boundary_segment_stats"],
            {
                "non_rectangular": non_rectangular_boundary_stats,
                "rectangular": rectangular_boundary_stats,
            },
        )
        self.assertEqual(
            record["candidate_polygon_shape"],
            {
                "status": "mixed_rectangular",
                "polygon_candidate_count": 2,
                "rectangular_count": 1,
                "non_rectangular_count": 1,
                "unsupported_count": 0,
            },
        )
        self.assertEqual(record["sample_candidates"][0]["ele"], 1200)
        self.assertEqual(
            record["sample_candidates"][0]["geometry"],
            {"type": "Polygon", "point_count": 5, "part_count": 1, "bounds": [0.0, 0.0, 2.0, 2.0]},
        )
        self.assertEqual(record["sample_candidates"][0]["polygon_shape"], "rectangular")
        self.assertEqual(record["sample_candidates"][0]["boundary_segment_stats"], rectangular_boundary_stats)
        self.assertEqual(record["sample_candidates"][1]["property_keys"], ["ele", "extra", "index"])

    def test_contour_tile_record_marks_line_compatible_candidate_geometry(self):
        def decoder(_payload):
            return {
                "contour": {
                    "features": [
                        {
                            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                            "properties": {"ele": 1200, "index": 5},
                        }
                    ]
                }
            }

        record = contour_tile_record(
            tile={"z": 14, "x": 8504, "y": 5833},
            tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
            tile_fetcher=lambda _url: gzip.compress(b"tile"),
            tile_decoder=decoder,
        )

        self.assertEqual(
            record["candidate_label_geometry"],
            {
                "status": "line_compatible",
                "candidate_count": 1,
                "line_compatible_count": 1,
                "polygon_count": 0,
                "other_count": 0,
            },
        )
        self.assertEqual(record["candidate_polygon_boundary_segment_stats"], {})

    def test_contour_tile_record_covers_remaining_candidate_geometry_statuses(self):
        cases = [
            (
                [
                    {
                        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                        "properties": {"index": 1},
                    }
                ],
                {
                    "status": "no_candidates",
                    "candidate_count": 0,
                    "line_compatible_count": 0,
                    "polygon_count": 0,
                    "other_count": 0,
                },
            ),
            (
                [
                    {
                        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                        "properties": {"index": 5},
                    },
                    {
                        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                        "properties": {"index": 10},
                    },
                ],
                {
                    "status": "mixed_with_line_compatible",
                    "candidate_count": 2,
                    "line_compatible_count": 1,
                    "polygon_count": 1,
                    "other_count": 0,
                },
            ),
            (
                [{"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"index": 5}}],
                {
                    "status": "no_line_compatible",
                    "candidate_count": 1,
                    "line_compatible_count": 0,
                    "polygon_count": 0,
                    "other_count": 1,
                },
            ),
        ]
        for features, expected in cases:
            with self.subTest(status=expected["status"]):
                record = contour_tile_record(
                    tile={"z": 14, "x": 8504, "y": 5833},
                    tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
                    tile_fetcher=lambda _url: gzip.compress(b"tile"),
                    tile_decoder=lambda _payload, features=features: {"contour": {"features": features}},
                )

                self.assertEqual(record["candidate_label_geometry"], expected)

    def test_contour_tile_record_summarizes_candidate_polygon_shapes(self):
        cases = [
            (
                [
                    {
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 0], [2, 0], [2, 1], [2, 2], [0, 2], [0, 0]]],
                        },
                        "properties": {"index": 5},
                    },
                    {
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [2, 0], [1, 1], [0, 0]]],
                        },
                        "properties": {"index": 10},
                    },
                    {
                        "geometry": {
                            "type": "MultiPolygon",
                            "coordinates": [[[[10, 10], [12, 10], [12, 12], [10, 12], [10, 10]]]],
                        },
                        "properties": {"index": "5"},
                    },
                ],
                {"non_rectangular": 1, "rectangular": 2},
                {
                    "status": "mixed_rectangular",
                    "polygon_candidate_count": 3,
                    "rectangular_count": 2,
                    "non_rectangular_count": 1,
                    "unsupported_count": 0,
                },
            ),
            (
                [
                    {
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [2, 0], [1, 1], [0, 0]]],
                        },
                        "properties": {"index": 5},
                    },
                    {
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[10, 10], [12, 10], [12, 12], [10, 12], [10, 10]],
                                [[10.5, 10.5], [11, 10.5], [11, 11], [10.5, 10.5]],
                            ],
                        },
                        "properties": {"index": 10},
                    },
                ],
                {"non_rectangular": 1, "unsupported": 1},
                {
                    "status": "mixed_polygon_shapes",
                    "polygon_candidate_count": 2,
                    "rectangular_count": 0,
                    "non_rectangular_count": 1,
                    "unsupported_count": 1,
                },
            ),
        ]
        for features, expected_counts, expected_summary in cases:
            with self.subTest(status=expected_summary["status"]):
                record = contour_tile_record(
                    tile={"z": 14, "x": 8504, "y": 5833},
                    tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
                    tile_fetcher=lambda _url: gzip.compress(b"tile"),
                    tile_decoder=lambda _payload, features=features: {"contour": {"features": features}},
                )

                self.assertEqual(record["candidate_polygon_shape_counts"], expected_counts)
                self.assertEqual(record["candidate_polygon_shape"], expected_summary)

    def test_contour_tile_record_reports_fetch_or_decode_errors(self):
        def failing_fetcher(_url):
            raise RuntimeError("offline")

        record = contour_tile_record(
            tile={"z": 14, "x": 8504, "y": 5833},
            tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
            tile_fetcher=failing_fetcher,
            tile_decoder=lambda _payload: {},
        )

        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error"], "RuntimeError")

    def test_collect_contour_feature_report_uses_style_tiles_and_camera(self):
        style_calls = []
        fetched_urls = []

        def fetch_style(token, owner, style_id):
            style_calls.append((token, owner, style_id))
            return {
                "version": 8,
                "sources": {
                    "composite": {
                        "type": "vector",
                        "url": "mapbox://mapbox.mapbox-streets-v8,mapbox.mapbox-terrain-v2",
                    }
                },
                "layers": [],
            }

        def fetch_tile(url):
            fetched_urls.append(url)
            return gzip.compress(b"tile")

        def decoder(_payload):
            return {
                "contour": {
                    "features": [
                        {
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                            },
                            "properties": {"ele": 1000, "index": 5},
                        },
                        {
                            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                            "properties": {"ele": 1010, "index": 1},
                        },
                        {
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]],
                            },
                            "properties": {"ele": 1200, "index": 10},
                        },
                    ]
                }
            }

        generated = dt.datetime(2026, 5, 18, 11, 35, tzinfo=dt.timezone.utc)
        report = collect_contour_feature_report(
            ContourFeatureConfig(token="token", output_root=Path("/tmp"), tile_zoom=0, now=generated),
            style_fetcher=fetch_style,
            tile_fetcher=fetch_tile,
            tile_decoder=decoder,
        )

        self.assertEqual(style_calls, [("token", "mapbox", "outdoors-v12")])
        self.assertEqual(len(fetched_urls), 1)
        self.assertIn("/0/0/0.mvt", fetched_urls[0])
        self.assertEqual(report["camera"]["name"], "chamonix-trails-z14-outdoors")
        self.assertEqual(report["tileset_ids"], ["mapbox.mapbox-streets-v8", "mapbox.mapbox-terrain-v2"])
        self.assertEqual(report["tile_count"], 1)
        self.assertEqual(report["decoded_tile_count"], 1)
        self.assertEqual(report["contour_feature_count"], 3)
        self.assertEqual(report["contour_label_candidate_count"], 2)
        self.assertEqual(report["generated"], "2026-05-18T11:35:00+00:00")
        self.assertEqual(report["geometry_type_counts"], {"LineString": 1, "Polygon": 2})
        self.assertEqual(report["candidate_geometry_type_counts"], {"Polygon": 2})
        self.assertEqual(report["candidate_label_geometry"]["status"], "polygon_only")
        self.assertEqual(report["candidate_label_geometry"]["line_compatible_count"], 0)
        self.assertEqual(report["candidate_polygon_shape_counts"], {"rectangular": 2})
        self.assertEqual(report["candidate_polygon_shape"]["status"], "rectangular_only")
        self.assertEqual(
            report["candidate_polygon_boundary_segment_stats"],
            {
                "rectangular": {
                    "feature_count": 2,
                    "ring_count": 2,
                    "point_count": 10,
                    "segment_count": 8,
                    "axis_aligned_segment_count": 8,
                    "diagonal_segment_count": 0,
                    "bbox_edge_segment_count": 8,
                }
            },
        )

    def test_collect_all_camera_contour_feature_report_aggregates_camera_rows(self):
        style_calls = []

        def fetch_style(_token, _owner, _style_id):
            style_calls.append((_token, _owner, _style_id))
            return {
                "version": 8,
                "sources": {
                    "composite": {
                        "type": "vector",
                        "url": "mapbox://mapbox.mapbox-streets-v8,mapbox.mapbox-terrain-v2",
                    }
                },
                "layers": [],
            }

        def decoder(_payload):
            return {
                "contour": {
                    "features": [
                        {
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                            },
                            "properties": {"ele": 1000, "index": 5},
                        },
                        {
                            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                            "properties": {"ele": 1010, "index": 1},
                        },
                    ]
                }
            }

        generated = dt.datetime(2026, 5, 18, 11, 35, tzinfo=dt.timezone.utc)
        with mock.patch(
            "qfit.validation.mapbox_outdoors_contour_features._comparison_camera_names",
            return_value=["chamonix-trails-z14-outdoors", "zermatt-trails-z18-outdoors"],
        ):
            report = collect_all_camera_contour_feature_report(
                ContourFeatureConfig(token="token", output_root=Path("/tmp"), tile_zoom=0, now=generated),
                style_fetcher=fetch_style,
                tile_fetcher=lambda _url: gzip.compress(b"tile"),
                tile_decoder=decoder,
            )

        self.assertEqual(style_calls, [("token", "mapbox", "outdoors-v12")])
        self.assertEqual(report["camera_count"], 2)
        self.assertEqual(report["successful_camera_count"], 2)
        self.assertEqual(report["failed_camera_count"], 0)
        self.assertEqual(report["tile_count"], 2)
        self.assertEqual(report["decoded_tile_count"], 2)
        self.assertEqual(report["contour_feature_count"], 4)
        self.assertEqual(report["contour_label_candidate_count"], 2)
        self.assertEqual(report["candidate_label_geometry_statuses"], {"polygon_only": 2})
        self.assertEqual(report["candidate_polygon_shape_statuses"], {"rectangular_only": 2})
        self.assertEqual(
            report["candidate_polygon_boundary_segment_stats"],
            {
                "rectangular": {
                    "feature_count": 2,
                    "ring_count": 2,
                    "point_count": 10,
                    "segment_count": 8,
                    "axis_aligned_segment_count": 8,
                    "diagonal_segment_count": 0,
                    "bbox_edge_segment_count": 8,
                }
            },
        )
        self.assertEqual(
            [camera["camera"] for camera in report["cameras"]],
            ["chamonix-trails-z14-outdoors", "zermatt-trails-z18-outdoors"],
        )
        self.assertEqual([camera["status"] for camera in report["cameras"]], ["decoded", "decoded"])
        self.assertEqual(
            [camera["candidate_polygon_shape_status"] for camera in report["cameras"]],
            ["rectangular_only", "rectangular_only"],
        )
        self.assertEqual(
            [camera["candidate_polygon_boundary_segment_stats"] for camera in report["cameras"]],
            [
                {
                    "rectangular": {
                        "feature_count": 1,
                        "ring_count": 1,
                        "point_count": 5,
                        "segment_count": 4,
                        "axis_aligned_segment_count": 4,
                        "diagonal_segment_count": 0,
                        "bbox_edge_segment_count": 4,
                    }
                },
                {
                    "rectangular": {
                        "feature_count": 1,
                        "ring_count": 1,
                        "point_count": 5,
                        "segment_count": 4,
                        "axis_aligned_segment_count": 4,
                        "diagonal_segment_count": 0,
                        "bbox_edge_segment_count": 4,
                    }
                },
            ],
        )
        self.assertNotIn("camera_reports", report)

    def test_collect_all_camera_contour_feature_report_keeps_camera_errors(self):
        def fetch_style(_token, _owner, _style_id):
            return {
                "version": 8,
                "sources": {
                    "composite": {
                        "type": "vector",
                        "url": "mapbox://mapbox.mapbox-streets-v8,mapbox.mapbox-terrain-v2",
                    }
                },
                "layers": [],
            }

        with mock.patch(
            "qfit.validation.mapbox_outdoors_contour_features._comparison_camera_names",
            return_value=["chamonix-trails-z14-outdoors", "missing-camera"],
        ):
            report = collect_all_camera_contour_feature_report(
                ContourFeatureConfig(token="token", output_root=Path("/tmp"), tile_zoom=0),
                style_fetcher=fetch_style,
                tile_fetcher=lambda _url: gzip.compress(b"tile"),
                tile_decoder=lambda _payload: {"contour": {"features": []}},
            )

        self.assertEqual(report["camera_count"], 2)
        self.assertEqual(report["successful_camera_count"], 1)
        self.assertEqual(report["failed_camera_count"], 1)
        self.assertEqual(report["cameras"][1]["camera"], "missing-camera")
        self.assertEqual(report["cameras"][1]["status"], "error")
        self.assertEqual(report["cameras"][1]["error"], "ValueError")

    def test_write_report_writes_json_and_markdown(self):
        report = {
            "generated": "2026-05-18T11:22:00+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "camera": {"name": "chamonix"},
            "tile_zoom": 14,
            "tile_count": 1,
            "decoded_tile_count": 1,
            "contour_feature_count": 3,
            "contour_label_candidate_count": 2,
            "index_counts": {"1": 1, "5": 2},
            "geometry_type_counts": {"LineString": 1, "Polygon": 2},
            "candidate_geometry_type_counts": {"Polygon": 2},
            "candidate_label_geometry": {
                "status": "polygon_only",
                "candidate_count": 2,
                "line_compatible_count": 0,
                "polygon_count": 2,
                "other_count": 0,
            },
            "candidate_polygon_shape_counts": {"rectangular": 2},
            "candidate_polygon_shape": {
                "status": "rectangular_only",
                "polygon_candidate_count": 2,
                "rectangular_count": 2,
                "non_rectangular_count": 0,
                "unsupported_count": 0,
            },
            "candidate_polygon_boundary_segment_stats": {
                "rectangular": {
                    "feature_count": 2,
                    "ring_count": 2,
                    "point_count": 10,
                    "segment_count": 8,
                    "axis_aligned_segment_count": 8,
                    "diagonal_segment_count": 0,
                    "bbox_edge_segment_count": 8,
                }
            },
            "sample_candidates": [{"ele": 1000, "index": 5, "geometry": {"type": "Polygon"}}],
            "tiles": [
                {
                    "z": 14,
                    "x": 8504,
                    "y": 5833,
                    "status": "decoded",
                    "contour_feature_count": 3,
                    "contour_label_candidate_count": 2,
                    "index_counts": {"1": 1, "5": 2},
                    "geometry_type_counts": {"LineString": 1, "Polygon": 2},
                    "candidate_geometry_type_counts": {"Polygon": 2},
                    "candidate_polygon_shape_counts": {"rectangular": 2},
                    "candidate_polygon_boundary_segment_stats": {
                        "rectangular": {
                            "feature_count": 2,
                            "ring_count": 2,
                            "point_count": 10,
                            "segment_count": 8,
                            "axis_aligned_segment_count": 8,
                            "diagonal_segment_count": 0,
                            "bbox_edge_segment_count": 8,
                        }
                    },
                }
            ],
        }
        markdown = build_summary_markdown(report)

        self.assertIn("Contour-label candidates (index 5/10): 2", markdown)
        self.assertIn('Candidate geometry types: {"Polygon":2}', markdown)
        self.assertIn('Candidate label geometry: {"candidate_count":2', markdown)
        self.assertIn('Candidate polygon shapes: {"non_rectangular_count":0', markdown)
        self.assertIn('Candidate polygon boundary segments: {"rectangular":{', markdown)
        self.assertIn('{"rectangular":2}', markdown)
        self.assertIn("| 14 | 8504 | 5833 | decoded | 3 | 2 |", markdown)
        self.assertIn("Sample contour-label candidates", markdown)

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = build_contour_feature_paths(Path(tmpdir) / "run")
            write_report(report, paths)

            self.assertEqual(json.loads(paths.json_path.read_text(encoding="utf-8"))["tile_count"], 1)
            self.assertIn("Contour features: 3", paths.summary_path.read_text(encoding="utf-8"))

    def test_write_all_camera_report_writes_json_and_markdown(self):
        report = {
            "generated": "2026-05-18T11:22:00+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "camera_count": 2,
            "successful_camera_count": 2,
            "failed_camera_count": 0,
            "tile_count": 2,
            "decoded_tile_count": 2,
            "contour_feature_count": 4,
            "contour_label_candidate_count": 2,
            "candidate_label_geometry_statuses": {"no_candidates": 1, "polygon_only": 1},
            "candidate_polygon_shape_statuses": {"no_polygon_candidates": 1, "rectangular_only": 1},
            "candidate_polygon_boundary_segment_stats": {
                "rectangular": {
                    "feature_count": 2,
                    "ring_count": 2,
                    "point_count": 10,
                    "segment_count": 8,
                    "axis_aligned_segment_count": 8,
                    "diagonal_segment_count": 0,
                    "bbox_edge_segment_count": 8,
                }
            },
            "cameras": [
                {
                    "status": "decoded",
                    "camera": "switzerland-alps-z5-outdoors",
                    "camera_zoom": 5.0,
                    "tile_zoom": 5,
                    "tile_count": 1,
                    "decoded_tile_count": 1,
                    "contour_feature_count": 0,
                    "contour_label_candidate_count": 0,
                    "candidate_label_geometry_status": "no_candidates",
                    "candidate_geometry_type_counts": {},
                    "candidate_polygon_shape_status": "no_polygon_candidates",
                    "candidate_polygon_shape_counts": {},
                    "candidate_polygon_boundary_segment_stats": {},
                },
                {
                    "status": "decoded",
                    "camera": "chamonix-trails-z14-outdoors",
                    "camera_zoom": 14.0,
                    "tile_zoom": 14,
                    "tile_count": 1,
                    "decoded_tile_count": 1,
                    "contour_feature_count": 4,
                    "contour_label_candidate_count": 2,
                    "candidate_label_geometry_status": "polygon_only",
                    "candidate_geometry_type_counts": {"Polygon": 2},
                    "candidate_polygon_shape_status": "rectangular_only",
                    "candidate_polygon_shape_counts": {"rectangular": 2},
                    "candidate_polygon_boundary_segment_stats": {
                        "rectangular": {
                            "feature_count": 2,
                            "ring_count": 2,
                            "point_count": 10,
                            "segment_count": 8,
                            "axis_aligned_segment_count": 8,
                            "diagonal_segment_count": 0,
                            "bbox_edge_segment_count": 8,
                        }
                    },
                },
            ],
        }
        markdown = build_all_camera_summary_markdown(report)

        self.assertIn("# Mapbox Outdoors contour feature diagnostic - all cameras", markdown)
        self.assertIn('Camera statuses: {"decoded":2}', markdown)
        self.assertIn('Candidate label geometry statuses: {"no_candidates":1,"polygon_only":1}', markdown)
        self.assertIn('Candidate polygon shape statuses: {"no_polygon_candidates":1,"rectangular_only":1}', markdown)
        self.assertIn('Candidate polygon boundary segments: {"rectangular":{', markdown)
        self.assertIn("| chamonix-trails-z14-outdoors | decoded | 14.0 | 14 | 1/1 | 4 | 2 | polygon_only |", markdown)

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = build_all_camera_contour_feature_paths(Path(tmpdir) / "run")
            write_all_camera_report(report, paths)

            written = json.loads(paths.json_path.read_text(encoding="utf-8"))
            self.assertEqual(written["camera_count"], 2)
            self.assertNotIn("camera_reports", written)
            self.assertIn("Cameras: 2", paths.summary_path.read_text(encoding="utf-8"))

    def test_main_writes_single_camera_report(self):
        report = {
            "generated": "2026-05-18T11:22:00+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "camera": {"name": "chamonix-trails-z14-outdoors"},
            "tile_zoom": 14,
            "tile_count": 1,
            "decoded_tile_count": 1,
            "contour_feature_count": 3,
            "contour_label_candidate_count": 2,
            "index_counts": {},
            "geometry_type_counts": {},
            "candidate_geometry_type_counts": {},
            "candidate_label_geometry": {"status": "polygon_only"},
            "candidate_polygon_shape_counts": {},
            "candidate_polygon_shape": {"status": "no_polygon_candidates"},
            "candidate_polygon_boundary_segment_stats": {},
            "sample_candidates": [],
            "tiles": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "qfit.validation.mapbox_outdoors_contour_features.collect_contour_feature_report",
                return_value=report,
            ) as collect_report:
                result = main([
                    "chamonix-trails-z14-outdoors",
                    "--mapbox-token",
                    "token",
                    "--output-root",
                    tmpdir,
                ])

            self.assertIsNone(result)
            collect_report.assert_called_once()
            summaries = list(Path(tmpdir).glob("chamonix-trails-z14-outdoors/*/summary.md"))
            self.assertEqual(len(summaries), 1)
            self.assertIn("Contour features: 3", summaries[0].read_text(encoding="utf-8"))

    def test_main_writes_all_camera_report(self):
        report = {
            "generated": "2026-05-18T11:22:00+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "camera_count": 1,
            "successful_camera_count": 1,
            "failed_camera_count": 0,
            "tile_count": 1,
            "decoded_tile_count": 1,
            "contour_feature_count": 3,
            "contour_label_candidate_count": 2,
            "candidate_label_geometry_statuses": {"polygon_only": 1},
            "candidate_polygon_shape_statuses": {"rectangular_only": 1},
            "candidate_polygon_boundary_segment_stats": {"rectangular": {"feature_count": 2}},
            "cameras": [
                {
                    "status": "decoded",
                    "camera": "chamonix-trails-z14-outdoors",
                    "camera_zoom": 13.75,
                    "tile_zoom": 14,
                    "tile_count": 1,
                    "decoded_tile_count": 1,
                    "contour_feature_count": 3,
                    "contour_label_candidate_count": 2,
                    "candidate_label_geometry_status": "polygon_only",
                    "candidate_geometry_type_counts": {"Polygon": 2},
                    "candidate_polygon_shape_status": "rectangular_only",
                    "candidate_polygon_shape_counts": {"rectangular": 2},
                    "candidate_polygon_boundary_segment_stats": {"rectangular": {"feature_count": 2}},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "qfit.validation.mapbox_outdoors_contour_features.collect_all_camera_contour_feature_report",
                return_value=report,
            ) as collect_report:
                result = main([
                    "--all-cameras",
                    "--mapbox-token",
                    "token",
                    "--output-root",
                    tmpdir,
                ])

            self.assertIsNone(result)
            collect_report.assert_called_once()
            summaries = list(Path(tmpdir).glob("all-cameras/*/summary.md"))
            self.assertEqual(len(summaries), 1)
            self.assertIn("Cameras: 1", summaries[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
