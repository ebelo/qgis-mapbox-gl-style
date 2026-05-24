import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from tests import _path  # noqa: F401

from qfit.validation.mapbox_outdoors_source_crop_overlap import (
    SourceCropOverlapConfig,
    _candidate_missing_filter_property_summary,
    _combined_filter_property_requirements,
    _comparison_membership_contains,
    _mapbox_expression_value,
    _mapbox_filter_matches,
    _source_filter_property_names,
    _style_layer_active_at_zoom,
    bbox_overlaps_lon_lat_bounds,
    build_run_directory,
    build_source_crop_overlap_paths,
    build_summary_markdown,
    collect_source_crop_overlap_report,
    crop_box_lon_lat_bounds,
    feature_lon_lat_bbox,
    lon_lat_to_tile,
    recommended_tile_zoom,
    resolve_mapbox_token,
    source_layer_overlap_record,
    tiles_for_lon_lat_bounds,
    write_report,
)


class MapboxOutdoorsSourceCropOverlapTests(unittest.TestCase):
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
            output_root=Path("/tmp/source-overlap"),
            camera_name="zermatt",
            now=dt.datetime(2026, 5, 21, 21, 30, tzinfo=dt.timezone.utc),
        )
        paths = build_source_crop_overlap_paths(run_dir)

        self.assertEqual(run_dir, Path("/tmp/source-overlap/zermatt/20260521T213000Z"))
        self.assertEqual(paths.json_path, run_dir / "source-crop-overlap.json")
        self.assertEqual(paths.summary_path, run_dir / "summary.md")

    def test_crop_bounds_and_tile_helpers_cover_unrotated_camera(self):
        camera = {
            "longitude": 0.0,
            "latitude": 0.0,
            "zoom": 2.0,
            "width": 512,
            "height": 512,
        }

        bounds = crop_box_lon_lat_bounds(camera=camera, box=[256, 256, 384, 384])
        tiles = tiles_for_lon_lat_bounds(bounds, 2)

        self.assertEqual(recommended_tile_zoom(17.6), 18)
        self.assertEqual(lon_lat_to_tile(0.0, 0.0, 2), (2, 2))
        self.assertGreater(bounds["east"], bounds["west"])
        self.assertLess(bounds["south"], bounds["north"])
        self.assertIn({"z": 2, "x": 2, "y": 2}, tiles)

    def test_feature_bbox_overlap_uses_transformed_lon_lat_coordinates(self):
        feature = {
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0], [-1.0, -1.0]]],
            },
            "properties": {"class": "park"},
        }

        bbox = feature_lon_lat_bbox(feature)

        self.assertEqual(bbox, (-1.0, -1.0, 1.0, 1.0))
        self.assertTrue(
            bbox_overlaps_lon_lat_bounds(
                bbox,
                {"west": -0.5, "south": -0.5, "east": 0.5, "north": 0.5},
            )
        )
        self.assertFalse(
            bbox_overlaps_lon_lat_bounds(
                bbox,
                {"west": 2.0, "south": 2.0, "east": 3.0, "north": 3.0},
            )
        )

    def test_collect_report_counts_source_features_overlapping_crop_boxes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            visual_crop_json = _write_source_overlap_fixture(root)
            fetched_urls = []

            def fetcher(url):
                fetched_urls.append(url)
                return b"fake-tile"

            def decoder(_payload, _tile):
                return {
                    "landuse": {
                        "features": [
                            _feature(
                                "Polygon",
                                [[[9, 9], [11, 9], [11, 11], [9, 11], [9, 9]]],
                                {"class": "park", "type": "park"},
                            ),
                            _feature(
                                "Polygon",
                                [[[30, 30], [31, 30], [31, 31], [30, 31], [30, 30]]],
                                {"class": "cemetery", "type": "cemetery"},
                            ),
                        ]
                    },
                    "contour": {
                        "features": [
                            _feature("LineString", [[9, 10], [11, 10]], {"index": 10, "ele": 1600}),
                            _feature("LineString", [[9, 10.5], [11, 10.5]], {"index": 1, "ele": 1580}),
                        ]
                    },
                    "landuse_overlay": {"features": []},
                }

            report = collect_source_crop_overlap_report(
                SourceCropOverlapConfig(
                    token="test-token",
                    visual_crop_json_path=visual_crop_json,
                    output_root=root / "output",
                    camera_name="test-camera",
                    source_layers=("landuse", "landuse_overlay", "contour"),
                    tile_zoom=2,
                    now=dt.datetime(2026, 5, 21, 21, 45, tzinfo=dt.timezone.utc),
                ),
                tile_fetcher=fetcher,
                tile_decoder=decoder,
            )

        self.assertEqual(report["decoded_tile_count"], 1)
        self.assertEqual(report["tileset_ids"], ["mapbox.mapbox-streets-v8", "mapbox.mapbox-terrain-v2"])
        self.assertNotIn("test-token", json.dumps(report))
        self.assertEqual(len(fetched_urls), 1)
        self.assertIn("test-token", fetched_urls[0])

        combined = {row["source_layer"]: row for row in report["combined_source_layers"]}
        self.assertEqual(combined["landuse"]["overlap_feature_count"], 1)
        self.assertEqual(combined["landuse"]["property_counts"]["class"], {"park": 1})
        self.assertAlmostEqual(combined["landuse"]["bbox_crop_coverage_ratio"], 0.128418417649)
        self.assertAlmostEqual(
            combined["landuse"]["property_overlap_areas"]["class"]["park"]["crop_coverage_ratio"],
            0.128418417649,
        )
        self.assertAlmostEqual(
            combined["landuse"]["qgis_style_layer_matches"]["landuse-park"]["crop_coverage_ratio"],
            0.128418417649,
        )
        self.assertEqual(
            combined["landuse"]["qgis_filter_property_requirements"]["landuse-park-sized"][
                "missing_feature_counts"
            ],
            {"sizerank": 1},
        )
        self.assertEqual(
            combined["landuse"]["qgis_filter_property_requirements"]["landuse-park-sized"][
                "candidate_missing_feature_counts"
            ],
            {},
        )
        self.assertEqual(
            combined["landuse"]["qgis_filter_property_requirements"]["landuse-park-sized"][
                "candidate_property_counts"
            ],
            {},
        )
        self.assertEqual(
            combined["landuse"]["qgis_filter_property_requirements"]["landuse-park-sized"][
                "matched_feature_count"
            ],
            1,
        )
        self.assertEqual(combined["landuse_overlay"]["overlap_feature_count"], 0)
        self.assertEqual(combined["contour"]["overlap_feature_count"], 2)
        self.assertEqual(combined["contour"]["bbox_crop_coverage_ratio"], 0.0)
        self.assertEqual(
            combined["contour"]["qgis_style_layer_matches"]["contour-major"]["feature_count"],
            1,
        )
        self.assertEqual(
            combined["contour"]["qgis_style_layer_matches"]["contour-minor"]["feature_count"],
            1,
        )
        self.assertEqual(combined["contour"]["property_counts"]["index"], {"10": 1, "1": 1})
        self.assertEqual(combined["contour"]["ele_range"], {"min": 1580.0, "max": 1600.0})

        markdown = build_summary_markdown(report)
        self.assertIn("Bbox coverage is a summed upper-bound attribution aid", markdown)
        self.assertIn("QGIS style-layer coverage evaluates camera-zoom-active filters", markdown)
        self.assertIn("QGIS filter missing props reports active style-layer filter properties", markdown)
        self.assertIn("## QGIS Style-Layer Paint Coverage", markdown)
        self.assertIn(
            "| `landuse` | `landuse-park` | `fill` | 1 | 0.128 | "
            'fill-color="hsl(98, 55%, 70%)"<br>fill-opacity=1.0 |',
            markdown,
        )
        self.assertIn(
            "| `contour` | `contour-major` | `line` | 1 | 0.000 | "
            'line-color="hsl(60, 10%, 35%)"<br>line-opacity=1.0 |',
            markdown,
        )
        self.assertIn(
            "| `landuse` | 2 | 1 | 0.128 | park=1 | park=0.128 | landuse-park=0.128, landuse-park-sized=0.128 | landuse-park-sized: sizerank=1/1 candidate=0 (matched=1) | park=1 | - | - |",
            markdown,
        )
        self.assertIn("| `landuse_overlay` | 0 | 0 | 0.000 | - | - | - | - | - | - | - |", markdown)
        self.assertIn(
            "| `contour` | 2 | 2 | 0.000 | - | - | contour-major=0.000, contour-minor=0.000 | - | - | 10=1, 1=1 | 1580-1600 |",
            markdown,
        )

    def test_filter_property_candidates_ignore_normal_class_mismatches(self):
        record = source_layer_overlap_record(
            decoded_tiles=[
                {
                    "landuse": {
                        "features": [
                            _feature(
                                "Polygon",
                                [[[-0.9, -0.9], [-0.1, -0.9], [-0.1, -0.1], [-0.9, -0.1], [-0.9, -0.9]]],
                                {"class": "park"},
                            ),
                            _feature(
                                "Polygon",
                                [[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9], [0.1, 0.1]]],
                                {"class": "cemetery"},
                            ),
                            _feature(
                                "Polygon",
                                [[[-0.8, 0.1], [-0.2, 0.1], [-0.2, 0.8], [-0.8, 0.8], [-0.8, 0.1]]],
                                {"class": "park", "sizerank": 3},
                            ),
                        ]
                    }
                }
            ],
            bounds={"west": -1.0, "south": -1.0, "east": 1.0, "north": 1.0},
            source_layer="landuse",
            camera_zoom=18.0,
            style_layers=[
                {
                    "id": "landuse-park-sized",
                    "type": "fill",
                    "source-layer": "landuse",
                    "filter": [
                        "all",
                        ["==", ["get", "class"], "park"],
                        [">=", ["to-number", ["get", "sizerank"]], 0],
                    ],
                },
                {
                    "id": "landuse-park-or-sized",
                    "type": "fill",
                    "source-layer": "landuse",
                    "filter": [
                        "any",
                        ["==", ["get", "class"], "park"],
                        [">=", ["to-number", ["get", "sizerank"]], 0],
                    ],
                },
                {
                    "id": "landuse-none-sized",
                    "type": "fill",
                    "source-layer": "landuse",
                    "filter": [
                        "none",
                        ["!=", ["get", "sizerank"], 3],
                        ["==", ["get", "class"], "cemetery"],
                    ],
                },
                {
                    "id": "landuse-case-preserves-branch",
                    "type": "fill",
                    "source-layer": "landuse",
                    "filter": [
                        "case",
                        ["==", ["get", "sizerank"], 1],
                        True,
                        ["==", ["get", "class"], "park"],
                        True,
                        False,
                    ],
                },
                {
                    "id": "landuse-case-sized-park",
                    "type": "fill",
                    "source-layer": "landuse",
                    "filter": [
                        "case",
                        [
                            "all",
                            ["==", ["get", "class"], "park"],
                            [">=", ["to-number", ["get", "sizerank"]], 0],
                        ],
                        True,
                        False,
                    ],
                },
                {
                    "id": "landuse-match-sized-park",
                    "type": "fill",
                    "source-layer": "landuse",
                    "filter": [
                        "match",
                        ["get", "class"],
                        "park",
                        [">=", ["to-number", ["get", "sizerank"]], 0],
                        False,
                    ],
                }
            ],
        )

        requirement = record["qgis_filter_property_requirements"]["landuse-park-sized"]
        self.assertEqual(requirement["missing_feature_counts"], {"sizerank": 2})
        self.assertEqual(requirement["candidate_missing_feature_counts"], {})
        self.assertEqual(requirement["candidate_property_counts"], {})
        self.assertEqual(requirement["candidate_property_overlap_areas"], {})
        self.assertEqual(requirement["candidate_missing_feature_total"], 0)
        self.assertEqual(requirement["matched_feature_count"], 2)
        self.assertEqual(
            record["qgis_filter_property_requirements"]["landuse-park-or-sized"][
                "candidate_missing_feature_counts"
            ],
            {},
        )
        self.assertEqual(
            record["qgis_filter_property_requirements"]["landuse-none-sized"]["candidate_missing_feature_counts"],
            {"sizerank": 1},
        )
        self.assertEqual(
            record["qgis_filter_property_requirements"]["landuse-case-preserves-branch"][
                "candidate_missing_feature_counts"
            ],
            {},
        )
        self.assertEqual(
            record["qgis_filter_property_requirements"]["landuse-case-sized-park"][
                "candidate_missing_feature_counts"
            ],
            {},
        )
        self.assertEqual(
            record["qgis_filter_property_requirements"]["landuse-match-sized-park"][
                "candidate_missing_feature_counts"
            ],
            {},
        )

    def test_match_input_missing_property_counts_as_candidate_gate(self):
        record = source_layer_overlap_record(
            decoded_tiles=[
                {
                    "landuse": {
                        "features": [
                            _feature(
                                "Polygon",
                                [[[-0.9, -0.9], [0.9, -0.9], [0.9, 0.9], [-0.9, 0.9], [-0.9, -0.9]]],
                                {"type": "park"},
                            )
                        ]
                    }
                }
            ],
            bounds={"west": -1.0, "south": -1.0, "east": 1.0, "north": 1.0},
            source_layer="landuse",
            camera_zoom=18.0,
            style_layers=[
                {
                    "id": "landuse-class-match",
                    "type": "fill",
                    "source-layer": "landuse",
                    "filter": ["match", ["get", "class"], "park", True, False],
                }
            ],
        )

        requirement = record["qgis_filter_property_requirements"]["landuse-class-match"]
        self.assertEqual(requirement["missing_feature_counts"], {"class": 1})
        self.assertEqual(requirement["candidate_missing_feature_counts"], {"class": 1})
        self.assertEqual(requirement["candidate_property_counts"], {"type": {"park": 1}})
        self.assertEqual(requirement["matched_feature_count"], 0)

    def test_candidate_property_summary_keeps_all_values_for_combined_aggregation(self):
        features = [
            _feature(
                "Polygon",
                [[[index, 0], [index + 0.5, 0], [index + 0.5, 0.5], [index, 0.5], [index, 0]]],
                {"class": f"class-{index}"},
            )
            for index in range(9)
        ]

        missing_counts, candidate_property_counts, candidate_property_areas = _candidate_missing_filter_property_summary(
            features,
            ["class", "sizerank"],
            [
                "all",
                ["==", ["get", "sizerank"], 1],
                ["!=", ["get", "class"], "excluded"],
            ],
            bounds={"west": 0.0, "south": 0.0, "east": 9.0, "north": 1.0},
            crop_area=9.0,
            camera_zoom=18.0,
        )

        self.assertEqual(missing_counts, {"sizerank": 9})
        self.assertEqual(len(candidate_property_counts["class"]), 9)
        self.assertEqual(candidate_property_counts["class"]["class-8"], 1)
        self.assertEqual(len(candidate_property_areas["class"]), 9)
        self.assertAlmostEqual(
            candidate_property_areas["class"]["class-8"]["crop_coverage_ratio"],
            0.027777777778,
        )

    def test_combined_candidate_counts_follow_displayed_missing_properties(self):
        missing_counts = {f"p{index}": 20 for index in range(8)}
        missing_counts["p8"] = 1
        candidate_counts = {"p0": 1, **{f"p{index}": 5 for index in range(1, 9)}}

        combined = _combined_filter_property_requirements(
            [
                {
                    "qgis_filter_property_requirements": {
                        "landuse-many": {
                            "filter_properties": list(missing_counts),
                            "missing_feature_counts": missing_counts,
                            "missing_feature_total": sum(missing_counts.values()),
                            "candidate_missing_feature_counts": candidate_counts,
                            "candidate_missing_feature_total": sum(candidate_counts.values()),
                            "candidate_property_counts": {"class": {"commercial_area": 4}},
                            "candidate_property_overlap_areas": {
                                "class": {
                                    "commercial_area": {
                                        "overlap_bbox_area": 4.0,
                                        "crop_coverage_ratio": 999.0,
                                    }
                                }
                            },
                            "overlap_feature_count": 25,
                            "matched_feature_count": 0,
                        }
                    }
                }
            ],
            crop_area=8.0,
        )

        requirement = combined["landuse-many"]
        self.assertEqual(set(requirement["missing_feature_counts"]), {f"p{index}" for index in range(8)})
        self.assertEqual(requirement["candidate_missing_feature_counts"]["p0"], 1)
        self.assertEqual(requirement["candidate_property_counts"], {"class": {"commercial_area": 4}})
        self.assertEqual(
            requirement["candidate_property_overlap_areas"],
            {"class": {"commercial_area": {"overlap_bbox_area": 4.0, "crop_coverage_ratio": 0.5}}},
        )
        self.assertNotIn("p8", requirement["candidate_missing_feature_counts"])

    def test_mapbox_filter_helpers_cover_preprocessed_style_expressions(self):
        properties = {"class": "park", "type": "garden", "sizerank": "3", "index": 10}
        context_properties = {**properties, "$geometry_type": "Polygon", "$zoom": 18.0}

        self.assertTrue(_comparison_membership_contains("ark", "parkland"))
        self.assertFalse(_comparison_membership_contains(1, "123"))
        self.assertTrue(_comparison_membership_contains("park", ["park", "cemetery"]))
        self.assertEqual(_mapbox_expression_value(["get", "class"], properties), "park")
        self.assertEqual(_mapbox_expression_value(["literal", ["park", "cemetery"]], properties), ["park", "cemetery"])
        self.assertEqual(_mapbox_expression_value(["to-number", ["get", "missing"]], properties), 0.0)
        self.assertEqual(_mapbox_expression_value(["to-number", False], properties), 0.0)
        self.assertEqual(_mapbox_expression_value(["to-number", True], properties), 1.0)
        self.assertEqual(_mapbox_expression_value(["to-number", ["get", "class"], 5], properties), 5.0)
        self.assertTrue(_mapbox_expression_value(["has", "class"], properties))
        self.assertFalse(_mapbox_expression_value(["!has", "class"], properties))
        self.assertTrue(_mapbox_expression_value(["!has", "missing"], properties))
        self.assertIsNone(_mapbox_expression_value(["match"], properties))
        self.assertIsNone(_mapbox_expression_value(["match", ["get", "class"], "park", True], properties))
        self.assertIsNone(_mapbox_expression_value(["case"], properties))
        self.assertEqual(
            _mapbox_expression_value(["step", ["zoom"], "low", 10, "mid", 18, "high"], context_properties),
            "high",
        )
        self.assertEqual(
            _mapbox_expression_value(
                [
                    "case",
                    ["==", ["get", "class"], "school"],
                    "school",
                    ["==", ["get", "class"], "park"],
                    "park",
                    "fallback",
                ],
                properties,
            ),
            "park",
        )
        self.assertTrue(
            _mapbox_filter_matches(
                [
                    "all",
                    [">=", ["to-number", ["get", "sizerank"]], 0],
                    ["<=", ["to-number", ["get", "sizerank"]], 14],
                    ["match", ["get", "class"], ["park", "cemetery"], True, False],
                    ["!=", ["get", "type"], "zoo"],
                ],
                properties,
            )
        )
        self.assertTrue(
            _mapbox_filter_matches(
                [
                    "all",
                    [">=", ["to-number", ["get", "sizerank"]], 0],
                    ["<=", ["to-number", ["get", "sizerank"]], 14],
                    ["match", ["get", "class"], "commercial_area", True, False],
                ],
                {"class": "commercial_area"},
            )
        )
        self.assertFalse(
            _mapbox_filter_matches(
                ["all", ["match", ["get", "class"], "commercial_area", True, False]],
                properties,
            )
        )
        self.assertTrue(_mapbox_filter_matches(["==", ["geometry-type"], "Polygon"], context_properties))
        self.assertTrue(_mapbox_filter_matches(["==", "$type", "Polygon"], context_properties))
        self.assertTrue(_mapbox_filter_matches(["in", "class", "school", "park"], context_properties))
        self.assertFalse(_mapbox_filter_matches(["!in", "class", "school", "park"], context_properties))
        self.assertTrue(
            _mapbox_filter_matches(
                ["any", ["==", ["get", "class"], "school"], ["==", ["get", "class"], "park"]],
                properties,
            )
        )
        self.assertTrue(_mapbox_filter_matches(["none", ["==", "class", "school"], ["!has", "type"]], properties))
        self.assertFalse(_mapbox_filter_matches(["none", ["==", "class", "school"], ["!has", "missing"]], properties))
        self.assertFalse(_mapbox_filter_matches(["!", ["==", ["get", "class"], "park"]], properties))
        self.assertTrue(_mapbox_filter_matches(["in", ["get", "class"], ["literal", ["park", "cemetery"]]], properties))
        self.assertTrue(_mapbox_filter_matches(["!in", ["get", "class"], ["literal", ["school", "cemetery"]]], properties))
        self.assertFalse(_mapbox_filter_matches(["!in", ["get", "class"], ["literal", ["park", "cemetery"]]], properties))
        self.assertTrue(_mapbox_filter_matches(["in", ["get", "class"], "parkland"], properties))
        self.assertTrue(_mapbox_filter_matches(["!in", ["get", "class"], "schoolyard"], properties))
        self.assertFalse(_mapbox_filter_matches(["!in", ["get", "class"], "parkland"], properties))
        self.assertFalse(_mapbox_filter_matches([">", ["get", "class"], 1], properties))
        self.assertFalse(_mapbox_filter_matches([], properties))
        self.assertTrue(_mapbox_filter_matches(True, properties))
        self.assertFalse(_mapbox_filter_matches(False, properties))
        self.assertFalse(_style_layer_active_at_zoom({"layout": {"visibility": "none"}}, 12.0))
        self.assertTrue(_style_layer_active_at_zoom({"layout": {"visibility": "visible"}}, 12.0))
        self.assertTrue(_style_layer_active_at_zoom({"minzoom": 10, "maxzoom": 18}, 17.9))
        self.assertFalse(_style_layer_active_at_zoom({"minzoom": 10, "maxzoom": 18}, 18.0))
        self.assertFalse(_style_layer_active_at_zoom({"minzoom": 10}, 9.9))
        self.assertEqual(
            _source_filter_property_names(
                [
                    "all",
                    ["!has", "expected_absent"],
                    ["has", "class"],
                    ["==", "legacy_left", ["get", "legacy_right"]],
                    ["==", "$type", ["get", "kind"]],
                ]
            ),
            ["class", "kind", "legacy_left", "legacy_right"],
        )

    def test_write_report_outputs_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            paths = build_source_crop_overlap_paths(run_dir)
            report = {
                "generated": "2026-05-21T21:45:00+00:00",
                "camera": "test-camera",
                "camera_zoom": 18.0,
                "tile_zoom": 18,
                "decoded_tile_count": 1,
                "visual_crop_json": "debug/crops.json",
                "qgis_preprocessed_style": "debug/style.json",
                "combined_source_layers": [],
                "crops": [],
            }

            write_report(report, paths)

            self.assertEqual(json.loads(paths.json_path.read_text(encoding="utf-8"))["camera"], "test-camera")
            self.assertIn("# Mapbox Outdoors source/crop overlap", paths.summary_path.read_text(encoding="utf-8"))

    def test_collect_report_requires_crop_boxes_for_camera(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            visual_crop_json = _write_source_overlap_fixture(root, manual_crop_boxes={}, include_camera_crops=False)

            with self.assertRaisesRegex(ValueError, "no crop boxes"):
                collect_source_crop_overlap_report(
                    SourceCropOverlapConfig(
                        token="test-token",
                        visual_crop_json_path=visual_crop_json,
                        camera_name="test-camera",
                    ),
                    tile_fetcher=lambda _url: b"",
                    tile_decoder=lambda _payload, _tile: {},
                )

    def test_collect_report_rejects_untrusted_nested_artifact_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            visual_crop_json = _write_source_overlap_fixture(root)
            manifest_path = root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["outputs"]["qgis_preprocessed_style"] = "/etc/passwd"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "QGIS-preprocessed style path"):
                collect_source_crop_overlap_report(
                    SourceCropOverlapConfig(
                        token="test-token",
                        visual_crop_json_path=visual_crop_json,
                        camera_name="test-camera",
                    ),
                    tile_fetcher=lambda _url: b"",
                    tile_decoder=lambda _payload, _tile: {},
                )


