import datetime as dt
import gzip
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from tests import _path  # noqa: F401

from qfit.validation import mapbox_outdoors_road_features as road_features
from qfit.validation.mapbox_outdoors_road_features import (
    RoadFeatureConfig,
    build_all_camera_road_feature_paths,
    build_all_camera_summary_markdown,
    build_parser,
    build_road_feature_paths,
    build_run_directory,
    build_summary_markdown,
    collect_all_camera_road_feature_report,
    collect_road_feature_report,
    is_level_crossing_candidate,
    is_oneway_arrow_candidate,
    is_path_line_candidate,
    is_pedestrian_line_candidate,
    is_pedestrian_polygon_candidate,
    is_road_exit_shield_candidate,
    is_road_intersection_candidate,
    is_road_number_shield_candidate,
    is_step_line_candidate,
    load_style_definition,
    main,
    resolve_mapbox_token,
    road_tile_record,
    write_all_camera_report,
    write_report,
)


def _feature(geometry_type, properties):
    return {
        "geometry": {"type": geometry_type, "coordinates": [[0, 0], [1, 1]]},
        "properties": properties,
    }


class MapboxOutdoorsRoadFeatureTests(unittest.TestCase):
    def test_top_count_labels_preserves_count_order_and_limit(self):
        labels = road_features._top_count_labels(
            {
                "trail": 2,
                "footway": 3,
                "piste": 1,
                "path": 4,
            }
        )

        self.assertEqual(labels, ["path=4", "footway=3", "trail=2"])

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
            output_root=Path("/tmp/roads"),
            camera_name="zermatt",
            now=dt.datetime(2026, 5, 18, 14, 5, tzinfo=dt.timezone.utc),
        )
        paths = build_road_feature_paths(run_dir)

        self.assertEqual(run_dir, Path("/tmp/roads/zermatt/20260518T140500Z"))
        self.assertEqual(paths.json_path, run_dir / "road-features.json")
        self.assertEqual(paths.summary_path, run_dir / "summary.md")

    def test_load_style_definition_requires_json_object(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            style_path = Path(tmpdir) / "style.json"
            style_path.write_text('{"version": 8}\n', encoding="utf-8")
            self.assertEqual(load_style_definition(style_path), {"version": 8})

            style_path.write_text('["not", "an", "object"]\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_style_definition(style_path)

    def test_candidate_filters_match_pedestrian_polygon_and_line_style_inputs(self):
        pedestrian_polygon = _feature(
            "Polygon",
            {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": 0},
        )
        path_polygon = _feature("MultiPolygon", {"class": "path", "type": "footway", "structure": "ford"})
        bridge_polygon = _feature("Polygon", {"class": "pedestrian", "type": "pedestrian", "structure": "bridge"})
        negative_layer_polygon = _feature(
            "Polygon",
            {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": -1},
        )
        pedestrian_line = _feature(
            "LineString",
            {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": 1},
        )
        negative_layer_line = _feature(
            "LineString",
            {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": -1},
        )
        path_line = _feature("MultiLineString", {"class": "path", "type": "footway", "structure": "none"})
        negative_layer_path_line = _feature(
            "LineString",
            {"class": "path", "type": "footway", "structure": "none", "layer": -1},
        )
        step_path_line = _feature("LineString", {"class": "path", "type": "steps", "structure": "none"})
        bridge_step_line = _feature("LineString", {"class": "path", "type": "steps", "structure": "bridge"})
        tunnel_step_line = _feature("LineString", {"class": "path", "type": "steps", "structure": "tunnel"})
        low_zoom_street_oneway = _feature("LineString", {"class": "street", "structure": "none", "oneway": "true"})
        high_zoom_track_oneway = _feature("LineString", {"class": "track", "structure": "none", "oneway": "true"})
        motorway_oneway = _feature("LineString", {"class": "motorway", "structure": "bridge", "oneway": "true"})
        bool_oneway = _feature("LineString", {"class": "street", "structure": "none", "oneway": True})
        unsupported_oneway_class = _feature("LineString", {"class": "path", "structure": "none", "oneway": "true"})
        road_intersection = _feature("Point", {"class": "intersection", "name": "Rue du Lac"})
        low_zoom_road_intersection = _feature("Point", {"class": "intersection", "name": "Rue du Lac"})
        unnamed_road_intersection = _feature("Point", {"class": "intersection"})
        line_road_intersection = _feature("LineString", {"class": "intersection", "name": "Rue du Lac"})
        level_crossing = _feature("Point", {"class": "level_crossing", "structure": "none", "layer": 0})
        low_zoom_level_crossing = _feature("Point", {"class": "level_crossing", "structure": "none"})
        line_level_crossing = _feature("LineString", {"class": "level_crossing", "structure": "none"})
        low_zoom_shield_point = _feature("Point", {"class": "primary", "reflen": "2", "len": 10})
        high_zoom_shield_point = _feature("Point", {"class": "primary", "reflen": "2", "len": 3000})
        low_zoom_shield_line = _feature("LineString", {"class": "primary", "reflen": 2, "len": 6000})
        z11_shield_line = _feature("LineString", {"class": "primary", "reflen": 2, "len": 6001})
        short_z11_shield_line = _feature("LineString", {"class": "primary", "reflen": 2, "len": 2500})
        z12_shield_line = _feature("LineString", {"class": "primary", "reflen": "2", "len": "2501"})
        z13_shield_line = _feature("LineString", {"class": "primary", "reflen": 2, "len": 1001})
        z14_shield_line = _feature("LineString", {"class": "primary", "reflen": 2, "len": 2501})
        missing_length_shield_line = _feature("LineString", {"class": "primary", "reflen": 2})
        service_shield_line = _feature("LineString", {"class": "service", "reflen": 2, "len": 6001})
        zero_reflen_shield_line = _feature("LineString", {"class": "primary", "reflen": 0, "len": 6001})
        long_reflen_shield_line = _feature("LineString", {"class": "primary", "reflen": 7, "len": 6001})
        bool_reflen_shield_line = _feature("LineString", {"class": "primary", "reflen": True, "len": 6001})
        road_exit_shield = _feature("Point", {"ref": "12", "reflen": 2})
        string_reflen_road_exit_shield = _feature("Point", {"ref": "A1", "reflen": "2"})
        low_zoom_road_exit_shield = _feature("Point", {"ref": "12", "reflen": 2})
        missing_reflen_road_exit_shield = _feature("Point", {"ref": "12"})
        zero_reflen_road_exit_shield = _feature("Point", {"ref": "12", "reflen": 0})
        long_reflen_road_exit_shield = _feature("Point", {"ref": "1234567890", "reflen": 10})
        bool_reflen_road_exit_shield = _feature("Point", {"ref": "12", "reflen": True})
        unsupported_step_line = _feature(
            "LineString",
            {"class": "path", "type": "steps", "structure": "unsupported"},
        )
        sidewalk_path_line = _feature("LineString", {"class": "path", "type": "sidewalk", "structure": "none"})
        crossing_path_line = _feature("LineString", {"class": "path", "type": "crossing", "structure": "none"})
        missing_structure_path_line = _feature("LineString", {"class": "path", "type": "footway"})

        self.assertTrue(is_pedestrian_polygon_candidate(pedestrian_polygon))
        self.assertTrue(is_pedestrian_polygon_candidate(path_polygon))
        self.assertFalse(is_pedestrian_polygon_candidate(bridge_polygon))
        self.assertFalse(is_pedestrian_polygon_candidate(negative_layer_polygon))
        self.assertFalse(is_pedestrian_polygon_candidate(pedestrian_line))
        self.assertTrue(is_pedestrian_line_candidate(pedestrian_line))
        self.assertFalse(is_pedestrian_line_candidate(negative_layer_line))
        self.assertFalse(is_pedestrian_line_candidate(path_line))
        self.assertTrue(is_path_line_candidate(path_line))
        self.assertFalse(is_path_line_candidate(negative_layer_path_line))
        self.assertFalse(is_path_line_candidate(step_path_line, tile_zoom=15))
        self.assertFalse(is_path_line_candidate(step_path_line, tile_zoom=16))
        self.assertTrue(is_step_line_candidate(step_path_line))
        self.assertTrue(is_step_line_candidate(bridge_step_line))
        self.assertTrue(is_step_line_candidate(tunnel_step_line))
        self.assertFalse(is_oneway_arrow_candidate(low_zoom_street_oneway, tile_zoom=15))
        self.assertTrue(is_oneway_arrow_candidate(low_zoom_street_oneway, tile_zoom=16))
        self.assertFalse(is_oneway_arrow_candidate(low_zoom_street_oneway))
        self.assertFalse(is_oneway_arrow_candidate(high_zoom_track_oneway, tile_zoom=15))
        self.assertTrue(is_oneway_arrow_candidate(high_zoom_track_oneway, tile_zoom=16))
        self.assertFalse(is_oneway_arrow_candidate(high_zoom_track_oneway))
        self.assertFalse(is_oneway_arrow_candidate(motorway_oneway, tile_zoom=15))
        self.assertTrue(is_oneway_arrow_candidate(motorway_oneway, tile_zoom=16))
        self.assertFalse(is_oneway_arrow_candidate(bool_oneway, tile_zoom=16))
        self.assertFalse(is_oneway_arrow_candidate(unsupported_oneway_class, tile_zoom=16))
        self.assertTrue(is_road_intersection_candidate(road_intersection, tile_zoom=15))
        self.assertFalse(is_road_intersection_candidate(low_zoom_road_intersection, tile_zoom=14))
        self.assertFalse(is_road_intersection_candidate(road_intersection))
        self.assertFalse(is_road_intersection_candidate(unnamed_road_intersection, tile_zoom=15))
        self.assertFalse(is_road_intersection_candidate(line_road_intersection, tile_zoom=15))
        self.assertTrue(is_level_crossing_candidate(level_crossing, tile_zoom=16))
        self.assertFalse(is_level_crossing_candidate(low_zoom_level_crossing, tile_zoom=15))
        self.assertFalse(is_level_crossing_candidate(level_crossing))
        self.assertFalse(is_level_crossing_candidate(line_level_crossing, tile_zoom=16))
        self.assertFalse(is_road_number_shield_candidate(low_zoom_shield_point, tile_zoom=5))
        self.assertTrue(is_road_number_shield_candidate(low_zoom_shield_point, tile_zoom=10))
        self.assertFalse(is_road_number_shield_candidate(high_zoom_shield_point, tile_zoom=11))
        self.assertFalse(is_road_number_shield_candidate(low_zoom_shield_line, tile_zoom=10))
        self.assertTrue(is_road_number_shield_candidate(z11_shield_line, tile_zoom=11))
        self.assertFalse(is_road_number_shield_candidate(short_z11_shield_line, tile_zoom=11))
        self.assertTrue(is_road_number_shield_candidate(z12_shield_line, tile_zoom=12))
        self.assertFalse(is_road_number_shield_candidate(z13_shield_line, tile_zoom=13))
        self.assertTrue(is_road_number_shield_candidate(z14_shield_line, tile_zoom=14))
        self.assertFalse(is_road_number_shield_candidate(missing_length_shield_line, tile_zoom=14))
        self.assertFalse(is_road_number_shield_candidate(service_shield_line, tile_zoom=14))
        self.assertFalse(is_road_number_shield_candidate(zero_reflen_shield_line, tile_zoom=14))
        self.assertFalse(is_road_number_shield_candidate(long_reflen_shield_line, tile_zoom=14))
        self.assertFalse(is_road_number_shield_candidate(bool_reflen_shield_line, tile_zoom=14))
        self.assertFalse(is_road_number_shield_candidate(z14_shield_line))
        self.assertTrue(is_road_exit_shield_candidate(road_exit_shield, tile_zoom=14))
        self.assertTrue(is_road_exit_shield_candidate(string_reflen_road_exit_shield, tile_zoom=14))
        self.assertFalse(is_road_exit_shield_candidate(low_zoom_road_exit_shield, tile_zoom=13))
        self.assertFalse(is_road_exit_shield_candidate(missing_reflen_road_exit_shield, tile_zoom=14))
        self.assertFalse(is_road_exit_shield_candidate(zero_reflen_road_exit_shield, tile_zoom=14))
        self.assertFalse(is_road_exit_shield_candidate(long_reflen_road_exit_shield, tile_zoom=14))
        self.assertFalse(is_road_exit_shield_candidate(bool_reflen_road_exit_shield, tile_zoom=14))
        self.assertFalse(is_road_exit_shield_candidate(road_exit_shield))
        self.assertFalse(is_step_line_candidate(unsupported_step_line))
        self.assertFalse(is_step_line_candidate(sidewalk_path_line))
        self.assertFalse(is_path_line_candidate(sidewalk_path_line, tile_zoom=15))
        self.assertTrue(is_path_line_candidate(sidewalk_path_line, tile_zoom=16))
        self.assertFalse(is_path_line_candidate(crossing_path_line, tile_zoom=15))
        self.assertTrue(is_path_line_candidate(crossing_path_line, tile_zoom=16))
        self.assertFalse(is_path_line_candidate(missing_structure_path_line))
        self.assertFalse(is_path_line_candidate(pedestrian_line))

    def test_road_tile_record_counts_pedestrian_polygons_and_line_candidates(self):
        road_features = [
            _feature(
                "Polygon",
                {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": 0, "surface": "paved"},
            ),
            _feature("MultiPolygon", {"class": "path", "type": "footway", "structure": "none", "surface": "unpaved"}),
            _feature("Polygon", {"class": "pedestrian", "type": "pedestrian", "structure": "bridge"}),
            _feature(
                "LineString",
                {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": 1, "surface": "paved"},
            ),
            _feature("LineString", {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": -1}),
            _feature("LineString", {"class": "path", "type": "footway", "structure": "none", "surface": "unpaved"}),
            _feature("MultiLineString", {"class": "path", "type": "steps", "structure": "none", "layer": 0, "surface": "paved"}),
            _feature("LineString", {"class": "street", "structure": "none", "layer": 0, "oneway": "true"}),
            _feature("LineString", {"class": "motorway", "structure": "bridge", "oneway": "true"}),
            _feature("Point", {"class": "intersection", "name": "A1"}),
            _feature("Point", {"class": "level_crossing", "structure": "none", "layer": 0}),
            _feature(
                "LineString",
                {
                    "class": "primary",
                    "reflen": "2",
                    "shield": "ch-primary",
                    "structure": "none",
                    "layer": 0,
                    "len": 3000,
                },
            ),
            _feature("LineString", {"class": "service", "reflen": 2, "structure": "none"}),
            _feature("LineString", {"class": "street", "type": "street", "structure": "none"}),
        ]
        motorway_junction_features = [
            _feature("Point", {"ref": "12", "reflen": 2}),
            _feature("Point", {"ref": "1234567890", "reflen": 10}),
        ]

        def decoder(_payload):
            return {
                "road": {"features": road_features},
                "motorway_junction": {"features": motorway_junction_features},
            }

        record = road_tile_record(
            tile={"z": 18, "x": 136712, "y": 93238},
            tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
            tile_fetcher=lambda _url: gzip.compress(b"tile"),
            tile_decoder=decoder,
        )

        self.assertEqual(record["status"], "decoded")
        self.assertEqual(record["road_feature_count"], 14)
        self.assertEqual(record["motorway_junction_feature_count"], 2)
        self.assertEqual(record["pedestrian_polygon_candidate_count"], 2)
        self.assertEqual(record["pedestrian_line_candidate_count"], 1)
        self.assertEqual(record["path_line_candidate_count"], 1)
        self.assertEqual(record["step_line_candidate_count"], 1)
        self.assertEqual(record["oneway_arrow_candidate_count"], 2)
        self.assertEqual(record["road_intersection_candidate_count"], 1)
        self.assertEqual(record["level_crossing_candidate_count"], 1)
        self.assertEqual(record["road_number_shield_candidate_count"], 1)
        self.assertEqual(record["road_exit_shield_candidate_count"], 1)
        self.assertEqual(record["pedestrian_polygon_class_counts"], {"path": 1, "pedestrian": 1})
        self.assertEqual(record["pedestrian_polygon_type_counts"], {"footway": 1, "pedestrian": 1})
        self.assertEqual(record["pedestrian_polygon_structure_counts"], {"none": 2})
        self.assertEqual(record["pedestrian_polygon_layer_counts"], {"(missing)": 1, "0": 1})
        self.assertEqual(record["pedestrian_polygon_surface_counts"], {"paved": 1, "unpaved": 1})
        self.assertEqual(
            record["pedestrian_polygon_signature_counts"],
            {
                "class=path; type=footway; surface=unpaved; structure=none; layer=(missing)": 1,
                "class=pedestrian; type=pedestrian; surface=paved; structure=none; layer=0": 1,
            },
        )
        self.assertEqual(record["pedestrian_line_type_counts"], {"pedestrian": 1})
        self.assertEqual(record["pedestrian_line_structure_counts"], {"none": 1})
        self.assertEqual(record["pedestrian_line_layer_counts"], {"1": 1})
        self.assertEqual(record["pedestrian_line_surface_counts"], {"paved": 1})
        self.assertEqual(
            record["pedestrian_line_signature_counts"],
            {"class=pedestrian; type=pedestrian; surface=paved; structure=none; layer=1": 1},
        )
        self.assertEqual(record["path_line_type_counts"], {"footway": 1})
        self.assertEqual(record["path_line_structure_counts"], {"none": 1})
        self.assertEqual(record["path_line_layer_counts"], {"(missing)": 1})
        self.assertEqual(record["path_line_surface_counts"], {"unpaved": 1})
        self.assertEqual(
            record["path_line_signature_counts"],
            {"class=path; type=footway; surface=unpaved; structure=none; layer=(missing)": 1},
        )
        self.assertEqual(record["step_line_structure_counts"], {"none": 1})
        self.assertEqual(record["step_line_layer_counts"], {"0": 1})
        self.assertEqual(record["step_line_surface_counts"], {"paved": 1})
        self.assertEqual(
            record["step_line_signature_counts"],
            {"class=path; type=steps; surface=paved; structure=none; layer=0": 1},
        )
        self.assertEqual(record["oneway_arrow_class_counts"], {"motorway": 1, "street": 1})
        self.assertEqual(record["oneway_arrow_structure_counts"], {"bridge": 1, "none": 1})
        self.assertEqual(record["oneway_arrow_layer_counts"], {"(missing)": 1, "0": 1})
        self.assertEqual(record["road_intersection_name_counts"], {"A1": 1})
        self.assertEqual(record["road_intersection_signature_counts"], {"class=intersection; name=A1": 1})
        self.assertEqual(record["level_crossing_structure_counts"], {"none": 1})
        self.assertEqual(record["level_crossing_layer_counts"], {"0": 1})
        self.assertEqual(
            record["level_crossing_signature_counts"],
            {"class=level_crossing; structure=none; layer=0": 1},
        )
        self.assertEqual(record["road_number_shield_class_counts"], {"primary": 1})
        self.assertEqual(record["road_number_shield_reflen_counts"], {"2": 1})
        self.assertEqual(record["road_number_shield_structure_counts"], {"none": 1})
        self.assertEqual(record["road_number_shield_layer_counts"], {"0": 1})
        self.assertEqual(
            record["road_number_shield_signature_counts"],
            {"class=primary; reflen=2; shield=ch-primary; shield_beta=(missing); structure=none; layer=0": 1},
        )
        self.assertEqual(record["road_exit_shield_reflen_counts"], {"2": 1})
        self.assertEqual(record["road_exit_shield_signature_counts"], {"ref=12; reflen=2": 1})
        self.assertEqual(record["sample_pedestrian_polygons"][0]["properties"]["class"], "pedestrian")
        self.assertEqual(record["sample_step_lines"][0]["properties"]["type"], "steps")
        self.assertEqual(record["sample_oneway_arrow_lines"][0]["properties"]["oneway"], "true")
        self.assertEqual(record["sample_road_intersections"][0]["properties"]["name"], "A1")
        self.assertEqual(record["sample_level_crossings"][0]["properties"]["class"], "level_crossing")
        self.assertEqual(record["sample_road_number_shields"][0]["properties"]["shield"], "ch-primary")
        self.assertEqual(record["sample_road_exit_shields"][0]["properties"]["ref"], "12")

    def test_road_tile_record_accepts_flat_layer_lists_and_summarizes_irregular_features(self):
        road_features = [
            {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 2]], [[2, 3], [4, 5]]],
                },
                "properties": {"class": "pedestrian", "type": ["plaza"], "structure": "none"},
            },
            {
                "geometry": {"type": "LineString", "coordinates": [7, 8]},
                "properties": {"class": "pedestrian", "type": {"kind": "lane"}, "structure": "none"},
            },
            {"geometry": {"type": "Polygon"}, "properties": {"class": "path", "structure": "none"}},
            {"properties": {"class": "street"}},
            "ignore-me",
        ]

        record = road_tile_record(
            tile={"z": 18, "x": 1, "y": 2},
            tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
            tile_fetcher=lambda _url: gzip.compress(b"tile"),
            tile_decoder=lambda _payload: {"road": road_features},
        )

        self.assertEqual(record["status"], "decoded")
        self.assertEqual(record["road_feature_count"], 4)
        self.assertEqual(record["motorway_junction_feature_count"], 0)
        self.assertEqual(record["pedestrian_polygon_candidate_count"], 2)
        self.assertEqual(record["pedestrian_line_candidate_count"], 1)
        self.assertEqual(record["oneway_arrow_candidate_count"], 0)
        self.assertEqual(record["road_number_shield_candidate_count"], 0)
        self.assertEqual(record["road_exit_shield_candidate_count"], 0)
        self.assertEqual(record["road_geometry_type_counts"]["(missing)"], 1)
        self.assertEqual(record["pedestrian_polygon_type_counts"], {'["plaza"]': 1, "(missing)": 1})
        self.assertEqual(record["pedestrian_polygon_structure_counts"], {"none": 2})
        self.assertEqual(record["pedestrian_polygon_layer_counts"], {"(missing)": 2})
        self.assertEqual(record["pedestrian_polygon_surface_counts"], {"(missing)": 2})
        self.assertEqual(record["pedestrian_line_type_counts"], {'{"kind":"lane"}': 1})
        self.assertEqual(record["pedestrian_line_structure_counts"], {"none": 1})
        self.assertEqual(record["pedestrian_line_layer_counts"], {"(missing)": 1})
        self.assertEqual(record["pedestrian_line_surface_counts"], {"(missing)": 1})
        self.assertEqual(record["sample_pedestrian_polygons"][0]["geometry"]["bounds"], [0.0, 0.0, 4.0, 5.0])
        self.assertEqual(record["sample_pedestrian_lines"][0]["geometry"]["point_count"], 1)

    def test_road_tile_record_treats_non_list_layer_as_empty(self):
        record = road_tile_record(
            tile={"z": 18, "x": 1, "y": 2},
            tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
            tile_fetcher=lambda _url: gzip.compress(b"tile"),
            tile_decoder=lambda _payload: {"road": "not-a-layer"},
        )

        self.assertEqual(record["status"], "decoded")
        self.assertEqual(record["road_feature_count"], 0)
        self.assertEqual(record["motorway_junction_feature_count"], 0)

    def test_road_tile_record_marks_decode_errors(self):
        record = road_tile_record(
            tile={"z": 18, "x": 1, "y": 2},
            tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
            tile_fetcher=lambda _url: b"not-gzip",
            tile_decoder=lambda _payload: (_ for _ in ()).throw(ValueError("bad tile")),
        )

        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error"], "ValueError")

    def test_collect_road_feature_report_uses_style_tiles_and_camera(self):
        generated = dt.datetime(2026, 5, 18, 14, 12, tzinfo=dt.timezone.utc)
        calls = []

        def style_fetcher(token, owner, style_id):
            self.assertEqual((token, owner, style_id), ("token", "mapbox", "outdoors-v12"))
            return {"sources": {"composite": {"type": "vector", "url": "mapbox://mapbox.mapbox-streets-v8"}}}

        def tile_fetcher(url):
            calls.append(url)
            return gzip.compress(b"tile")

        def decoder(_payload):
            return {
                "road": {
                    "features": [
                        _feature(
                            "Polygon",
                            {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": 0, "surface": "paved"},
                        ),
                        _feature(
                            "LineString",
                            {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": 0, "surface": "paved"},
                        ),
                        _feature(
                            "LineString",
                            {"class": "path", "type": "footway", "structure": "none", "surface": "unpaved"},
                        ),
                        _feature(
                            "LineString",
                            {"class": "path", "type": "steps", "structure": "bridge", "surface": "paved"},
                        ),
                        _feature("LineString", {"class": "street", "structure": "none", "layer": 0, "oneway": "true"}),
                    ]
                },
                "motorway_junction": {
                    "features": [_feature("Point", {"ref": "12", "reflen": 2})]
                },
            }

        report = collect_road_feature_report(
            RoadFeatureConfig(token="token", output_root=Path("/tmp"), tile_zoom=0, now=generated),
            style_fetcher=style_fetcher,
            tile_fetcher=tile_fetcher,
            tile_decoder=decoder,
        )

        self.assertEqual(report["generated"], "2026-05-18T14:12:00+00:00")
        self.assertEqual(report["camera"]["name"], "zermatt-trails-z18-outdoors")
        self.assertEqual(report["tile_zoom"], 0)
        self.assertEqual(report["tile_count"], 1)
        self.assertEqual(report["decoded_tile_count"], 1)
        self.assertEqual(report["road_feature_count"], 5)
        self.assertEqual(report["motorway_junction_feature_count"], 1)
        self.assertEqual(report["pedestrian_polygon_candidate_count"], 1)
        self.assertEqual(report["pedestrian_line_candidate_count"], 1)
        self.assertEqual(report["path_line_candidate_count"], 1)
        self.assertEqual(report["step_line_candidate_count"], 1)
        self.assertEqual(report["oneway_arrow_candidate_count"], 0)
        self.assertEqual(report["road_intersection_candidate_count"], 0)
        self.assertEqual(report["level_crossing_candidate_count"], 0)
        self.assertEqual(report["road_number_shield_candidate_count"], 0)
        self.assertEqual(report["road_exit_shield_candidate_count"], 0)
        self.assertEqual(report["pedestrian_polygon_type_counts"], {"pedestrian": 1})
        self.assertEqual(report["pedestrian_polygon_structure_counts"], {"none": 1})
        self.assertEqual(report["pedestrian_polygon_layer_counts"], {"0": 1})
        self.assertEqual(report["pedestrian_polygon_surface_counts"], {"paved": 1})
        self.assertEqual(
            report["pedestrian_polygon_signature_counts"],
            {"class=pedestrian; type=pedestrian; surface=paved; structure=none; layer=0": 1},
        )
        self.assertEqual(report["pedestrian_line_structure_counts"], {"none": 1})
        self.assertEqual(report["pedestrian_line_layer_counts"], {"0": 1})
        self.assertEqual(
            report["pedestrian_line_signature_counts"],
            {"class=pedestrian; type=pedestrian; surface=paved; structure=none; layer=0": 1},
        )
        self.assertEqual(report["path_line_type_counts"], {"footway": 1})
        self.assertEqual(report["path_line_structure_counts"], {"none": 1})
        self.assertEqual(report["path_line_layer_counts"], {"(missing)": 1})
        self.assertEqual(report["path_line_surface_counts"], {"unpaved": 1})
        self.assertEqual(
            report["path_line_signature_counts"],
            {"class=path; type=footway; surface=unpaved; structure=none; layer=(missing)": 1},
        )
        self.assertEqual(report["step_line_structure_counts"], {"bridge": 1})
        self.assertEqual(report["step_line_layer_counts"], {"(missing)": 1})
        self.assertEqual(report["step_line_surface_counts"], {"paved": 1})
        self.assertEqual(
            report["step_line_signature_counts"],
            {"class=path; type=steps; surface=paved; structure=bridge; layer=(missing)": 1},
        )
        self.assertEqual(report["oneway_arrow_class_counts"], {})
        self.assertEqual(report["oneway_arrow_structure_counts"], {})
        self.assertEqual(report["oneway_arrow_layer_counts"], {})
        self.assertEqual(report["road_intersection_name_counts"], {})
        self.assertEqual(report["road_intersection_signature_counts"], {})
        self.assertEqual(report["level_crossing_structure_counts"], {})
        self.assertEqual(report["level_crossing_layer_counts"], {})
        self.assertEqual(report["level_crossing_signature_counts"], {})
        self.assertEqual(report["road_number_shield_class_counts"], {})
        self.assertEqual(report["road_number_shield_reflen_counts"], {})
        self.assertEqual(report["road_number_shield_structure_counts"], {})
        self.assertEqual(report["road_number_shield_layer_counts"], {})
        self.assertEqual(report["road_exit_shield_reflen_counts"], {})
        self.assertEqual(len(calls), 1)
        self.assertIn("mapbox.mapbox-streets-v8/0/0/0.mvt", calls[0])

    def test_collect_road_feature_report_can_load_style_json_file(self):
        generated = dt.datetime(2026, 5, 18, 14, 12, tzinfo=dt.timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            style_path = Path(tmpdir) / "style.json"
            style_path.write_text(
                json.dumps({"sources": {"composite": {"type": "vector", "url": "mapbox://mapbox.mapbox-streets-v8"}}}),
                encoding="utf-8",
            )
            report = collect_road_feature_report(
                RoadFeatureConfig(
                    token="token",
                    output_root=Path(tmpdir),
                    style_json_path=style_path,
                    tile_zoom=0,
                    now=generated,
                ),
                tile_fetcher=lambda _url: gzip.compress(b"tile"),
                tile_decoder=lambda _payload: {"road": [], "motorway_junction": []},
            )

        self.assertEqual(report["tileset_ids"], ["mapbox.mapbox-streets-v8"])
        self.assertEqual(report["road_feature_count"], 0)
        self.assertEqual(report["motorway_junction_feature_count"], 0)

    def test_collect_road_feature_report_requires_token_for_style_and_tiles(self):
        with self.assertRaisesRegex(ValueError, "style-json"):
            collect_road_feature_report(
                RoadFeatureConfig(token=None, output_root=Path("/tmp"), tile_zoom=0),
                style_fetcher=lambda _token, _owner, _style_id: {},
                tile_fetcher=lambda _url: gzip.compress(b"tile"),
                tile_decoder=lambda _payload: {"road": []},
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            style_path = Path(tmpdir) / "style.json"
            style_path.write_text(
                json.dumps({"sources": {"composite": {"type": "vector", "url": "mapbox://mapbox.mapbox-streets-v8"}}}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "vector tiles"):
                collect_road_feature_report(
                    RoadFeatureConfig(token=None, output_root=Path(tmpdir), style_json_path=style_path, tile_zoom=0),
                    tile_fetcher=lambda _url: gzip.compress(b"tile"),
                    tile_decoder=lambda _payload: {"road": []},
                )
            with self.assertRaisesRegex(ValueError, "vector tiles"):
                collect_road_feature_report(
                    RoadFeatureConfig(token="", output_root=Path(tmpdir), style_json_path=style_path, tile_zoom=0),
                    tile_fetcher=lambda _url: gzip.compress(b"tile"),
                    tile_decoder=lambda _payload: {"road": []},
                )

    def test_collect_road_feature_report_rejects_unknown_camera(self):
        with self.assertRaisesRegex(ValueError, "Unknown comparison camera"):
            collect_road_feature_report(
                RoadFeatureConfig(token="token", output_root=Path("/tmp"), camera_name="missing-camera", tile_zoom=0),
                style_fetcher=lambda _token, _owner, _style_id: {
                    "sources": {"composite": {"type": "vector", "url": "mapbox://mapbox.mapbox-streets-v8"}}
                },
                tile_fetcher=lambda _url: gzip.compress(b"tile"),
                tile_decoder=lambda _payload: {"road": []},
            )

    def test_collect_all_camera_road_feature_report_aggregates_camera_counts(self):
        generated = dt.datetime(2026, 5, 18, 15, 40, tzinfo=dt.timezone.utc)
        style_calls = []

        def style_fetcher(_token, _owner, _style_id):
            style_calls.append((_token, _owner, _style_id))
            return {"sources": {"composite": {"type": "vector", "url": "mapbox://mapbox.mapbox-streets-v8"}}}

        def decoder(_payload):
            return {
                "road": [
                    _feature(
                        "Polygon",
                        {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": 0, "surface": "paved"},
                    ),
                    _feature(
                        "LineString",
                        {"class": "pedestrian", "type": "pedestrian", "structure": "none", "layer": 0, "surface": "paved"},
                    ),
                    _feature(
                        "LineString",
                        {"class": "path", "type": "footway", "structure": "none", "surface": "unpaved"},
                    ),
                    _feature(
                        "LineString",
                        {"class": "path", "type": "steps", "structure": "tunnel", "surface": "paved"},
                    ),
                    _feature("LineString", {"class": "street", "structure": "none", "layer": 0, "oneway": "true"}),
                ],
                "motorway_junction": [_feature("Point", {"ref": "12", "reflen": 2})],
            }

        report = collect_all_camera_road_feature_report(
            RoadFeatureConfig(token="token", output_root=Path("/tmp"), tile_zoom=0, now=generated),
            camera_names=("zermatt-trails-z18-outdoors", "chamonix-trails-z14-outdoors"),
            style_fetcher=style_fetcher,
            tile_fetcher=lambda _url: gzip.compress(b"tile"),
            tile_decoder=decoder,
        )

        self.assertEqual(report["generated"], "2026-05-18T15:40:00+00:00")
        self.assertEqual(report["camera_count"], 2)
        self.assertEqual(report["successful_camera_count"], 2)
        self.assertEqual(report["failed_camera_count"], 0)
        self.assertEqual(report["tile_count"], 2)
        self.assertEqual(report["decoded_tile_count"], 2)
        self.assertEqual(report["road_feature_count"], 10)
        self.assertEqual(report["motorway_junction_feature_count"], 2)
        self.assertEqual(report["pedestrian_polygon_candidate_count"], 2)
        self.assertEqual(report["pedestrian_line_candidate_count"], 2)
        self.assertEqual(report["path_line_candidate_count"], 2)
        self.assertEqual(report["step_line_candidate_count"], 2)
        self.assertEqual(report["oneway_arrow_candidate_count"], 0)
        self.assertEqual(report["road_intersection_candidate_count"], 0)
        self.assertEqual(report["level_crossing_candidate_count"], 0)
        self.assertEqual(report["road_number_shield_candidate_count"], 0)
        self.assertEqual(report["road_exit_shield_candidate_count"], 0)
        self.assertEqual(report["road_geometry_type_counts"], {"LineString": 8, "Polygon": 2})
        self.assertEqual(report["pedestrian_polygon_class_counts"], {"pedestrian": 2})
        self.assertEqual(report["pedestrian_polygon_type_counts"], {"pedestrian": 2})
        self.assertEqual(report["pedestrian_polygon_structure_counts"], {"none": 2})
        self.assertEqual(report["pedestrian_polygon_layer_counts"], {"0": 2})
        self.assertEqual(report["pedestrian_polygon_surface_counts"], {"paved": 2})
        self.assertEqual(
            report["pedestrian_polygon_signature_counts"],
            {"class=pedestrian; type=pedestrian; surface=paved; structure=none; layer=0": 2},
        )
        self.assertEqual(report["pedestrian_line_type_counts"], {"pedestrian": 2})
        self.assertEqual(report["pedestrian_line_structure_counts"], {"none": 2})
        self.assertEqual(report["pedestrian_line_layer_counts"], {"0": 2})
        self.assertEqual(report["pedestrian_line_surface_counts"], {"paved": 2})
        self.assertEqual(
            report["pedestrian_line_signature_counts"],
            {"class=pedestrian; type=pedestrian; surface=paved; structure=none; layer=0": 2},
        )
        self.assertEqual(report["path_line_type_counts"], {"footway": 2})
        self.assertEqual(report["path_line_structure_counts"], {"none": 2})
        self.assertEqual(report["path_line_layer_counts"], {"(missing)": 2})
        self.assertEqual(report["path_line_surface_counts"], {"unpaved": 2})
        self.assertEqual(
            report["path_line_signature_counts"],
            {"class=path; type=footway; surface=unpaved; structure=none; layer=(missing)": 2},
        )
        self.assertEqual(report["step_line_structure_counts"], {"tunnel": 2})
        self.assertEqual(report["step_line_layer_counts"], {"(missing)": 2})
        self.assertEqual(report["step_line_surface_counts"], {"paved": 2})
        self.assertEqual(
            report["step_line_signature_counts"],
            {"class=path; type=steps; surface=paved; structure=tunnel; layer=(missing)": 2},
        )
        self.assertEqual(report["oneway_arrow_class_counts"], {})
        self.assertEqual(report["oneway_arrow_structure_counts"], {})
        self.assertEqual(report["oneway_arrow_layer_counts"], {})
        self.assertEqual(report["road_intersection_name_counts"], {})
        self.assertEqual(report["road_intersection_signature_counts"], {})
        self.assertEqual(report["level_crossing_structure_counts"], {})
        self.assertEqual(report["level_crossing_layer_counts"], {})
        self.assertEqual(report["level_crossing_signature_counts"], {})
        self.assertEqual(report["road_number_shield_class_counts"], {})
        self.assertEqual(report["road_number_shield_reflen_counts"], {})
        self.assertEqual(report["road_number_shield_structure_counts"], {})
        self.assertEqual(report["road_number_shield_layer_counts"], {})
        self.assertEqual(report["road_exit_shield_reflen_counts"], {})
        self.assertEqual(style_calls, [("token", "mapbox", "outdoors-v12")])
        self.assertEqual(
            [camera_report["camera"] for camera_report in report["cameras"]],
            ["zermatt-trails-z18-outdoors", "chamonix-trails-z14-outdoors"],
        )
        self.assertEqual([camera_report["status"] for camera_report in report["cameras"]], ["decoded", "decoded"])
        self.assertNotIn("tiles", report["cameras"][0])

    def test_collect_all_camera_road_feature_report_keeps_camera_errors(self):
        generated = dt.datetime(2026, 5, 18, 15, 40, tzinfo=dt.timezone.utc)
        report = collect_all_camera_road_feature_report(
            RoadFeatureConfig(token="token", output_root=Path("/tmp"), tile_zoom=0, now=generated),
            camera_names=("zermatt-trails-z18-outdoors", "missing-camera"),
            style_fetcher=lambda _token, _owner, _style_id: {
                "sources": {"composite": {"type": "vector", "url": "mapbox://mapbox.mapbox-streets-v8"}}
            },
            tile_fetcher=lambda _url: gzip.compress(b"tile"),
            tile_decoder=lambda _payload: {"road": [], "motorway_junction": []},
        )

        self.assertEqual(report["camera_count"], 2)
        self.assertEqual(report["successful_camera_count"], 1)
        self.assertEqual(report["failed_camera_count"], 1)
        self.assertEqual(report["tile_count"], 1)
        self.assertEqual(report["decoded_tile_count"], 1)
        self.assertEqual(report["cameras"][1]["camera"], "missing-camera")
        self.assertEqual(report["cameras"][1]["status"], "error")
        self.assertEqual(report["cameras"][1]["error"], "ValueError")

    def test_build_summary_markdown_includes_counts_and_samples(self):
        pedestrian_signature = "class=pedestrian; type=pedestrian; surface=paved; structure=none; layer=0"
        path_signature = "class=path; type=footway; surface=unpaved; structure=none; layer=(missing)"
        step_signature = "class=path; type=steps; surface=paved; structure=none; layer=0"
        intersection_signature = "class=intersection; name=A1"
        level_crossing_signature = "class=level_crossing; structure=none; layer=0"
        shield_signature = "class=primary; reflen=2; shield=ch-primary; shield_beta=(missing); structure=none; layer=0"
        exit_shield_signature = "ref=12; reflen=2"
        report = {
            "generated": "2026-05-18T14:12:00+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "camera": {"name": "zermatt-trails-z18-outdoors"},
            "tile_zoom": 18,
            "decoded_tile_count": 1,
            "tile_count": 1,
            "road_feature_count": 4,
            "motorway_junction_feature_count": 1,
            "pedestrian_polygon_candidate_count": 1,
            "pedestrian_line_candidate_count": 1,
            "path_line_candidate_count": 1,
            "step_line_candidate_count": 1,
            "oneway_arrow_candidate_count": 1,
            "road_intersection_candidate_count": 1,
            "level_crossing_candidate_count": 1,
            "road_number_shield_candidate_count": 1,
            "road_exit_shield_candidate_count": 1,
            "pedestrian_polygon_type_counts": {"pedestrian": 1},
            "pedestrian_polygon_structure_counts": {"none": 1},
            "pedestrian_polygon_layer_counts": {"0": 1},
            "pedestrian_polygon_surface_counts": {"paved": 1},
            "pedestrian_polygon_signature_counts": {pedestrian_signature: 1},
            "pedestrian_line_type_counts": {"pedestrian": 1},
            "pedestrian_line_structure_counts": {"none": 1},
            "pedestrian_line_layer_counts": {"0": 1},
            "pedestrian_line_surface_counts": {"paved": 1},
            "pedestrian_line_signature_counts": {pedestrian_signature: 1},
            "path_line_type_counts": {"footway": 1},
            "path_line_structure_counts": {"none": 1},
            "path_line_layer_counts": {"(missing)": 1},
            "path_line_surface_counts": {"unpaved": 1},
            "path_line_signature_counts": {path_signature: 1},
            "step_line_structure_counts": {"none": 1},
            "step_line_layer_counts": {"0": 1},
            "step_line_surface_counts": {"paved": 1},
            "step_line_signature_counts": {step_signature: 1},
            "oneway_arrow_class_counts": {"street": 1},
            "oneway_arrow_structure_counts": {"none": 1},
            "oneway_arrow_layer_counts": {"0": 1},
            "road_intersection_name_counts": {"A1": 1},
            "road_intersection_signature_counts": {intersection_signature: 1},
            "level_crossing_structure_counts": {"none": 1},
            "level_crossing_layer_counts": {"0": 1},
            "level_crossing_signature_counts": {level_crossing_signature: 1},
            "road_number_shield_class_counts": {"primary": 1},
            "road_number_shield_reflen_counts": {"2": 1},
            "road_number_shield_structure_counts": {"none": 1},
            "road_number_shield_layer_counts": {"0": 1},
            "road_number_shield_signature_counts": {shield_signature: 1},
            "road_exit_shield_reflen_counts": {"2": 1},
            "road_exit_shield_signature_counts": {exit_shield_signature: 1},
            "sample_pedestrian_polygons": [{"properties": {"class": "pedestrian"}}],
            "sample_pedestrian_lines": [{"properties": {"class": "pedestrian"}}],
            "sample_path_lines": [{"properties": {"class": "path"}}],
            "sample_step_lines": [{"properties": {"type": "steps"}}],
            "sample_oneway_arrow_lines": [{"properties": {"class": "street", "oneway": "true"}}],
            "sample_road_intersections": [{"properties": {"class": "intersection", "name": "A1"}}],
            "sample_level_crossings": [{"properties": {"class": "level_crossing"}}],
            "sample_road_number_shields": [{"properties": {"class": "primary", "shield": "ch-primary"}}],
            "sample_road_exit_shields": [{"properties": {"ref": "12", "reflen": 2}}],
            "tiles": [
                "ignore-me",
                {
                    "z": 18,
                    "x": 1,
                    "y": 2,
                    "status": "decoded",
                    "road_feature_count": 4,
                    "motorway_junction_feature_count": 1,
                    "pedestrian_polygon_candidate_count": 1,
                    "pedestrian_line_candidate_count": 1,
                    "path_line_candidate_count": 1,
                    "step_line_candidate_count": 1,
                    "oneway_arrow_candidate_count": 1,
                    "road_intersection_candidate_count": 1,
                    "level_crossing_candidate_count": 1,
                    "road_number_shield_candidate_count": 1,
                    "road_exit_shield_candidate_count": 1,
                    "pedestrian_polygon_type_counts": {"pedestrian": 1},
                    "pedestrian_polygon_structure_counts": {"none": 1},
                    "pedestrian_polygon_layer_counts": {"0": 1},
                    "pedestrian_polygon_surface_counts": {"paved": 1},
                    "pedestrian_polygon_signature_counts": {pedestrian_signature: 1},
                    "pedestrian_line_type_counts": {"pedestrian": 1},
                    "pedestrian_line_structure_counts": {"none": 1},
                    "pedestrian_line_layer_counts": {"0": 1},
                    "pedestrian_line_surface_counts": {"paved": 1},
                    "pedestrian_line_signature_counts": {pedestrian_signature: 1},
                    "path_line_type_counts": {"footway": 1},
                    "path_line_structure_counts": {"none": 1},
                    "path_line_layer_counts": {"(missing)": 1},
                    "path_line_surface_counts": {"unpaved": 1},
                    "path_line_signature_counts": {path_signature: 1},
                    "step_line_structure_counts": {"none": 1},
                    "step_line_layer_counts": {"0": 1},
                    "step_line_surface_counts": {"paved": 1},
                    "step_line_signature_counts": {step_signature: 1},
                    "oneway_arrow_class_counts": {"street": 1},
                    "oneway_arrow_structure_counts": {"none": 1},
                    "oneway_arrow_layer_counts": {"0": 1},
                    "road_intersection_name_counts": {"A1": 1},
                    "road_intersection_signature_counts": {intersection_signature: 1},
                    "level_crossing_structure_counts": {"none": 1},
                    "level_crossing_layer_counts": {"0": 1},
                    "level_crossing_signature_counts": {level_crossing_signature: 1},
                    "road_number_shield_class_counts": {"primary": 1},
                    "road_number_shield_reflen_counts": {"2": 1},
                    "road_number_shield_structure_counts": {"none": 1},
                    "road_number_shield_layer_counts": {"0": 1},
                    "road_number_shield_signature_counts": {shield_signature: 1},
                    "road_exit_shield_reflen_counts": {"2": 1},
                    "road_exit_shield_signature_counts": {exit_shield_signature: 1},
                }
            ],
        }

        markdown = build_summary_markdown(report)

        self.assertIn("# Mapbox Outdoors road feature diagnostic - zermatt-trails-z18-outdoors", markdown)
        self.assertIn("Pedestrian/path polygon candidates: 1", markdown)
        self.assertIn("Path line candidates: 1", markdown)
        self.assertIn("Step line candidates: 1", markdown)
        self.assertIn("One-way arrow candidates: 1", markdown)
        self.assertIn("Road intersection candidates: 1", markdown)
        self.assertIn("Level crossing candidates: 1", markdown)
        self.assertIn("Road number shield candidates: 1", markdown)
        self.assertIn("Road exit shield candidates: 1", markdown)
        self.assertIn('Pedestrian polygon structure counts: {"none":1}', markdown)
        self.assertIn('Pedestrian polygon layer counts: {"0":1}', markdown)
        self.assertIn('Pedestrian polygon surface counts: {"paved":1}', markdown)
        self.assertIn(f'Pedestrian polygon signatures: {{"{pedestrian_signature}":1}}', markdown)
        self.assertIn('Pedestrian line structure counts: {"none":1}', markdown)
        self.assertIn('Pedestrian line layer counts: {"0":1}', markdown)
        self.assertIn('Pedestrian line surface counts: {"paved":1}', markdown)
        self.assertIn(f'Pedestrian line signatures: {{"{pedestrian_signature}":1}}', markdown)
        self.assertIn('Path line structure counts: {"none":1}', markdown)
        self.assertIn('Path line layer counts: {"(missing)":1}', markdown)
        self.assertIn('Path line surface counts: {"unpaved":1}', markdown)
        self.assertIn(f'Path line signatures: {{"{path_signature}":1}}', markdown)
        self.assertIn('Step line structure counts: {"none":1}', markdown)
        self.assertIn('Step line layer counts: {"0":1}', markdown)
        self.assertIn('Step line surface counts: {"paved":1}', markdown)
        self.assertIn(f'Step line signatures: {{"{step_signature}":1}}', markdown)
        self.assertIn('One-way arrow class counts: {"street":1}', markdown)
        self.assertIn('One-way arrow structure counts: {"none":1}', markdown)
        self.assertIn('One-way arrow layer counts: {"0":1}', markdown)
        self.assertIn('Road intersection name counts: {"A1":1}', markdown)
        self.assertIn(f'Road intersection signatures: {{"{intersection_signature}":1}}', markdown)
        self.assertIn('Level crossing structure counts: {"none":1}', markdown)
        self.assertIn('Level crossing layer counts: {"0":1}', markdown)
        self.assertIn(f'Level crossing signatures: {{"{level_crossing_signature}":1}}', markdown)
        self.assertIn('Road number shield class counts: {"primary":1}', markdown)
        self.assertIn('Road number shield reflen counts: {"2":1}', markdown)
        self.assertIn('Road number shield structure counts: {"none":1}', markdown)
        self.assertIn('Road number shield layer counts: {"0":1}', markdown)
        self.assertIn(f'Road number shield signatures: {{"{shield_signature}":1}}', markdown)
        self.assertIn('Road exit shield reflen counts: {"2":1}', markdown)
        self.assertIn(f'Road exit shield signatures: {{"{exit_shield_signature}":1}}', markdown)
        self.assertIn("## Sample pedestrian/path polygon candidates", markdown)
        self.assertIn("## Sample pedestrian line candidates", markdown)
        self.assertIn("## Sample path line candidates", markdown)
        self.assertIn("## Sample step line candidates", markdown)
        self.assertIn("## Sample one-way arrow candidates", markdown)
        self.assertIn("## Sample road intersection candidates", markdown)
        self.assertIn("## Sample level crossing candidates", markdown)
        self.assertIn("## Sample road number shield candidates", markdown)
        self.assertIn("## Sample road exit shield candidates", markdown)

    def test_build_all_camera_summary_markdown_includes_camera_rows(self):
        pedestrian_signature = "class=pedestrian; type=pedestrian; surface=paved; structure=none; layer=0"
        path_signature = "class=path; type=footway; surface=unpaved; structure=none; layer=(missing)"
        step_signature = "class=path; type=steps; surface=paved; structure=bridge; layer=(missing)"
        intersection_signature = "class=intersection; name=A1"
        level_crossing_signature = "class=level_crossing; structure=none; layer=0"
        shield_signature = "class=primary; reflen=2; shield=ch-primary; shield_beta=(missing); structure=none; layer=0"
        exit_shield_signature = "ref=12; reflen=2"
        report = {
            "generated": "2026-05-18T15:40:00+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "camera_count": 1,
            "successful_camera_count": 1,
            "failed_camera_count": 0,
            "decoded_tile_count": 1,
            "tile_count": 1,
            "road_feature_count": 4,
            "motorway_junction_feature_count": 1,
            "pedestrian_polygon_candidate_count": 1,
            "pedestrian_line_candidate_count": 1,
            "path_line_candidate_count": 1,
            "step_line_candidate_count": 1,
            "oneway_arrow_candidate_count": 1,
            "road_intersection_candidate_count": 1,
            "level_crossing_candidate_count": 1,
            "road_number_shield_candidate_count": 1,
            "road_exit_shield_candidate_count": 1,
            "pedestrian_polygon_type_counts": {"pedestrian": 1},
            "pedestrian_polygon_structure_counts": {"none": 1},
            "pedestrian_polygon_layer_counts": {"0": 1},
            "pedestrian_polygon_surface_counts": {"paved": 1},
            "pedestrian_polygon_signature_counts": {pedestrian_signature: 1},
            "pedestrian_line_type_counts": {"pedestrian": 1},
            "pedestrian_line_structure_counts": {"none": 1},
            "pedestrian_line_layer_counts": {"0": 1},
            "pedestrian_line_surface_counts": {"paved": 1},
            "pedestrian_line_signature_counts": {pedestrian_signature: 1},
            "path_line_type_counts": {"footway": 1},
            "path_line_structure_counts": {"none": 1},
            "path_line_layer_counts": {"(missing)": 1},
            "path_line_surface_counts": {"unpaved": 1},
            "path_line_signature_counts": {path_signature: 1},
            "step_line_structure_counts": {"bridge": 1},
            "step_line_layer_counts": {"(missing)": 1},
            "step_line_surface_counts": {"paved": 1},
            "step_line_signature_counts": {step_signature: 1},
            "oneway_arrow_class_counts": {"street": 1},
            "oneway_arrow_structure_counts": {"none": 1},
            "oneway_arrow_layer_counts": {"0": 1},
            "road_intersection_name_counts": {"A1": 1},
            "road_intersection_signature_counts": {intersection_signature: 1},
            "level_crossing_structure_counts": {"none": 1},
            "level_crossing_layer_counts": {"0": 1},
            "level_crossing_signature_counts": {level_crossing_signature: 1},
            "road_number_shield_class_counts": {"primary": 1},
            "road_number_shield_reflen_counts": {"2": 1},
            "road_number_shield_structure_counts": {"none": 1},
            "road_number_shield_layer_counts": {"0": 1},
            "road_number_shield_signature_counts": {shield_signature: 1},
            "road_exit_shield_reflen_counts": {"2": 1},
            "road_exit_shield_signature_counts": {exit_shield_signature: 1},
            "cameras": [
                {
                    "status": "decoded",
                    "camera": "zermatt-trails-z18-outdoors",
                    "camera_zoom": 18.0,
                    "tile_zoom": 18,
                    "decoded_tile_count": 1,
                    "failed_tile_count": 0,
                    "tile_count": 1,
                    "road_feature_count": 4,
                    "motorway_junction_feature_count": 1,
                    "pedestrian_polygon_candidate_count": 1,
                    "pedestrian_line_candidate_count": 1,
                    "path_line_candidate_count": 1,
                    "step_line_candidate_count": 1,
                    "oneway_arrow_candidate_count": 1,
                    "road_intersection_candidate_count": 1,
                    "level_crossing_candidate_count": 1,
                    "road_number_shield_candidate_count": 1,
                    "road_exit_shield_candidate_count": 1,
                    "pedestrian_polygon_type_counts": {"pedestrian": 1},
                    "pedestrian_polygon_structure_counts": {"none": 1},
                    "pedestrian_polygon_layer_counts": {"0": 1},
                    "pedestrian_polygon_surface_counts": {"paved": 1},
                    "pedestrian_polygon_signature_counts": {pedestrian_signature: 1},
                    "pedestrian_line_type_counts": {"pedestrian": 1},
                    "pedestrian_line_structure_counts": {"none": 1},
                    "pedestrian_line_layer_counts": {"0": 1},
                    "pedestrian_line_surface_counts": {"paved": 1},
                    "pedestrian_line_signature_counts": {pedestrian_signature: 1},
                    "path_line_type_counts": {"footway": 1},
                    "path_line_structure_counts": {"none": 1},
                    "path_line_layer_counts": {"(missing)": 1},
                    "path_line_surface_counts": {"unpaved": 1},
                    "path_line_signature_counts": {path_signature: 1},
                    "step_line_structure_counts": {"bridge": 1},
                    "step_line_layer_counts": {"(missing)": 1},
                    "step_line_surface_counts": {"paved": 1},
                    "step_line_signature_counts": {step_signature: 1},
                    "oneway_arrow_class_counts": {"street": 1},
                    "oneway_arrow_structure_counts": {"none": 1},
                    "oneway_arrow_layer_counts": {"0": 1},
                    "road_intersection_name_counts": {"A1": 1},
                    "road_intersection_signature_counts": {intersection_signature: 1},
                    "level_crossing_structure_counts": {"none": 1},
                    "level_crossing_layer_counts": {"0": 1},
                    "level_crossing_signature_counts": {level_crossing_signature: 1},
                    "road_number_shield_class_counts": {"primary": 1},
                    "road_number_shield_reflen_counts": {"2": 1},
                    "road_number_shield_structure_counts": {"none": 1},
                    "road_number_shield_layer_counts": {"0": 1},
                    "road_number_shield_signature_counts": {shield_signature: 1},
                    "road_exit_shield_reflen_counts": {"2": 1},
                    "road_exit_shield_signature_counts": {exit_shield_signature: 1},
                }
            ],
        }

        markdown = build_all_camera_summary_markdown(report)

        self.assertIn("# Mapbox Outdoors road feature diagnostic - all cameras", markdown)
        self.assertIn("Cameras: 1", markdown)
        self.assertIn('Camera statuses: {"decoded":1}', markdown)
        self.assertIn("| zermatt-trails-z18-outdoors | decoded | 18.0 | 18 | 1/1 | - | 4 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |", markdown)
        self.assertIn(f'Path line signatures: {{"{path_signature}":1}}', markdown)
        self.assertIn(f'Road exit shield signatures: {{"{exit_shield_signature}":1}}', markdown)
        self.assertIn("## Path/pedestrian focus", markdown)
        self.assertIn(
            '| zermatt-trails-z18-outdoors | 18.0 | 18 | 1 | 1 | 1 | 1 | ["pedestrian=1"] | ["footway=1"] | ["bridge=1"] |',
            markdown,
        )
        self.assertIn(f'["{path_signature}=1"] | ["{step_signature}=1"] |', markdown)

    def test_build_all_camera_summary_markdown_omits_path_pedestrian_focus_for_zero_counts(self):
        report = {
            "generated": "2026-05-18T15:40:00+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "camera_count": 1,
            "successful_camera_count": 1,
            "failed_camera_count": 0,
            "decoded_tile_count": 1,
            "tile_count": 1,
            "road_feature_count": 0,
            "motorway_junction_feature_count": 0,
            "pedestrian_polygon_candidate_count": 0,
            "pedestrian_line_candidate_count": 0,
            "path_line_candidate_count": 0,
            "step_line_candidate_count": 0,
            "oneway_arrow_candidate_count": 0,
            "road_intersection_candidate_count": 0,
            "level_crossing_candidate_count": 0,
            "road_number_shield_candidate_count": 0,
            "road_exit_shield_candidate_count": 0,
            "cameras": [
                {
                    "status": "decoded",
                    "camera": "switzerland-alps-z5-outdoors",
                    "camera_zoom": 5.35,
                    "tile_zoom": 5,
                    "decoded_tile_count": 1,
                    "failed_tile_count": 0,
                    "tile_count": 1,
                    "road_feature_count": 0,
                    "motorway_junction_feature_count": 0,
                    "pedestrian_polygon_candidate_count": 0,
                    "pedestrian_line_candidate_count": 0,
                    "path_line_candidate_count": 0,
                    "step_line_candidate_count": 0,
                    "oneway_arrow_candidate_count": 0,
                    "road_intersection_candidate_count": 0,
                    "level_crossing_candidate_count": 0,
                    "road_number_shield_candidate_count": 0,
                    "road_exit_shield_candidate_count": 0,
                }
            ],
        }

        markdown = build_all_camera_summary_markdown(report)

        self.assertNotIn("## Path/pedestrian focus", markdown)

    def test_write_report_writes_json_and_markdown(self):
        report = {
            "generated": "2026-05-18T14:12:00+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "camera": {"name": "zermatt-trails-z18-outdoors"},
            "tile_zoom": 18,
            "decoded_tile_count": 0,
            "tile_count": 0,
            "road_feature_count": 0,
            "pedestrian_polygon_candidate_count": 0,
            "pedestrian_line_candidate_count": 0,
            "path_line_candidate_count": 0,
            "step_line_candidate_count": 0,
            "tiles": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = build_road_feature_paths(Path(tmpdir) / "run")
            write_report(report, paths)

            self.assertEqual(json.loads(paths.json_path.read_text()), report)
            self.assertIn("Road features: 0", paths.summary_path.read_text())

    def test_write_all_camera_report_writes_json_and_markdown(self):
        report = {
            "generated": "2026-05-18T15:40:00+00:00",
            "style_owner": "mapbox",
            "style_id": "outdoors-v12",
            "camera_count": 0,
            "decoded_tile_count": 0,
            "tile_count": 0,
            "road_feature_count": 0,
            "pedestrian_polygon_candidate_count": 0,
            "pedestrian_line_candidate_count": 0,
            "path_line_candidate_count": 0,
            "step_line_candidate_count": 0,
            "cameras": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = build_all_camera_road_feature_paths(Path(tmpdir) / "run")
            write_all_camera_report(report, paths)

            self.assertEqual(json.loads(paths.json_path.read_text()), report)
            self.assertIn("all cameras", paths.summary_path.read_text())

    def test_private_helpers_cover_url_fetching_and_sample_limits(self):
        response = mock.Mock()
        response.read.return_value = b"tile"
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=None)
        with mock.patch("qfit.validation.mapbox_outdoors_contour_features.urlopen", return_value=response) as urlopen:
            self.assertEqual(road_features._fetch_url_bytes("https://example.test/tile.mvt"), b"tile")
        urlopen.assert_called_once_with("https://example.test/tile.mvt", timeout=20)

        self.assertEqual(road_features._merge_bounds([0, 0, 1, 1], None), [0, 0, 1, 1])
        self.assertEqual(road_features._combined_record_counts([{"counts": "bad"}], "counts"), {})
        many_samples = [{"sample": i} for i in range(road_features.MAX_SAMPLE_FEATURES + 3)]
        self.assertEqual(
            road_features._combined_samples([{"samples": many_samples}, {"samples": [{"sample": "extra"}]}], "samples"),
            many_samples[: road_features.MAX_SAMPLE_FEATURES],
        )

    def test_parser_and_main_wire_cli_arguments_to_report_output(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "camera-name",
                "--style-json",
                "/tmp/style.json",
                "--style-owner",
                "owner",
                "--style-id",
                "style",
                "--mapbox-token",
                "token",
                "--tile-zoom",
                "14",
                "--output-root",
                "/tmp/out",
            ]
        )
        self.assertEqual(args.camera, "camera-name")
        self.assertFalse(args.all_cameras)
        self.assertEqual(args.tile_zoom, 14)

        captured = {}

        def fake_collect(config):
            captured["config"] = config
            return {"camera": {"name": config.camera_name}, "tiles": []}

        def fake_write(report, paths):
            captured["report"] = report
            captured["paths"] = paths

        with tempfile.TemporaryDirectory() as tmpdir:
            stdout = io.StringIO()
            with (
                mock.patch.object(road_features, "collect_road_feature_report", side_effect=fake_collect),
                mock.patch.object(road_features, "write_report", side_effect=fake_write),
                redirect_stdout(stdout),
            ):
                result = main(["camera-name", "--mapbox-token", "token", "--tile-zoom", "14", "--output-root", tmpdir])

        self.assertEqual(result, 0)
        self.assertEqual(captured["config"].camera_name, "camera-name")
        self.assertEqual(captured["config"].tile_zoom, 14)
        self.assertEqual(captured["paths"].summary_path.name, "summary.md")
        self.assertIn("summary.md", stdout.getvalue())

    def test_main_wires_all_camera_mode_to_aggregate_report_output(self):
        captured = {}

        def fake_collect(config):
            captured["config"] = config
            return {"camera_count": 0, "cameras": []}

        def fake_write(report, paths):
            captured["report"] = report
            captured["paths"] = paths

        with tempfile.TemporaryDirectory() as tmpdir:
            stdout = io.StringIO()
            with (
                mock.patch.object(road_features, "collect_all_camera_road_feature_report", side_effect=fake_collect),
                mock.patch.object(road_features, "write_all_camera_report", side_effect=fake_write),
                redirect_stdout(stdout),
            ):
                result = main(["--all-cameras", "--mapbox-token", "token", "--output-root", tmpdir])

        self.assertEqual(result, 0)
        self.assertEqual(captured["config"].camera_name, road_features.DEFAULT_CAMERA_NAME)
        self.assertEqual(captured["report"], {"camera_count": 0, "cameras": []})
        self.assertEqual(captured["paths"].run_dir.parent.name, "all-cameras")
        self.assertIn("summary.md", stdout.getvalue())

    def test_main_rejects_single_camera_with_all_camera_mode(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = main(["camera-name", "--all-cameras"])

        self.assertEqual(result, 2)
        self.assertIn("either a single camera or --all-cameras", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
