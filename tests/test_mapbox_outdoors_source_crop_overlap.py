import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from tests import _path  # noqa: F401

from qfit.validation.mapbox_outdoors_source_crop_overlap import (
    SourceCropOverlapConfig,
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
        self.assertEqual(combined["landuse_overlay"]["overlap_feature_count"], 0)
        self.assertEqual(combined["contour"]["overlap_feature_count"], 2)
        self.assertEqual(combined["contour"]["property_counts"]["index"], {"10": 1, "1": 1})
        self.assertEqual(combined["contour"]["ele_range"], {"min": 1580.0, "max": 1600.0})

        markdown = build_summary_markdown(report)
        self.assertIn("| `landuse` | 2 | 1 | park=1 | park=1 | - | - |", markdown)
        self.assertIn("| `landuse_overlay` | 0 | 0 | - | - | - | - |", markdown)
        self.assertIn("| `contour` | 2 | 2 | - | - | 10=1, 1=1 | 1580-1600 |", markdown)

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
                "layers": [],
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
