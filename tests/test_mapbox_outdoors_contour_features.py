import datetime as dt
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from tests import _path  # noqa: F401

from qfit.validation.mapbox_outdoors_contour_features import (
    ContourFeatureConfig,
    build_contour_feature_paths,
    build_run_directory,
    build_summary_markdown,
    collect_contour_feature_report,
    contour_tile_record,
    decode_vector_tile_bytes,
    is_contour_label_candidate,
    iter_tile_coordinates,
    lon_lat_to_tile,
    recommended_tile_zoom,
    resolve_mapbox_token,
    tile_bounds_for_web_mercator_extent,
    web_mercator_to_lon_lat,
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
                        {"properties": {"ele": 1200, "index": 5, "class": "contour"}},
                        {"properties": {"ele": 1210, "index": 1}},
                        {"properties": {"ele": 1300, "index": "10", "extra": "kept-key"}},
                    ]
                }
            }

        record = contour_tile_record(
            tile={"z": 14, "x": 8504, "y": 5833},
            tile_url_template="https://example.test/{z}/{x}/{y}.mvt",
            tile_fetcher=lambda _url: gzip.compress(b"tile"),
            tile_decoder=decoder,
        )

        self.assertEqual(record["status"], "decoded")
        self.assertEqual(record["contour_feature_count"], 3)
        self.assertEqual(record["contour_label_candidate_count"], 2)
        self.assertEqual(record["index_counts"], {"1": 1, "10": 1, "5": 1})
        self.assertEqual(record["sample_candidates"][0]["ele"], 1200)
        self.assertEqual(record["sample_candidates"][1]["property_keys"], ["ele", "extra", "index"])

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
                        {"properties": {"ele": 1000, "index": 5}},
                        {"properties": {"ele": 1010, "index": 1}},
                        {"properties": {"ele": 1200, "index": 10}},
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
            "sample_candidates": [{"ele": 1000, "index": 5}],
            "tiles": [
                {
                    "z": 14,
                    "x": 8504,
                    "y": 5833,
                    "status": "decoded",
                    "contour_feature_count": 3,
                    "contour_label_candidate_count": 2,
                    "index_counts": {"1": 1, "5": 2},
                }
            ],
        }
        markdown = build_summary_markdown(report)

        self.assertIn("Contour-label candidates (index 5/10): 2", markdown)
        self.assertIn("| 14 | 8504 | 5833 | decoded | 3 | 2 |", markdown)
        self.assertIn("Sample contour-label candidates", markdown)

        with tempfile.TemporaryDirectory() as tmpdir:
            paths = build_contour_feature_paths(Path(tmpdir) / "run")
            write_report(report, paths)

            self.assertEqual(json.loads(paths.json_path.read_text(encoding="utf-8"))["tile_count"], 1)
            self.assertIn("Contour features: 3", paths.summary_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