def _feature(geometry_type, coordinates, properties):
    return {
        "geometry": {"type": geometry_type, "coordinates": coordinates},
        "properties": properties,
    }


def _write_source_overlap_fixture(root, *, manual_crop_boxes=None, include_camera_crops=True):
    camera_name = "test-camera"
    comparison_summary_path = root / "summary.json"
    manifest_path = root / "manifest.json"
    style_path = root / "qgis-preprocessed-style.json"
    visual_crop_json = root / "visual-crops.json"
    if manual_crop_boxes is None:
        manual_crop_boxes = {camera_name: [[240, 240, 272, 272]]}
    style_path.write_text(
        json.dumps(
            {
                "version": 8,
                "sources": {
                    "composite": {
                        "type": "vector",
                        "url": "mapbox://mapbox.mapbox-streets-v8,mapbox.mapbox-terrain-v2",
                    }
                },
                "layers": [
                    {
                        "id": "landuse-park",
                        "type": "fill",
                        "source-layer": "landuse",
                        "minzoom": 2,
                        "filter": ["==", ["get", "class"], "park"],
                        "paint": {"fill-color": "hsl(98, 55%, 70%)", "fill-opacity": 1.0},
                    },
                    {
                        "id": "landuse-park-high-zoom",
                        "type": "fill",
                        "source-layer": "landuse",
                        "minzoom": 3,
                        "filter": ["==", ["get", "class"], "park"],
                        "paint": {"fill-color": "hsl(98, 55%, 70%)", "fill-opacity": 1.0},
                    },
                    {
                        "id": "landuse-park-sized",
                        "type": "fill",
                        "source-layer": "landuse",
                        "minzoom": 2,
                        "filter": [
                            "all",
                            ["==", ["get", "class"], "park"],
                            [">=", ["to-number", ["get", "sizerank"]], 0],
                        ],
                        "paint": {"fill-color": "hsl(98, 55%, 70%)", "fill-opacity": 1.0},
                    },
                    {
                        "id": "landuse-cemetery",
                        "type": "fill",
                        "source-layer": "landuse",
                        "minzoom": 2,
                        "filter": ["==", ["get", "class"], "cemetery"],
                        "paint": {"fill-color": "hsl(98, 45%, 75%)", "fill-opacity": 1.0},
                    },
                    {
                        "id": "contour-minor",
                        "type": "line",
                        "source-layer": "contour",
                        "filter": ["match", ["get", "index"], [1, 2], True, False],
                        "paint": {"line-color": "hsl(60, 10%, 35%)", "line-opacity": 0.65},
                    },
                    {
                        "id": "contour-major",
                        "type": "line",
                        "source-layer": "contour",
                        "filter": ["match", ["get", "index"], [1, 2], False, True],
                        "paint": {"line-color": "hsl(60, 10%, 35%)", "line-opacity": 1.0},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(
            {
                "camera": {
                    "name": camera_name,
                    "longitude": 10.0,
                    "latitude": 10.0,
                    "zoom": 2.0,
                    "width": 512,
                    "height": 512,
                    "bearing": 0.0,
                    "pitch": 0.0,
                },
                "outputs": {"qgis_preprocessed_style": str(style_path)},
            }
        ),
        encoding="utf-8",
    )
    comparison_summary_path.write_text(
        json.dumps({"cameras": [{"camera": camera_name, "manifest": str(manifest_path)}]}),
        encoding="utf-8",
    )
    visual_crop_json.write_text(
        json.dumps(
            {
                "comparison_summary_json": str(comparison_summary_path),
                "manual_crop_boxes": manual_crop_boxes,
                "cameras": (
                    [{"camera": camera_name, "crops": [{"box": [240, 240, 272, 272]}]}]
                    if include_camera_crops
                    else []
                ),
            }
        ),
        encoding="utf-8",
    )
    return visual_crop_json
