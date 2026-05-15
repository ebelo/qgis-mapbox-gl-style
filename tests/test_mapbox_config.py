import copy
import unittest
from unittest.mock import patch

import tests._path  # noqa: F401,E402

from mapbox_config import (  # noqa: E402
    DEFAULT_MAPBOX_RETINA,
    DEFAULT_MAPBOX_TILE_PIXEL_RATIO,
    DEFAULT_MAPBOX_TILE_SIZE,
    QGIS_TEXT_FONT_FALLBACK,
    TILE_MODE_RASTER,
    TILE_MODE_VECTOR,
    TILE_MODES,
    MapboxConfigError,
    build_background_layer_name,
    build_mapbox_sprite_file_url,
    build_mapbox_sprite_url,
    build_mapbox_style_json_url,
    build_mapbox_tiles_url,
    build_mapbox_vector_tiles_url,
    build_vector_tile_layer_uri,
    extract_mapbox_vector_source_ids,
    fetch_mapbox_sprite_resources,
    nearest_native_web_mercator_zoom_level,
    native_web_mercator_resolution_for_zoom,
    simplify_mapbox_style_expressions,
    build_xyz_layer_uri,
    preset_defaults,
    preset_requires_custom_style,
    resolve_background_style,
    snap_web_mercator_bounds_to_native_zoom,
)


class _FakeUrlResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


class MapboxConfigTests(unittest.TestCase):
    def test_builtin_preset_resolves_to_known_mapbox_style(self):
        self.assertEqual(resolve_background_style("Outdoor"), ("mapbox", "outdoors-v12"))
        self.assertEqual(resolve_background_style("Light"), ("mapbox", "light-v11"))
        self.assertEqual(resolve_background_style("Satellite"), ("mapbox", "satellite-streets-v12"))

    def test_custom_preset_requires_owner_and_style_id(self):
        with self.assertRaises(MapboxConfigError):
            resolve_background_style("Winter (custom style)", style_owner="", style_id="")

        self.assertEqual(
            resolve_background_style(
                "Winter (custom style)",
                style_owner="ebelo",
                style_id="winter-wonderland",
            ),
            ("ebelo", "winter-wonderland"),
        )

    def test_preset_helpers_expose_expected_defaults(self):
        self.assertEqual(preset_defaults("Light"), ("mapbox", "light-v11"))
        self.assertFalse(preset_requires_custom_style("Outdoor"))
        self.assertTrue(preset_requires_custom_style("Custom"))

    def test_tiles_url_is_built_with_encoded_components(self):
        url = build_mapbox_tiles_url(
            access_token="pk.test token",
            style_owner="my user",
            style_id="style/id",
            retina=False,
        )
        self.assertEqual(DEFAULT_MAPBOX_TILE_SIZE, 512)
        self.assertEqual(DEFAULT_MAPBOX_TILE_PIXEL_RATIO, 2)
        self.assertFalse(DEFAULT_MAPBOX_RETINA)
        self.assertIn("styles/v1/my%20user/style%2Fid/tiles/512/{z}/{x}/{y}", url)
        self.assertIn("access_token=pk.test%20token", url)
        self.assertNotIn("@2x", url)

    def test_xyz_uri_wraps_tiles_url(self):
        uri = build_xyz_layer_uri("pk.123", "mapbox", "outdoors-v12")
        self.assertTrue(uri.startswith("type=xyz&url=https://api.mapbox.com/"))
        # 512px tiles without @2x suffix — the 512px size already provides retina
        # density; @2x on top would over-request and cause blur via mis-scaled resampling.
        self.assertIn("tiles/512/{z}/{x}/{y}", uri)
        self.assertNotIn("@2x", uri)
        self.assertIn("zmin=0&zmax=22", uri)
        self.assertIn(f"tilePixelRatio={DEFAULT_MAPBOX_TILE_PIXEL_RATIO}", uri)

    def test_layer_name_prefers_preset_label(self):
        self.assertEqual(
            build_background_layer_name("Satellite", "mapbox", "satellite-streets-v12"),
            "qfit background — Satellite",
        )
        self.assertEqual(
            build_background_layer_name("Custom", "ebelo", "winter-wonderland"),
            "qfit background — ebelo/winter-wonderland",
        )

    def test_native_web_mercator_resolution_for_zoom_zero_matches_world_tile_width(self):
        self.assertAlmostEqual(
            native_web_mercator_resolution_for_zoom(0),
            40075016.685578488 / 512.0,
            places=6,
        )

    def test_nearest_native_zoom_level_picks_exact_match(self):
        zoom_level = nearest_native_web_mercator_zoom_level(
            native_web_mercator_resolution_for_zoom(12)
        )
        self.assertEqual(zoom_level, 12)

    def test_snap_web_mercator_bounds_to_native_zoom_preserves_center(self):
        original_bounds = (1000.0, 2000.0, 11280.0, 9680.0)
        snapped_bounds, snapped_zoom_level = snap_web_mercator_bounds_to_native_zoom(
            original_bounds,
            viewport_width_px=1024,
            viewport_height_px=768,
        )

        original_center = (
            (original_bounds[0] + original_bounds[2]) / 2.0,
            (original_bounds[1] + original_bounds[3]) / 2.0,
        )
        snapped_center = (
            (snapped_bounds[0] + snapped_bounds[2]) / 2.0,
            (snapped_bounds[1] + snapped_bounds[3]) / 2.0,
        )

        self.assertEqual(original_center, snapped_center)
        snapped_resolution = max(
            (snapped_bounds[2] - snapped_bounds[0]) / 1024.0,
            (snapped_bounds[3] - snapped_bounds[1]) / 768.0,
        )
        self.assertAlmostEqual(
            snapped_resolution,
            native_web_mercator_resolution_for_zoom(snapped_zoom_level),
            places=6,
        )

    def test_vector_tile_url_uses_v4_endpoint(self):
        url = build_mapbox_vector_tiles_url("pk.token", "mapbox", "outdoors-v12")
        self.assertIn("api.mapbox.com/v4/mapbox.outdoors-v12", url)
        self.assertIn("{z}/{x}/{y}.mvt", url)
        self.assertIn("access_token=pk.token", url)

    def test_style_json_url_uses_styles_endpoint(self):
        url = build_mapbox_style_json_url("pk.token", "mapbox", "outdoors-v12")
        self.assertIn("api.mapbox.com/styles/v1/mapbox/outdoors-v12", url)
        self.assertIn("access_token=pk.token", url)

    def test_sprite_url_uses_styles_endpoint(self):
        url = build_mapbox_sprite_url(
            "pk.test token",
            "my user",
            "style/id",
            file_type="png",
            retina=True,
        )

        self.assertIn("api.mapbox.com/styles/v1/my%20user/style%2Fid/sprite@2x.png", url)
        self.assertIn("access_token=pk.test%20token", url)

    def test_sprite_url_rejects_unknown_file_type(self):
        with self.assertRaises(MapboxConfigError):
            build_mapbox_sprite_url("pk.token", "mapbox", "outdoors-v12", file_type="svg")

    def test_sprite_file_url_uses_style_json_mapbox_sprite_reference(self):
        url = build_mapbox_sprite_file_url(
            "pk.token",
            "mapbox://sprites/shared-owner/shared-style",
            file_type="json",
            retina=True,
        )

        self.assertIn("api.mapbox.com/styles/v1/shared-owner/shared-style/sprite@2x.json", url)
        self.assertIn("access_token=pk.token", url)

    def test_sprite_file_url_preserves_immutable_mapbox_sprite_id(self):
        url = build_mapbox_sprite_file_url(
            "pk.token",
            "mapbox://sprites/shared-owner/shared-style/immutable-sprite-id",
            file_type="png",
        )

        self.assertIn(
            "api.mapbox.com/styles/v1/shared-owner/shared-style/immutable-sprite-id/sprite.png",
            url,
        )
        self.assertIn("access_token=pk.token", url)

    def test_sprite_file_url_appends_file_type_and_token_to_mapbox_https_url(self):
        url = build_mapbox_sprite_file_url(
            "pk.test token",
            "https://api.mapbox.com/styles/v1/shared-owner/shared-style/sprite?fresh=true",
            file_type="png",
        )

        self.assertEqual(
            url,
            "https://api.mapbox.com/styles/v1/shared-owner/shared-style/sprite.png"
            "?fresh=true&access_token=pk.test+token",
        )

    def test_sprite_file_url_preserves_existing_token(self):
        url = build_mapbox_sprite_file_url(
            "pk.replacement",
            "https://api.mapbox.com/styles/v1/shared-owner/shared-style/sprite?access_token=pk.embedded",
            file_type="json",
        )

        self.assertEqual(
            url,
            "https://api.mapbox.com/styles/v1/shared-owner/shared-style/sprite.json?access_token=pk.embedded",
        )

    def test_sprite_file_url_does_not_append_token_to_lookalike_hosts(self):
        url = build_mapbox_sprite_file_url(
            "pk.secret",
            "https://evilmapbox.com/styles/v1/shared-owner/shared-style/sprite",
            file_type="json",
        )

        self.assertEqual(
            url,
            "https://evilmapbox.com/styles/v1/shared-owner/shared-style/sprite.json",
        )

    def test_sprite_file_url_rejects_unknown_url_scheme(self):
        with self.assertRaises(MapboxConfigError):
            build_mapbox_sprite_file_url("pk.token", "ftp://example.test/sprite", file_type="json")

    def test_fetch_sprite_resources_fetches_definitions_and_image(self):
        responses = [
            _FakeUrlResponse(b'{"marker":{"x":0,"y":0,"width":12,"height":12}}'),
            _FakeUrlResponse(b"png-bytes"),
        ]

        with patch("mapbox_config.urlopen", side_effect=responses) as urlopen_mock:
            resources = fetch_mapbox_sprite_resources(
                "pk.token",
                "mapbox",
                "outdoors-v12",
                sprite_url="mapbox://sprites/shared-owner/shared-style",
            )

        self.assertEqual(resources.definitions, {"marker": {"x": 0, "y": 0, "width": 12, "height": 12}})
        self.assertEqual(resources.image_bytes, b"png-bytes")
        self.assertEqual(urlopen_mock.call_count, 2)
        self.assertIn("shared-owner/shared-style/sprite.json", urlopen_mock.call_args_list[0].args[0])

    def test_vector_tile_layer_uri_contains_both_urls(self):
        uri = build_vector_tile_layer_uri("pk.token", "mapbox", "outdoors-v12")
        self.assertTrue(uri.startswith("type=xyz&url="))
        self.assertIn("styleUrl=", uri)
        self.assertIn("zmin=0&zmax=22", uri)

    def test_tile_modes_constants_are_defined(self):
        self.assertEqual(TILE_MODE_RASTER, "Raster")
        self.assertEqual(TILE_MODE_VECTOR, "Vector")
        self.assertIn(TILE_MODE_RASTER, TILE_MODES)
        self.assertIn(TILE_MODE_VECTOR, TILE_MODES)

    def test_vector_tile_url_raises_on_missing_token(self):
        with self.assertRaises(MapboxConfigError):
            build_mapbox_vector_tiles_url("", "mapbox", "outdoors-v12")

    def test_style_json_url_raises_on_missing_style_id(self):
        with self.assertRaises(MapboxConfigError):
            build_mapbox_style_json_url("pk.token", "mapbox", "")


class VectorTileConfigTests(unittest.TestCase):
    def test_tile_modes_contains_raster_and_vector(self):
        self.assertIn(TILE_MODE_RASTER, TILE_MODES)
        self.assertIn(TILE_MODE_VECTOR, TILE_MODES)

    def test_vector_tiles_url_uses_mvt_endpoint(self):
        url = build_mapbox_vector_tiles_url("pk.abc", "mapbox", "outdoors-v12")
        self.assertIn("api.mapbox.com/v4/mapbox.outdoors-v12", url)
        self.assertIn(".mvt", url)
        self.assertIn("access_token=pk.abc", url)

    def test_vector_tiles_url_can_target_composite_tilesets(self):
        url = build_mapbox_vector_tiles_url(
            "pk.abc",
            "mapbox",
            "outdoors-v12",
            tileset_ids=["mapbox.mapbox-streets-v8", "mapbox.mapbox-terrain-v2"],
        )
        self.assertIn("api.mapbox.com/v4/mapbox.mapbox-streets-v8,mapbox.mapbox-terrain-v2", url)

    def test_style_json_url_uses_styles_endpoint(self):
        url = build_mapbox_style_json_url("pk.abc", "mapbox", "outdoors-v12")
        self.assertIn("api.mapbox.com/styles/v1/mapbox/outdoors-v12", url)
        self.assertIn("access_token=pk.abc", url)

    def test_vector_tile_layer_uri_contains_both_urls(self):
        uri = build_vector_tile_layer_uri(
            "pk.abc",
            "mapbox",
            "outdoors-v12",
            tileset_ids=["mapbox.mapbox-streets-v8", "mapbox.mapbox-terrain-v2"],
        )
        self.assertIn("type=xyz", uri)
        self.assertIn(".mvt", uri)
        self.assertIn("mapbox.mapbox-streets-v8,mapbox.mapbox-terrain-v2", uri)
        self.assertIn("styles/v1/mapbox/outdoors-v12", uri)

    def test_extract_mapbox_vector_source_ids_reads_composite_source(self):
        style = {
            "sources": {
                "composite": {
                    "type": "vector",
                    "url": "mapbox://mapbox.mapbox-streets-v8,mapbox.mapbox-terrain-v2,mapbox.mapbox-bathymetry-v2",
                }
            }
        }
        self.assertEqual(
            extract_mapbox_vector_source_ids(style),
            [
                "mapbox.mapbox-streets-v8",
                "mapbox.mapbox-terrain-v2",
                "mapbox.mapbox-bathymetry-v2",
            ],
        )

    def test_vector_urls_raise_on_missing_token(self):
        with self.assertRaises(MapboxConfigError):
            build_mapbox_vector_tiles_url("", "mapbox", "outdoors-v12")
        with self.assertRaises(MapboxConfigError):
            build_mapbox_style_json_url("", "mapbox", "outdoors-v12")
        with self.assertRaises(MapboxConfigError):
            extract_mapbox_vector_source_ids({"sources": {}})


class SimplifyMapboxStyleTests(unittest.TestCase):
    def test_literal_color_preserved(self):
        style = {"layers": [{"paint": {"line-color": "hsl(200, 50%, 60%)"}, "layout": {}}]}
        result = simplify_mapbox_style_expressions(style)
        self.assertEqual(result["layers"][0]["paint"]["line-color"], "hsl(200, 50%, 60%)")

    def test_match_expression_resolved_to_fallback(self):
        style = {
            "layers": [
                {
                    "paint": {
                        "line-color": ["match", ["get", "class"], "motorway", "hsl(15, 100%, 75%)", "hsl(35, 89%, 75%)"]
                    },
                    "layout": {},
                }
            ]
        }
        result = simplify_mapbox_style_expressions(style)
        # The last literal color in the match expression is the default
        self.assertEqual(result["layers"][0]["paint"]["line-color"], "hsl(35, 89%, 75%)")

    def test_interpolate_expression_resolved_to_representative_zoom_color(self):
        style = {
            "layers": [
                {
                    "paint": {
                        "line-color": ["interpolate", ["linear"], ["zoom"], 10, "hsl(75, 25%, 68%)", 16, "hsl(60, 0%, 75%)"]
                    },
                    "layout": {},
                }
            ]
        }
        result = simplify_mapbox_style_expressions(style)
        self.assertEqual(result["layers"][0]["paint"]["line-color"], "hsl(75, 25%, 68%)")

    def test_step_zoom_expression_resolved_to_representative_zoom_color(self):
        style = {
            "layers": [
                {
                    "paint": {
                        "line-color": [
                            "step",
                            ["zoom"],
                            "hsl(40, 20%, 90%)",
                            11,
                            "hsl(45, 30%, 80%)",
                            14,
                            "hsl(50, 40%, 70%)",
                        ]
                    },
                    "layout": {},
                }
            ]
        }
        result = simplify_mapbox_style_expressions(style)
        self.assertEqual(result["layers"][0]["paint"]["line-color"], "hsl(45, 30%, 80%)")

    def test_line_width_expressions_clamped_to_sane_range(self):
        style = {
            "layers": [
                {
                    "paint": {
                        # zoom interpolation: at z12 → ~3px, at z22 → 300px
                        "line-width": ["interpolate", ["exponential", 1.5], ["zoom"], 12, 3, 22, 300],
                        "line-color": "hsl(100, 50%, 60%)",
                    },
                    "layout": {},
                }
            ]
        }
        result = simplify_mapbox_style_expressions(style)
        # line-width should be simplified to a scalar and clamped ≤ max
        width = result["layers"][0]["paint"]["line-width"]
        self.assertIsInstance(width, float)
        self.assertLessEqual(width, 3.0)
        self.assertGreater(width, 0)
        # literal colors are untouched
        self.assertEqual(result["layers"][0]["paint"]["line-color"], "hsl(100, 50%, 60%)")

    def test_line_width_expression_uses_representative_zoom_stop(self):
        style = {
            "layers": [
                {
                    "paint": {
                        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1, 12, 4, 18, 12]
                    },
                    "layout": {},
                }
            ]
        }
        result = simplify_mapbox_style_expressions(style)
        width = result["layers"][0]["paint"]["line-width"]
        self.assertAlmostEqual(width, 4 * 25.4 / 96.0)

    def test_line_width_expression_uses_output_stop_for_property_interpolation(self):
        style = {
            "layers": [
                {
                    "paint": {
                        "line-width": ["interpolate", ["linear"], ["get", "rank"], 0, 8, 10, 16]
                    },
                    "layout": {},
                }
            ]
        }
        result = simplify_mapbox_style_expressions(style)
        width = result["layers"][0]["paint"]["line-width"]
        self.assertEqual(width, 3.0)

    def test_regional_major_road_widths_are_split_by_zoom_band(self):
        zoom_width = ["interpolate", ["linear"], ["zoom"], 3, 1, 5, 3, 6, 4, 12, 10]
        zoom_filter = [
            "step",
            ["zoom"],
            ["==", ["get", "class"], "motorway"],
            5,
            ["all", ["==", ["get", "class"], "motorway"], ["==", ["get", "structure"], "none"]],
        ]
        style = {
            "layers": [
                {
                    "id": "road-motorway-trunk",
                    "type": "line",
                    "minzoom": 3,
                    "filter": zoom_filter,
                    "paint": {"line-width": zoom_width},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        layers = result["layers"]
        self.assertEqual(
            [layer["id"] for layer in layers],
            [
                "road-motorway-trunk-z3-to-z5",
                "road-motorway-trunk-z5-to-z6",
                "road-motorway-trunk-z6-to-z9",
                "road-motorway-trunk-z9-to-z12",
                "road-motorway-trunk",
            ],
        )
        self.assertEqual((layers[0]["minzoom"], layers[0]["maxzoom"]), (3.0, 5.0))
        self.assertEqual((layers[1]["minzoom"], layers[1]["maxzoom"]), (5.0, 6.0))
        self.assertEqual((layers[2]["minzoom"], layers[2]["maxzoom"]), (6.0, 9.0))
        self.assertEqual((layers[3]["minzoom"], layers[3]["maxzoom"]), (9.0, 12.0))
        self.assertEqual(layers[4]["minzoom"], 12.0)
        self.assertNotIn("maxzoom", layers[4])
        self.assertAlmostEqual(layers[0]["paint"]["line-width"], 3 * 2.1 * 25.4 / 96.0)
        self.assertAlmostEqual(layers[1]["paint"]["line-width"], 4 * 2.1 * 25.4 / 96.0)
        self.assertEqual(layers[2]["paint"]["line-width"], 3.0)
        self.assertEqual(layers[3]["paint"]["line-width"], 3.0)
        self.assertAlmostEqual(layers[4]["paint"]["line-width"], 10 * 25.4 / 96.0)
        self.assertEqual(layers[0]["filter"], ["==", ["get", "class"], "motorway"])
        self.assertEqual(
            layers[1]["filter"],
            ["all", ["==", ["get", "class"], "motorway"], ["==", ["get", "structure"], "none"]],
        )
        for layer in layers[2:]:
            self.assertEqual(layer["filter"], layers[1]["filter"])

    def test_regional_primary_road_width_is_split_from_layer_minzoom(self):
        style = {
            "layers": [
                {
                    "id": "road-primary",
                    "type": "line",
                    "minzoom": 6,
                    "paint": {"line-width": ["interpolate", ["linear"], ["zoom"], 6, 2, 9, 5, 12, 8]},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        layers = result["layers"]
        self.assertEqual(
            [layer["id"] for layer in layers],
            ["road-primary-z6-to-z9", "road-primary-z9-to-z12", "road-primary"],
        )
        self.assertEqual((layers[0]["minzoom"], layers[0]["maxzoom"]), (6.0, 9.0))
        self.assertEqual((layers[1]["minzoom"], layers[1]["maxzoom"]), (9.0, 12.0))
        self.assertEqual(layers[2]["minzoom"], 12.0)
        self.assertAlmostEqual(layers[0]["paint"]["line-width"], 5 * 2.1 * 25.4 / 96.0)
        self.assertEqual(layers[1]["paint"]["line-width"], 3.0)
        self.assertAlmostEqual(layers[2]["paint"]["line-width"], 8 * 25.4 / 96.0)

    def test_regional_secondary_road_width_uses_lower_qgis_scale(self):
        style = {
            "layers": [
                {
                    "id": "road-secondary-tertiary",
                    "type": "line",
                    "minzoom": 9,
                    "paint": {"line-width": ["interpolate", ["linear"], ["zoom"], 9, 2, 12, 4]},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        layers = result["layers"]
        self.assertEqual(
            [layer["id"] for layer in layers],
            ["road-secondary-tertiary-z9-to-z12", "road-secondary-tertiary"],
        )
        self.assertAlmostEqual(layers[0]["paint"]["line-width"], 4 * 1.3 * 25.4 / 96.0)
        self.assertAlmostEqual(layers[1]["paint"]["line-width"], 4 * 25.4 / 96.0)

    def test_regional_road_width_split_preserves_layers_that_end_before_z12(self):
        style = {
            "layers": [
                {
                    "id": "road-primary",
                    "type": "line",
                    "minzoom": 6,
                    "maxzoom": 10,
                    "paint": {"line-width": ["interpolate", ["linear"], ["zoom"], 6, 2, 9, 5, 12, 8]},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        layers = result["layers"]
        self.assertEqual(
            [layer["id"] for layer in layers],
            ["road-primary-z6-to-z9", "road-primary-z9-to-z12"],
        )
        self.assertEqual((layers[0]["minzoom"], layers[0]["maxzoom"]), (6.0, 9.0))
        self.assertEqual((layers[1]["minzoom"], layers[1]["maxzoom"]), (9.0, 10.0))
        self.assertAlmostEqual(layers[0]["paint"]["line-width"], 5 * 2.1 * 25.4 / 96.0)
        self.assertEqual(layers[1]["paint"]["line-width"], 3.0)

    def test_regional_road_width_split_treats_missing_minzoom_as_open_lower_bound(self):
        style = {
            "layers": [
                {
                    "id": "road-motorway-trunk",
                    "type": "line",
                    "paint": {"line-width": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 3, 12, 10]},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        layers = result["layers"]
        self.assertEqual(
            [layer["id"] for layer in layers],
            [
                "road-motorway-trunk-z3-to-z5",
                "road-motorway-trunk-z5-to-z6",
                "road-motorway-trunk-z6-to-z9",
                "road-motorway-trunk-z9-to-z12",
                "road-motorway-trunk",
            ],
        )
        self.assertNotIn("minzoom", layers[0])
        self.assertEqual(layers[0]["maxzoom"], 5.0)
        self.assertAlmostEqual(layers[0]["paint"]["line-width"], 3 * 2.1 * 25.4 / 96.0)

    def test_regional_road_width_split_does_not_floor_line_offset(self):
        style = {
            "layers": [
                {
                    "id": "road-primary",
                    "type": "line",
                    "minzoom": 6,
                    "paint": {
                        "line-width": ["interpolate", ["linear"], ["zoom"], 6, 2, 12, 4],
                        "line-offset": ["interpolate", ["linear"], ["zoom"], 6, 0, 12, 0],
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        layers = result["layers"]
        self.assertAlmostEqual(layers[0]["paint"]["line-width"], 3 * 2.1 * 25.4 / 96.0)
        self.assertEqual(layers[0]["paint"]["line-offset"], 0.1)

    def test_regional_road_width_split_does_not_floor_line_gap_width(self):
        style = {
            "layers": [
                {
                    "id": "road-motorway-trunk-case",
                    "type": "line",
                    "minzoom": 3,
                    "paint": {
                        "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1, 12, 4],
                        "line-gap-width": ["interpolate", ["linear"], ["zoom"], 3, 0, 5, 1, 12, 4],
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        layers = result["layers"]
        self.assertGreater(layers[0]["paint"]["line-width"], 0.6)
        self.assertAlmostEqual(layers[0]["paint"]["line-gap-width"], 1 * 2.1 * 25.4 / 96.0)

    def test_regional_major_road_case_gap_width_uses_split_zoom_band(self):
        style = {
            "layers": [
                {
                    "id": "road-motorway-trunk-case",
                    "type": "line",
                    "minzoom": 3,
                    "paint": {
                        "line-width": ["interpolate", ["linear"], ["zoom"], 14, 1, 22, 2],
                        "line-gap-width": ["interpolate", ["linear"], ["zoom"], 3, 1, 6, 4, 12, 10],
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        layers = result["layers"]
        self.assertEqual(
            [layer["id"] for layer in layers],
            [
                "road-motorway-trunk-case-z3-to-z5",
                "road-motorway-trunk-case-z5-to-z6",
                "road-motorway-trunk-case-z6-to-z9",
                "road-motorway-trunk-case-z9-to-z12",
                "road-motorway-trunk-case",
            ],
        )
        self.assertAlmostEqual(layers[0]["paint"]["line-gap-width"], 3 * 2.1 * 25.4 / 96.0)
        self.assertAlmostEqual(layers[1]["paint"]["line-gap-width"], 4 * 2.1 * 25.4 / 96.0)
        self.assertEqual(layers[2]["paint"]["line-gap-width"], 3.0)
        self.assertEqual(layers[3]["paint"]["line-gap-width"], 3.0)
        self.assertAlmostEqual(layers[4]["paint"]["line-gap-width"], 10 * 25.4 / 96.0)
        for layer in layers[:4]:
            self.assertEqual(layer["paint"]["line-width"], 0.6)

    def test_full_line_opacity_expressions_simplify_to_scalar_default(self):
        style = {
            "layers": [
                {
                    "paint": {
                        "line-opacity": [
                            "interpolate",
                            ["linear"],
                            ["zoom"],
                            10,
                            0,
                            11,
                            1,
                        ]
                    },
                },
                {"paint": {"line-opacity": ["step", ["zoom"], 0, 11, 1]}},
                {"paint": {"line-opacity": ["match", ["get", "class"], "gate", 1, 1]}},
                {"paint": {"line-opacity": ["interpolate", ["linear"], ["zoom"], 13, 1, 14, 0.5]}},
                {"paint": {"line-opacity": ["case", ["has", "class"], 1, 1]}},
                {"paint": {"line-opacity": ["coalesce", 1, ["get", "opacity"]]}},
                {"paint": {"line-opacity": ["step", ["get", "rank"], 1, 10, 1]}},
                {"minzoom": 14, "paint": {"line-opacity": ["step", ["zoom"], 0, 14, 1]}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["paint"]["line-opacity"], 1.0)
        self.assertEqual(result["layers"][1]["minzoom"], 11.0)
        self.assertNotIn("line-opacity", result["layers"][1]["paint"])
        for index in range(2, 8):
            self.assertEqual(result["layers"][index]["paint"]["line-opacity"], 1.0)

    def test_zero_to_full_zoom_step_opacity_moves_visibility_to_minzoom(self):
        line_step = ["step", ["zoom"], 0, 14, 1]
        multi_stop_line_step = ["step", ["zoom"], 0, 13, 0, 14, 1]
        partial_before_full_step = ["step", ["zoom"], 0, 13, 0.5, 14, 1]
        partial_after_full_step = ["step", ["zoom"], 0, 14, 1, 16, 0.5]
        maxzoom_before_full_step = ["step", ["zoom"], 0, 14, 1]
        fill_step = ["step", ["zoom"], 0, 12, 1]
        style = {
            "layers": [
                {"paint": {"line-opacity": line_step}},
                {"minzoom": 13, "paint": {"line-opacity": line_step}},
                {"paint": {"line-opacity": multi_stop_line_step}},
                {"paint": {"line-opacity": partial_before_full_step}},
                {"paint": {"line-opacity": partial_after_full_step}},
                {"maxzoom": 14, "paint": {"line-opacity": maxzoom_before_full_step}},
                {"paint": {"fill-opacity": fill_step}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        for index in range(3):
            self.assertEqual(result["layers"][index]["minzoom"], 14.0)
            self.assertNotIn("line-opacity", result["layers"][index]["paint"])
        self.assertEqual(result["layers"][3]["paint"]["line-opacity"], partial_before_full_step)
        self.assertEqual(result["layers"][4]["paint"]["line-opacity"], partial_after_full_step)
        self.assertEqual(result["layers"][5]["paint"]["line-opacity"], maxzoom_before_full_step)
        self.assertEqual(result["layers"][6]["minzoom"], 12.0)
        self.assertNotIn("fill-opacity", result["layers"][6]["paint"])

    def test_non_full_line_opacity_expressions_are_left_unchanged(self):
        zoom_expression = ["interpolate", ["linear"], ["zoom"], 10, 0.4, 16, 0.7]
        ramping_zoom_expression = ["interpolate", ["linear"], ["zoom"], 8, 0, 14, 1]
        property_expression = ["interpolate", ["linear"], ["get", "rank"], 0, 0.5, 10, 1]
        short_interpolate = ["interpolate", ["linear"]]
        unsupported_interpolate = ["interpolate", ["linear"], ["zoom"], "low", 1]
        short_step = ["step"]
        unknown_expression = ["get", "opacity"]
        empty_expression = []
        short_match = ["match", ["get", "class"]]
        partial_match = ["match", ["get", "class"], "gate", 0.5, 1]
        partial_case = ["case", ["has", "class"], 0.5, 1]
        partial_data_step = ["step", ["get", "rank"], 1, 10, 0.5]
        unresolved_coalesce = ["coalesce", ["get", "opacity"], 1]
        partial_coalesce = ["coalesce", ["get", "opacity"], 0.5, 1]
        style = {
            "layers": [
                {"paint": {"line-opacity": zoom_expression}},
                {"paint": {"line-opacity": ramping_zoom_expression}},
                {"paint": {"line-opacity": property_expression}},
                {"paint": {"line-opacity": short_interpolate}},
                {"paint": {"line-opacity": unsupported_interpolate}},
                {"paint": {"line-opacity": short_step}},
                {"paint": {"line-opacity": unknown_expression}},
                {"paint": {"line-opacity": empty_expression}},
                {"paint": {"line-opacity": short_match}},
                {"paint": {"line-opacity": partial_match}},
                {"paint": {"line-opacity": partial_case}},
                {"paint": {"line-opacity": partial_data_step}},
                {"paint": {"line-opacity": unresolved_coalesce}},
                {"paint": {"line-opacity": partial_coalesce}},
                {"paint": {"line-opacity": True}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["paint"]["line-opacity"], zoom_expression)
        self.assertEqual(result["layers"][1]["paint"]["line-opacity"], ramping_zoom_expression)
        self.assertEqual(result["layers"][2]["paint"]["line-opacity"], property_expression)
        self.assertEqual(result["layers"][3]["paint"]["line-opacity"], short_interpolate)
        self.assertEqual(result["layers"][4]["paint"]["line-opacity"], unsupported_interpolate)
        self.assertEqual(result["layers"][5]["paint"]["line-opacity"], short_step)
        self.assertEqual(result["layers"][6]["paint"]["line-opacity"], unknown_expression)
        self.assertEqual(result["layers"][7]["paint"]["line-opacity"], empty_expression)
        self.assertEqual(result["layers"][8]["paint"]["line-opacity"], short_match)
        self.assertEqual(result["layers"][9]["paint"]["line-opacity"], partial_match)
        self.assertEqual(result["layers"][10]["paint"]["line-opacity"], partial_case)
        self.assertEqual(result["layers"][11]["paint"]["line-opacity"], partial_data_step)
        self.assertEqual(result["layers"][12]["paint"]["line-opacity"], unresolved_coalesce)
        self.assertEqual(result["layers"][13]["paint"]["line-opacity"], partial_coalesce)
        self.assertIs(result["layers"][14]["paint"]["line-opacity"], True)

    def test_full_fill_opacity_expressions_simplify_to_scalar_default(self):
        style = {
            "layers": [
                {"paint": {"fill-opacity": ["interpolate", ["linear"], ["zoom"], 10, 0, 11, 1]}},
                {"paint": {"fill-opacity": ["step", ["zoom"], 0, 11, 1]}},
                {"paint": {"fill-opacity": ["match", ["get", "class"], "wetland", 1, 1]}},
                {"paint": {"fill-opacity": ["case", ["has", "class"], 1, 1]}},
                {"minzoom": 14, "paint": {"fill-opacity": ["step", ["zoom"], 0, 14, 1]}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["paint"]["fill-opacity"], 1.0)
        self.assertEqual(result["layers"][1]["minzoom"], 11.0)
        self.assertNotIn("fill-opacity", result["layers"][1]["paint"])
        for index in range(2, 5):
            self.assertEqual(result["layers"][index]["paint"]["fill-opacity"], 1.0)

    def test_non_full_fill_opacity_expressions_are_left_unchanged(self):
        partial_zoom_expression = ["interpolate", ["linear"], ["zoom"], 15, 0, 16, 1]
        partial_match = ["match", ["get", "class"], "wetland", 0.5, 1]
        partial_case = ["case", ["has", "class"], 0.5, 1]
        property_expression = ["interpolate", ["linear"], ["get", "rank"], 0, 0.5, 10, 1]
        style = {
            "layers": [
                {"paint": {"fill-opacity": partial_zoom_expression}},
                {"paint": {"fill-opacity": partial_match}},
                {"paint": {"fill-opacity": partial_case}},
                {"paint": {"fill-opacity": property_expression}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["paint"]["fill-opacity"], partial_zoom_expression)
        self.assertEqual(result["layers"][1]["paint"]["fill-opacity"], partial_match)
        self.assertEqual(result["layers"][2]["paint"]["fill-opacity"], partial_case)
        self.assertEqual(result["layers"][3]["paint"]["fill-opacity"], property_expression)

    def test_icon_image_placeholders_and_literal_zoom_steps_are_simplified(self):
        empty_output_step = ["step", ["zoom"], "dot-11", 8, ""]
        empty_representative_step = ["step", ["zoom"], ["case", ["==", ["get", "capital"], 2], "dot-11", "dot-9"], 8, ""]
        data_driven_step = [
            "step",
            ["zoom"],
            "shield-small",
            12,
            ["concat", ["get", "shield"], "-", ["to-string", ["get", "reflen"]]],
        ]
        data_driven_high_zoom_step = [
            "step",
            ["zoom"],
            "shield-small",
            13,
            ["concat", ["get", "shield"], "-", ["to-string", ["get", "reflen"]]],
        ]
        empty_then_data_driven_high_zoom_step = [
            "step",
            ["zoom"],
            "",
            13,
            ["concat", ["get", "shield"], "-", ["to-string", ["get", "reflen"]]],
        ]
        style = {
            "layers": [
                {"layout": {"icon-image": "", "text-field": "Country"}},
                {"layout": {"icon-image": "marker"}},
                {"layout": {"icon-image": ["get", "maki"]}},
                {"layout": {"icon-image": ["step", ["zoom"], "oneway-small", 18, "oneway-large"]}},
                {"minzoom": 14, "layout": {"icon-image": ["step", ["zoom"], "oneway-small", 13, "oneway-large"]}},
                {"maxzoom": 11, "layout": {"icon-image": ["step", ["zoom"], "zoom-low", 10, "zoom-mid", 12, "zoom-high"]}},
                {"layout": {"icon-image": empty_output_step}},
                {"layout": {"icon-image": empty_representative_step}},
                {"layout": {"icon-image": data_driven_step}},
                {"layout": {"icon-image": data_driven_high_zoom_step}},
                {"layout": {"icon-image": empty_then_data_driven_high_zoom_step}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertNotIn("icon-image", result["layers"][0]["layout"])
        self.assertEqual(result["layers"][0]["layout"]["text-field"], "Country")
        self.assertEqual(result["layers"][1]["layout"]["icon-image"], "marker")
        self.assertEqual(result["layers"][2]["layout"]["icon-image"], ["get", "maki"])
        self.assertEqual(result["layers"][3]["layout"]["icon-image"], "oneway-small")
        self.assertEqual(result["layers"][4]["layout"]["icon-image"], "oneway-large")
        self.assertEqual(result["layers"][5]["layout"]["icon-image"], "zoom-mid")
        self.assertNotIn("icon-image", result["layers"][6]["layout"])
        self.assertNotIn("icon-image", result["layers"][7]["layout"])
        self.assertEqual(result["layers"][8]["layout"]["icon-image"], data_driven_step)
        self.assertEqual(result["layers"][9]["layout"]["icon-image"], data_driven_high_zoom_step)
        self.assertEqual(result["layers"][10]["layout"]["icon-image"], empty_then_data_driven_high_zoom_step)

    def test_gate_label_icon_match_uses_existing_sprite_fallback(self):
        gate_icon = ["match", ["get", "type"], "gate", "gate", "lift_gate", "lift-gate", ""]
        generic_empty_fallback = ["match", ["get", "type"], "gate", "gate", "lift_gate", "lift-gate", ""]
        mixed_output_fallback = ["match", ["get", "type"], "gate", ["get", "maki"], "lift_gate", "lift-gate", ""]
        style = {
            "layers": [
                {"id": "gate-label", "layout": {"icon-image": gate_icon}},
                {"id": "other-label", "layout": {"icon-image": generic_empty_fallback}},
                {"id": "gate-label", "layout": {"icon-image": mixed_output_fallback}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["layout"]["icon-image"],
            ["match", ["get", "type"], "gate", "gate", "lift_gate", "lift-gate", "gate"],
        )
        self.assertEqual(result["layers"][1]["layout"]["icon-image"], generic_empty_fallback)
        self.assertEqual(result["layers"][2]["layout"]["icon-image"], mixed_output_fallback)

    def test_maki_icon_get_uses_existing_sprite_match_fallback(self):
        maki_icon = ["get", "maki"]
        other_field_icon = ["get", "network"]
        style = {
            "layers": [
                {"id": "airport-label", "layout": {"icon-image": maki_icon}},
                {"id": "natural-point-label", "layout": {"icon-image": maki_icon}},
                {"id": "airport-label", "layout": {"icon-image": other_field_icon}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["layout"]["icon-image"],
            [
                "match",
                ["get", "maki"],
                "airport",
                "airport",
                "airfield",
                "airfield",
                "heliport",
                "heliport",
                "rocket",
                "rocket",
                "airport",
            ],
        )
        self.assertEqual(
            result["layers"][1]["layout"]["icon-image"],
            [
                "match",
                ["get", "maki"],
                "marker",
                "marker",
                "mountain",
                "mountain",
                "volcano",
                "volcano",
                "waterfall",
                "waterfall",
                "marker",
            ],
        )
        self.assertEqual(result["layers"][2]["layout"]["icon-image"], other_field_icon)

    def test_poi_label_icon_image_uses_audited_maki_sprite_match_fallback(self):
        poi_icon = [
            "case",
            ["has", "maki_beta"],
            ["coalesce", ["image", ["get", "maki_beta"]], ["image", ["get", "maki"]]],
            ["image", ["get", "maki"]],
        ]
        original_poi_icon = copy.deepcopy(poi_icon)
        style = {
            "layers": [
                {"id": "poi-label-z17-plus", "layout": {"icon-image": poi_icon}},
                {"id": "other-label", "layout": {"icon-image": copy.deepcopy(poi_icon)}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        replacement = result["layers"][0]["layout"]["icon-image"]
        self.assertEqual(replacement[:2], ["match", ["get", "maki"]])
        self.assertIn("lodging", replacement)
        self.assertIn("restaurant", replacement)
        self.assertIn("fuel", replacement)
        self.assertIn("parking", replacement)
        self.assertIn("zoo", replacement)
        self.assertNotIn("terminal", replacement)
        self.assertEqual(replacement[-1], "marker")
        self.assertEqual(result["layers"][1]["layout"]["icon-image"], original_poi_icon)
        self.assertEqual(poi_icon, original_poi_icon)

    def test_transit_label_network_icon_get_uses_maki_sprite_match_fallback(self):
        network_icon = ["get", "network"]
        style = {
            "layers": [
                {"id": "transit-label", "layout": {"icon-image": network_icon}},
                {"id": "road-label", "layout": {"icon-image": network_icon}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["layout"]["icon-image"],
            [
                "match",
                ["get", "maki"],
                "bicycle-share",
                "bicycle-share",
                "bus",
                "bus",
                "entrance",
                "entrance",
                "ferry",
                "ferry",
                "rail",
                "rail",
                "rail-light",
                "rail-light",
                "rail-metro",
                "rail-metro",
                "rail",
            ],
        )
        self.assertEqual(result["layers"][1]["layout"]["icon-image"], network_icon)

    def test_transit_label_non_entrance_layout_is_literalized(self):
        text_anchor = ["match", ["get", "stop_type"], "entrance", "left", "top"]
        text_justify = ["match", ["get", "stop_type"], "entrance", "left", "center"]
        text_offset = [
            "match",
            ["get", "stop_type"],
            "entrance",
            ["literal", [1, 0]],
            ["literal", [0, 0.8]],
        ]
        style = {
            "layers": [
                {
                    "id": "transit-label",
                    "filter": ["all", ["!=", ["get", "stop_type"], "entrance"]],
                    "layout": {
                        "text-anchor": text_anchor,
                        "text-justify": text_justify,
                        "text-offset": text_offset,
                    },
                },
                {
                    "id": "transit-label",
                    "layout": {
                        "text-anchor": copy.deepcopy(text_anchor),
                        "text-justify": copy.deepcopy(text_justify),
                        "text-offset": copy.deepcopy(text_offset),
                    },
                },
                {
                    "id": "poi-label",
                    "filter": ["all", ["!=", ["get", "stop_type"], "entrance"]],
                    "layout": {"text-anchor": copy.deepcopy(text_anchor)},
                },
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        transit_layout = result["layers"][0]["layout"]
        self.assertEqual(transit_layout["text-anchor"], "top")
        self.assertEqual(transit_layout["text-justify"], "center")
        self.assertEqual(transit_layout["text-offset"], [0, 0.8])
        self.assertEqual(result["layers"][1]["layout"]["text-anchor"], text_anchor)
        self.assertEqual(result["layers"][1]["layout"]["text-justify"], text_justify)
        self.assertEqual(result["layers"][1]["layout"]["text-offset"], text_offset)
        self.assertEqual(result["layers"][2]["layout"]["text-anchor"], text_anchor)

    def test_road_exit_shield_concat_icon_uses_reflen_sprite_match_fallback(self):
        exit_icon = ["concat", "motorway-exit-", ["to-string", ["get", "reflen"]]]
        other_concat_icon = ["concat", "motorway-exit-", ["get", "reflen"]]
        style = {
            "layers": [
                {"id": "road-exit-shield", "layout": {"icon-image": exit_icon}},
                {"id": "road-number-shield", "layout": {"icon-image": exit_icon}},
                {"id": "road-exit-shield", "layout": {"icon-image": other_concat_icon}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["layout"]["icon-image"],
            [
                "match",
                ["get", "reflen"],
                1,
                "motorway-exit-1",
                2,
                "motorway-exit-2",
                3,
                "motorway-exit-3",
                4,
                "motorway-exit-4",
                5,
                "motorway-exit-5",
                6,
                "motorway-exit-6",
                7,
                "motorway-exit-7",
                8,
                "motorway-exit-8",
                9,
                "motorway-exit-9",
                "motorway-exit-1",
            ],
        )
        self.assertEqual(result["layers"][1]["layout"]["icon-image"], exit_icon)
        self.assertEqual(result["layers"][2]["layout"]["icon-image"], other_concat_icon)

    def test_road_number_shield_icon_case_expands_to_reflen_sprite_matches(self):
        shield_icon = [
            "case",
            ["has", "shield_beta"],
            [
                "coalesce",
                ["image", ["concat", ["get", "shield_beta"], "-", ["to-string", ["get", "reflen"]]]],
                ["image", ["concat", "default-", ["to-string", ["get", "reflen"]]]],
            ],
            ["concat", ["get", "shield"], "-", ["to-string", ["get", "reflen"]]],
        ]
        style = {
            "layers": [
                {"id": "before", "type": "background"},
                {
                    "id": "road-number-shield",
                    "type": "symbol",
                    "filter": ["all", ["has", "reflen"], ["<=", ["get", "reflen"], 6]],
                    "layout": {
                        "icon-image": shield_icon,
                        "symbol-placement": ["step", ["zoom"], "point", 11, "line"],
                        "symbol-spacing": ["interpolate", ["linear"], ["zoom"], 11, 400, 14, 600],
                        "text-field": ["get", "ref"],
                    },
                    "paint": {"text-color": "hsl(0, 0%, 0%)"},
                },
                {"id": "after", "type": "symbol", "layout": {"icon-image": shield_icon}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            [layer["id"] for layer in result["layers"]],
            [
                "before",
                "road-number-shield-2-beta",
                "road-number-shield-2",
                "road-number-shield-3-beta",
                "road-number-shield-3",
                "road-number-shield-4-beta",
                "road-number-shield-4",
                "road-number-shield-5-beta",
                "road-number-shield-5",
                "road-number-shield-6-beta",
                "road-number-shield-6",
                "after",
            ],
        )
        beta_layer = result["layers"][1]
        self.assertEqual(
            beta_layer["filter"],
            [
                "all",
                ["has", "reflen"],
                ["<=", ["get", "reflen"], 6],
                ["==", ["get", "reflen"], 2],
                ["has", "shield_beta"],
            ],
        )
        self.assertEqual(beta_layer["layout"]["text-field"], ["get", "ref"])
        self.assertEqual(beta_layer["layout"]["symbol-placement"], "line")
        self.assertAlmostEqual(beta_layer["layout"]["symbol-spacing"], 466.66666666666663)
        self.assertEqual(beta_layer["layout"]["icon-image"][:2], ["match", ["get", "shield_beta"]])
        self.assertIn("ch-motorway-2", beta_layer["layout"]["icon-image"])
        self.assertEqual(beta_layer["layout"]["icon-image"][-1], "default-2")

        shield_layer = result["layers"][2]
        self.assertEqual(shield_layer["filter"][-2:], [["==", ["get", "reflen"], 2], ["!", ["has", "shield_beta"]]])
        self.assertEqual(shield_layer["layout"]["icon-image"][:2], ["match", ["get", "shield"]])
        self.assertIn("rectangle-yellow-2", shield_layer["layout"]["icon-image"])
        self.assertEqual(result["layers"][-1]["layout"]["icon-image"], shield_icon)

    def test_zoom_only_icon_size_expressions_resolve_to_scalars(self):
        zoom_interpolate = ["interpolate", ["linear"], ["zoom"], 10, 0.5, 14, 1.5]
        single_stop_zoom_interpolate = ["interpolate", ["linear"], ["zoom"], 14, 1.25]
        zoom_step = ["step", ["zoom"], 0.1, 18, 0.2, 20, 1.0]
        property_interpolate = ["interpolate", ["linear"], ["get", "rank"], 0, 0.5, 10, 1.5]
        data_driven_zoom_interpolate = ["interpolate", ["linear"], ["zoom"], 10, ["get", "size"], 14, 1.5]
        style = {
            "layers": [
                {"layout": {"icon-size": zoom_interpolate}},
                {"layout": {"icon-size": single_stop_zoom_interpolate}},
                {"minzoom": 18, "layout": {"icon-size": zoom_step}},
                {"layout": {"icon-size": property_interpolate}},
                {"layout": {"icon-size": data_driven_zoom_interpolate}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertAlmostEqual(result["layers"][0]["layout"]["icon-size"], 1.0)
        self.assertAlmostEqual(result["layers"][1]["layout"]["icon-size"], 1.25)
        self.assertAlmostEqual(result["layers"][2]["layout"]["icon-size"], 0.2)
        self.assertEqual(result["layers"][3]["layout"]["icon-size"], property_interpolate)
        self.assertEqual(result["layers"][4]["layout"]["icon-size"], data_driven_zoom_interpolate)

    def test_line_dasharray_expressions_resolve_to_literal_arrays(self):
        style = {
            "layers": [
                {
                    "paint": {
                        "line-dasharray": ["step", ["zoom"], ["literal", [3, 3]], 12, ["literal", [4, 4]]]
                    },
                },
                {
                    "paint": {
                        "line-dasharray": [
                            "interpolate",
                            ["linear"],
                            ["zoom"],
                            10,
                            ["literal", [1, 2]],
                            14,
                            ["literal", [2, 4]],
                        ]
                    },
                },
                {"paint": {"line-dasharray": ["literal", [2, 1]]}},
                {"paint": {"line-dasharray": [5, 2]}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["paint"]["line-dasharray"], [4, 4])
        self.assertEqual(result["layers"][1]["paint"]["line-dasharray"], [1, 2])
        self.assertEqual(result["layers"][2]["paint"]["line-dasharray"], [2, 1])
        self.assertEqual(result["layers"][3]["paint"]["line-dasharray"], [5, 2])
        self.assertIsInstance(style["layers"][0]["paint"]["line-dasharray"][2], list)

    def test_data_driven_line_dasharray_expressions_use_safe_literal_fallbacks(self):
        style = {
            "layers": [
                {"paint": {"line-dasharray": ["step", ["get", "rank"], ["literal", [1, 1]], 3, ["literal", [3, 3]]]}},
                {"paint": {"line-dasharray": ["match", ["get", "class"], "ferry", ["literal", [2, 2]], ["literal", [4, 2]]]}},
                {
                    "paint": {
                        "line-dasharray": [
                            "case",
                            ["==", ["get", "class"], "trail"],
                            ["literal", [1, 3]],
                            ["literal", [6, 2]],
                        ]
                    }
                },
                {"paint": {"line-dasharray": ["coalesce", ["get", "dash"], ["literal", [2, 4]]]}},
                {
                    "paint": {
                        "line-dasharray": [
                            "interpolate",
                            ["linear"],
                            ["get", "rank"],
                            0,
                            ["literal", [1, 2]],
                            10,
                            ["literal", [2, 4]],
                        ]
                    }
                },
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["paint"]["line-dasharray"], [1, 1])
        self.assertEqual(result["layers"][1]["paint"]["line-dasharray"], [4, 2])
        self.assertEqual(result["layers"][2]["paint"]["line-dasharray"], [6, 2])
        self.assertEqual(result["layers"][3]["paint"]["line-dasharray"], [2, 4])
        self.assertEqual(result["layers"][4]["paint"]["line-dasharray"], [1, 2])

    def test_unsupported_line_dasharray_expression_is_left_unchanged(self):
        expression = ["get", "dash"]
        style = {
            "layers": [
                {"paint": {"line-dasharray": expression}},
                {"paint": {"line-dasharray": ["literal", [2, -1]]}},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["paint"]["line-dasharray"], expression)
        self.assertEqual(result["layers"][1]["paint"]["line-dasharray"], ["literal", [2, -1]])

    def test_filter_expressions_use_parser_friendly_equivalents(self):
        style = {
            "layers": [
                {
                    "filter": ["!", ["match", ["get", "type"], ["steps", "sidewalk"], True, False]],
                },
                {
                    "filter": ["case", ["has", "layer"], [">=", ["get", "layer"], 0], True],
                },
                {
                    "filter": ["case", ["==", ["get", "class"], "park"], True, False],
                },
                {
                    "filter": ["case", ["==", ["get", "class"], "park"], False, True],
                },
                {
                    "filter": ["<=", ["+", ["get", "filterrank"], 0], ["-", 12, 0]],
                },
                {"filter": True},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["filter"],
            ["match", ["get", "type"], ["steps", "sidewalk"], False, True],
        )
        self.assertEqual(
            result["layers"][1]["filter"],
            ["any", ["!", ["has", "layer"]], [">=", ["get", "layer"], 0]],
        )
        self.assertEqual(result["layers"][2]["filter"], ["==", ["get", "class"], "park"])
        self.assertEqual(result["layers"][3]["filter"], ["!", ["==", ["get", "class"], "park"]])
        self.assertEqual(result["layers"][4]["filter"], ["<=", ["get", "filterrank"], 12])
        self.assertEqual(result["layers"][5]["filter"], ["==", 1, 1])
        self.assertEqual(style["layers"][0]["filter"][0], "!")

    def test_filter_simplification_snapshots_zoom_dependent_filters(self):
        filter_expression = [
            "step",
            ["zoom"],
            ["match", ["get", "class"], ["primary", "secondary"], True, False],
            14,
            ["match", ["get", "class"], ["primary", "secondary", "service"], True, False],
        ]
        style = {"layers": [{"id": "road-label", "type": "symbol", "minzoom": 16, "filter": filter_expression}]}

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["filter"],
            ["match", ["get", "class"], ["primary", "secondary", "service"], True, False],
        )
        self.assertEqual(style["layers"][0]["filter"], filter_expression)

    def test_filter_simplification_snapshots_settlement_label_filters(self):
        major_filter = [
            "all",
            ["<=", ["get", "filterrank"], 3],
            ["step", ["zoom"], False, 12, ["<", ["get", "symbolrank"], 15]],
        ]
        minor_filter = [
            "all",
            ["<=", ["get", "filterrank"], 3],
            ["step", ["zoom"], [">", ["get", "symbolrank"], 6], 12, [">=", ["get", "symbolrank"], 15]],
        ]
        style = {
            "layers": [
                {"id": "settlement-major-label", "type": "symbol", "filter": major_filter},
                {"id": "settlement-minor-label", "type": "symbol", "filter": minor_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["filter"],
            [
                "all",
                ["all", ["<=", ["get", "filterrank"], 3], ["<", ["get", "symbolrank"], 15]],
                ["match", ["get", "type"], ["city"], True, False],
            ],
        )
        self.assertEqual(
            result["layers"][1]["filter"],
            [
                "all",
                ["all", ["<=", ["get", "filterrank"], 3], [">=", ["get", "symbolrank"], 15]],
                ["match", ["get", "type"], ["town"], True, False],
            ],
        )
        self.assertEqual(style["layers"][0]["filter"], major_filter)
        self.assertEqual(style["layers"][1]["filter"], minor_filter)

    def _settlement_dot_icon_layout(self):
        return {
            "icon-image": [
                "step",
                ["zoom"],
                [
                    "case",
                    ["==", ["get", "capital"], 2],
                    "border-dot-13",
                    ["step", ["get", "symbolrank"], "dot-11", 9, "dot-10", 11, "dot-9"],
                ],
                8.0,
                "",
            ],
            "text-anchor": ["step", ["zoom"], ["get", "text_anchor"], 8.0, "center"],
            "text-radial-offset": ["step", ["zoom"], ["match", ["get", "capital"], 2, 0.6, 0.55], 8.0, 0],
            "text-justify": [
                "step",
                ["zoom"],
                [
                    "match",
                    ["get", "text_anchor"],
                    ["left", "bottom-left", "top-left"],
                    "left",
                    ["right", "bottom-right", "top-right"],
                    "right",
                    "center",
                ],
                8.0,
                "center",
            ],
        }

    def test_filter_simplification_splits_major_settlement_dot_icons_by_zoom_and_rank(self):
        base_filter = ["<=", ["get", "filterrank"], 3]
        major_filter = [
            "all",
            base_filter,
            [
                "step",
                ["zoom"],
                False,
                2,
                ["<=", ["get", "symbolrank"], 6],
                4,
                ["<", ["get", "symbolrank"], 7],
                6,
                ["<", ["get", "symbolrank"], 8],
                7,
                ["<", ["get", "symbolrank"], 10],
            ],
        ]
        style = {
            "layers": [
                {
                    "id": "settlement-major-label",
                    "type": "symbol",
                    "minzoom": 2,
                    "maxzoom": 15,
                    "filter": major_filter,
                    "layout": self._settlement_dot_icon_layout(),
                }
            ]
        }
        original_icon_image = copy.deepcopy(style["layers"][0]["layout"]["icon-image"])

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(len(result["layers"]), 17)
        by_id = {layer["id"]: layer for layer in result["layers"]}
        city_filter = ["match", ["get", "type"], ["city"], True, False]
        capital_layer = by_id["settlement-major-label-z2-to-z4-capital-border-dot"]
        dot_layer = by_id["settlement-major-label-z7-to-z8-dot-10"]
        text_layer = by_id["settlement-major-label-z8-plus"]
        self.assertEqual(capital_layer["minzoom"], 2)
        self.assertEqual(capital_layer["maxzoom"], 4.0)
        self.assertEqual(dot_layer["minzoom"], 7.0)
        self.assertEqual(dot_layer["maxzoom"], 8.0)
        self.assertEqual(text_layer["minzoom"], 8.0)
        self.assertEqual(text_layer["maxzoom"], 15)
        self.assertEqual(capital_layer["layout"]["icon-image"], "border-dot-13")
        self.assertEqual(dot_layer["layout"]["icon-image"], "dot-10")
        self.assertEqual(capital_layer["layout"]["text-anchor"], ["get", "text_anchor"])
        self.assertEqual(capital_layer["layout"]["text-radial-offset"], 0.6)
        self.assertEqual(dot_layer["layout"]["text-radial-offset"], 0.55)
        self.assertEqual(capital_layer["layout"]["text-justify"], self._settlement_dot_icon_layout()["text-justify"][2])
        self.assertEqual(
            capital_layer["filter"],
            ["all", ["all", base_filter, ["<=", ["get", "symbolrank"], 6], ["==", ["get", "capital"], 2]], city_filter],
        )
        self.assertEqual(
            dot_layer["filter"],
            [
                "all",
                [
                    "all",
                    base_filter,
                    ["<", ["get", "symbolrank"], 10],
                    ["all", ["!=", ["get", "capital"], 2], [">=", ["get", "symbolrank"], 9], ["<", ["get", "symbolrank"], 11]],
                ],
                city_filter,
            ],
        )
        self.assertNotIn("icon-image", text_layer["layout"])
        self.assertEqual(text_layer["layout"]["text-anchor"], "center")
        self.assertEqual(text_layer["layout"]["text-radial-offset"], 0)
        self.assertEqual(text_layer["layout"]["text-justify"], "center")
        self.assertEqual(text_layer["filter"][-1], city_filter)
        self.assertEqual(style["layers"][0]["layout"]["icon-image"], original_icon_image)

    def test_filter_simplification_splits_minor_settlement_dot_icons_with_town_filter(self):
        base_filter = ["<=", ["get", "filterrank"], 3]
        minor_filter = [
            "step",
            ["zoom"],
            [">", ["get", "symbolrank"], 6],
            4,
            [">=", ["get", "symbolrank"], 7],
            6,
            [">=", ["get", "symbolrank"], 8],
            7,
            [">=", ["get", "symbolrank"], 10],
        ]
        style = {
            "layers": [
                {
                    "id": "settlement-minor-label",
                    "type": "symbol",
                    "minzoom": 2,
                    "maxzoom": 13,
                    "filter": ["all", base_filter, minor_filter],
                    "layout": self._settlement_dot_icon_layout(),
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(len(result["layers"]), 17)
        by_id = {layer["id"]: layer for layer in result["layers"]}
        town_filter = ["match", ["get", "type"], ["town"], True, False]
        for suffix, icon_name in (
            ("capital-border-dot", "border-dot-13"),
            ("dot-11", "dot-11"),
            ("dot-10", "dot-10"),
            ("dot-9", "dot-9"),
        ):
            layer = by_id[f"settlement-minor-label-z2-to-z4-{suffix}"]
            self.assertEqual(layer["layout"]["icon-image"], icon_name)
            self.assertEqual(layer["filter"][-1], town_filter)
        self.assertEqual(by_id["settlement-minor-label-z2-to-z4-dot-11"]["filter"][1][2], [">", ["get", "symbolrank"], 6])
        self.assertEqual(by_id["settlement-minor-label-z7-to-z8-dot-11"]["filter"][1][2], [">=", ["get", "symbolrank"], 10])
        self.assertNotIn("icon-image", by_id["settlement-minor-label-z8-plus"]["layout"])
        self.assertEqual(by_id["settlement-minor-label-z8-plus"]["filter"][-1], town_filter)

    def test_filter_simplification_snapshots_terrain_fill_filters(self):
        landuse_filter = [
            "all",
            [">=", ["to-number", ["get", "sizerank"]], 0],
            [
                "match",
                ["get", "class"],
                "residential",
                ["step", ["zoom"], True, 10, False],
                "park",
                ["step", ["zoom"], False, 8, ["==", ["get", "sizerank"], 1], 10, True],
                False,
            ],
            [
                "<=",
                [
                    "-",
                    ["to-number", ["get", "sizerank"]],
                    ["interpolate", ["exponential", 1.5], ["zoom"], 12, 0, 18, 14],
                ],
                14,
            ],
        ]
        hillshade_filter = [
            "all",
            ["step", ["zoom"], ["==", ["get", "class"], "shadow"], 11, True],
            ["match", ["get", "level"], 89, True, 78, ["step", ["zoom"], False, 5, True], False],
        ]
        style = {
            "layers": [
                {"id": "landuse", "type": "fill", "filter": landuse_filter},
                {"id": "hillshade", "type": "fill", "filter": hillshade_filter},
                {"id": "water", "type": "fill", "filter": hillshade_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["filter"],
            [
                "all",
                [">=", ["to-number", ["get", "sizerank"]], 0],
                ["match", ["get", "class"], "residential", False, "park", True, False],
                ["<=", ["to-number", ["get", "sizerank"]], 14],
            ],
        )
        self.assertEqual(
            result["layers"][1]["filter"],
            ["all", True, ["match", ["get", "level"], 89, True, 78, True, False]],
        )
        self.assertEqual(result["layers"][2]["filter"], hillshade_filter)
        self.assertEqual(style["layers"][0]["filter"], landuse_filter)
        self.assertEqual(style["layers"][1]["filter"], hillshade_filter)

    def test_filter_simplification_snapshots_oneway_arrow_filters_at_minzoom(self):
        low_zoom_classes = ["primary", "secondary", "tertiary", "street", "street_limited"]
        high_zoom_classes = [
            "primary",
            "secondary",
            "tertiary",
            "street",
            "street_limited",
            "primary_link",
            "secondary_link",
            "tertiary_link",
            "service",
            "track",
        ]
        arrow_filter = [
            "all",
            ["==", ["get", "oneway"], "true"],
            [
                "step",
                ["zoom"],
                ["match", ["get", "class"], low_zoom_classes, True, False],
                16,
                ["match", ["get", "class"], high_zoom_classes, True, False],
            ],
            ["match", ["get", "structure"], ["none", "ford"], True, False],
        ]
        original_arrow_filter = copy.deepcopy(arrow_filter)
        style = {
            "layers": [
                {"id": "road-oneway-arrow-blue", "type": "symbol", "minzoom": 16, "filter": arrow_filter},
                {"id": "bridge-oneway-arrow-blue", "type": "symbol", "minzoom": 16, "filter": arrow_filter},
                {"id": "tunnel-oneway-arrow-blue", "type": "symbol", "minzoom": 16, "filter": arrow_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        for layer in result["layers"]:
            self.assertEqual(
                layer["filter"],
                [
                    "all",
                    ["==", ["get", "oneway"], "true"],
                    ["match", ["get", "class"], high_zoom_classes, True, False],
                    ["match", ["get", "structure"], ["none", "ford"], True, False],
                ],
            )
        self.assertEqual(arrow_filter, original_arrow_filter)
        for layer in style["layers"]:
            self.assertEqual(layer["filter"], original_arrow_filter)

    def test_filter_simplification_snapshots_motorway_trunk_line_filters(self):
        motorway_filter = [
            "all",
            [
                "step",
                ["zoom"],
                ["match", ["get", "class"], ["motorway", "trunk"], True, False],
                5,
                [
                    "all",
                    ["match", ["get", "class"], ["motorway", "trunk"], True, False],
                    ["match", ["get", "structure"], ["none", "ford"], True, False],
                ],
            ],
            ["==", ["geometry-type"], "LineString"],
        ]
        original_motorway_filter = copy.deepcopy(motorway_filter)
        style = {
            "layers": [
                {"id": "road-motorway-trunk", "type": "line", "filter": motorway_filter},
                {"id": "road-motorway-trunk-case", "type": "line", "filter": motorway_filter},
                {"id": "road-primary", "type": "line", "filter": motorway_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        expected_filter = [
            "all",
            [
                "all",
                ["match", ["get", "class"], ["motorway", "trunk"], True, False],
                ["match", ["get", "structure"], ["none", "ford"], True, False],
            ],
            ["==", ["geometry-type"], "LineString"],
        ]
        self.assertEqual(result["layers"][0]["filter"], expected_filter)
        self.assertEqual(result["layers"][1]["filter"], expected_filter)
        self.assertEqual(result["layers"][2]["filter"], original_motorway_filter)
        self.assertEqual(motorway_filter, original_motorway_filter)
        for layer in style["layers"]:
            self.assertEqual(layer["filter"], original_motorway_filter)

    def test_filter_simplification_snapshots_minor_line_filters_at_service_zoom(self):
        minor_filter = [
            "all",
            [
                "match",
                ["get", "class"],
                ["track"],
                True,
                "service",
                ["step", ["zoom"], False, 14, True],
                False,
            ],
            ["match", ["get", "structure"], ["none", "ford"], True, False],
            ["==", ["geometry-type"], "LineString"],
        ]
        original_minor_filter = copy.deepcopy(minor_filter)
        style = {
            "layers": [
                {"id": "road-minor", "type": "line", "minzoom": 13, "filter": minor_filter},
                {"id": "road-minor-case", "type": "line", "minzoom": 13, "filter": minor_filter},
                {"id": "bridge-minor", "type": "line", "minzoom": 13, "filter": minor_filter},
                {"id": "bridge-minor-case", "type": "line", "minzoom": 13, "filter": minor_filter},
                {"id": "tunnel-minor", "type": "line", "minzoom": 13, "filter": minor_filter},
                {"id": "tunnel-minor-case", "type": "line", "minzoom": 13, "filter": minor_filter},
                {"id": "road-path", "type": "line", "minzoom": 13, "filter": minor_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        expected_filter = [
            "all",
            ["match", ["get", "class"], ["track"], True, "service", True, False],
            ["match", ["get", "structure"], ["none", "ford"], True, False],
            ["==", ["geometry-type"], "LineString"],
        ]
        for layer in result["layers"][:-1]:
            self.assertEqual(layer["filter"], expected_filter)
        self.assertEqual(result["layers"][-1]["filter"], original_minor_filter)
        self.assertEqual(minor_filter, original_minor_filter)
        for layer in style["layers"]:
            self.assertEqual(layer["filter"], original_minor_filter)

    def test_filter_simplification_splits_path_type_filters_by_zoom_band(self):
        path_filter = [
            "all",
            ["==", ["get", "class"], "path"],
            [
                "step",
                ["zoom"],
                ["!", ["match", ["get", "type"], ["steps", "sidewalk", "crossing"], True, False]],
                16,
                ["!=", ["get", "type"], "steps"],
            ],
            ["match", ["get", "structure"], ["none", "ford"], True, False],
            ["==", ["geometry-type"], "LineString"],
        ]
        original_path_filter = copy.deepcopy(path_filter)
        style = {
            "layers": [
                {"id": "road-path-bg", "type": "line", "minzoom": 12, "filter": path_filter},
                {"id": "road-path", "type": "line", "minzoom": 12, "filter": path_filter},
                {"id": "bridge-path-bg", "type": "line", "minzoom": 14, "filter": path_filter},
                {"id": "road-minor", "type": "line", "minzoom": 12, "filter": path_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            [layer["id"] for layer in result["layers"]],
            [
                "road-path-bg-below-z16",
                "road-path-bg-z16-plus",
                "road-path-below-z16",
                "road-path-z16-plus",
                "bridge-path-bg-below-z16",
                "bridge-path-bg-z16-plus",
                "road-minor",
            ],
        )
        expected_low_filter = [
            "all",
            ["==", ["get", "class"], "path"],
            ["match", ["get", "type"], ["steps", "sidewalk", "crossing"], False, True],
            ["match", ["get", "structure"], ["none", "ford"], True, False],
            ["==", ["geometry-type"], "LineString"],
        ]
        expected_high_filter = [
            "all",
            ["==", ["get", "class"], "path"],
            ["!=", ["get", "type"], "steps"],
            ["match", ["get", "structure"], ["none", "ford"], True, False],
            ["==", ["geometry-type"], "LineString"],
        ]
        self.assertEqual(result["layers"][0]["filter"], expected_low_filter)
        self.assertEqual(result["layers"][0]["minzoom"], 12)
        self.assertEqual(result["layers"][0]["maxzoom"], 16.0)
        self.assertEqual(result["layers"][1]["filter"], expected_high_filter)
        self.assertEqual(result["layers"][1]["minzoom"], 16.0)
        self.assertEqual(result["layers"][2]["filter"], expected_low_filter)
        self.assertEqual(result["layers"][3]["filter"], expected_high_filter)
        self.assertEqual(result["layers"][4]["minzoom"], 14)
        self.assertEqual(result["layers"][4]["maxzoom"], 16.0)
        self.assertEqual(result["layers"][5]["minzoom"], 16.0)
        self.assertEqual(result["layers"][6]["filter"], expected_low_filter)
        self.assertEqual(path_filter, original_path_filter)
        for layer in style["layers"]:
            self.assertEqual(layer["filter"], original_path_filter)

    def test_filter_simplification_replaces_path_filter_without_split_outside_threshold(self):
        path_filter = [
            "all",
            ["==", ["get", "class"], "path"],
            [
                "step",
                ["zoom"],
                ["match", ["get", "type"], ["steps", "sidewalk", "crossing"], False, True],
                16,
                ["!=", ["get", "type"], "steps"],
            ],
            ["==", ["geometry-type"], "LineString"],
        ]
        style = {
            "layers": [
                {"id": "road-path", "type": "line", "minzoom": 12, "maxzoom": 15, "filter": path_filter},
                {"id": "road-path-bg", "type": "line", "minzoom": 16, "filter": path_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual([layer["id"] for layer in result["layers"]], ["road-path", "road-path-bg"])
        self.assertEqual(
            result["layers"][0]["filter"],
            [
                "all",
                ["==", ["get", "class"], "path"],
                ["match", ["get", "type"], ["steps", "sidewalk", "crossing"], False, True],
                ["==", ["geometry-type"], "LineString"],
            ],
        )
        self.assertEqual(
            result["layers"][1]["filter"],
            [
                "all",
                ["==", ["get", "class"], "path"],
                ["!=", ["get", "type"], "steps"],
                ["==", ["geometry-type"], "LineString"],
            ],
        )

    def test_filter_simplification_splits_poi_filterrank_filter_by_zoom_band(self):
        class_rank_match = [
            "match",
            ["get", "class"],
            "food_and_drink_stores",
            3,
            "park_like",
            4,
            2,
        ]
        poi_filter = [
            "<=",
            ["get", "filterrank"],
            ["+", ["step", ["zoom"], 0, 16, 1, 17, 2], class_rank_match],
        ]
        original_poi_filter = copy.deepcopy(poi_filter)
        style = {
            "layers": [
                {
                    "id": "poi-label",
                    "type": "symbol",
                    "minzoom": 6,
                    "filter": poi_filter,
                    "layout": {"text-size": ["interpolate", ["linear"], ["zoom"], 0, 4, 20, 24]},
                },
                {"id": "natural-point-label", "type": "symbol", "minzoom": 6, "filter": poi_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            [layer["id"] for layer in result["layers"]],
            ["poi-label-below-z16", "poi-label-z16-to-z17", "poi-label-z17-plus", "natural-point-label"],
        )
        self.assertEqual(result["layers"][0]["minzoom"], 6)
        self.assertEqual(result["layers"][0]["maxzoom"], 16.0)
        self.assertEqual(result["layers"][1]["minzoom"], 16.0)
        self.assertEqual(result["layers"][1]["maxzoom"], 17.0)
        self.assertEqual(result["layers"][2]["minzoom"], 17.0)
        self.assertNotIn("maxzoom", result["layers"][2])
        self.assertEqual(result["layers"][0]["layout"]["text-size"], 9.0)
        self.assertEqual(result["layers"][1]["layout"]["text-size"], 9.0)
        self.assertEqual(result["layers"][2]["layout"]["text-size"], 9.0)
        self.assertEqual(
            result["layers"][0]["filter"],
            ["<=", ["get", "filterrank"], ["match", ["get", "class"], "food_and_drink_stores", 3.0, "park_like", 4.0, 2.0]],
        )
        self.assertEqual(
            result["layers"][1]["filter"],
            ["<=", ["get", "filterrank"], ["match", ["get", "class"], "food_and_drink_stores", 4.0, "park_like", 5.0, 3.0]],
        )
        self.assertEqual(
            result["layers"][2]["filter"],
            ["<=", ["get", "filterrank"], ["match", ["get", "class"], "food_and_drink_stores", 5.0, "park_like", 6.0, 4.0]],
        )
        self.assertEqual(result["layers"][3]["filter"], original_poi_filter)
        self.assertEqual(poi_filter, original_poi_filter)
        for layer in style["layers"]:
            self.assertEqual(layer["filter"], original_poi_filter)

    def test_filter_simplification_replaces_poi_filter_without_split_outside_thresholds(self):
        class_rank_match = ["match", ["get", "class"], "historic", 3, 2]
        poi_filter = [
            "<=",
            ["get", "filterrank"],
            ["+", class_rank_match, ["step", ["zoom"], 0, 16, 1, 17, 2]],
        ]
        style = {
            "layers": [
                {"id": "poi-label", "type": "symbol", "minzoom": 6, "maxzoom": 16, "filter": poi_filter},
                {"id": "poi-label", "type": "symbol", "minzoom": 16, "maxzoom": 17, "filter": poi_filter},
                {"id": "poi-label", "type": "symbol", "minzoom": 17, "filter": poi_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual([layer["id"] for layer in result["layers"]], ["poi-label", "poi-label", "poi-label"])
        self.assertEqual(
            result["layers"][0]["filter"],
            ["<=", ["get", "filterrank"], ["match", ["get", "class"], "historic", 3.0, 2.0]],
        )
        self.assertEqual(
            result["layers"][1]["filter"],
            ["<=", ["get", "filterrank"], ["match", ["get", "class"], "historic", 4.0, 3.0]],
        )
        self.assertEqual(
            result["layers"][2]["filter"],
            ["<=", ["get", "filterrank"], ["match", ["get", "class"], "historic", 5.0, 4.0]],
        )

    def test_filter_simplification_splits_natural_icon_visibility_by_zoom_and_sizerank(self):
        icon_opacity = [
            "step",
            ["zoom"],
            ["step", ["get", "sizerank"], 0, 5.0, 1],
            17.0,
            ["step", ["get", "sizerank"], 0, 13.0, 1],
        ]
        text_anchor = [
            "step",
            ["zoom"],
            ["step", ["get", "sizerank"], "center", 5.0, "top"],
            17.0,
            ["step", ["get", "sizerank"], "center", 13.0, "top"],
        ]
        text_offset = [
            "step",
            ["zoom"],
            ["step", ["get", "sizerank"], ["literal", [0, 0]], 5.0, ["literal", [0, 0.8]]],
            17.0,
            ["step", ["get", "sizerank"], ["literal", [0, 0]], 13.0, ["literal", [0, 0.8]]],
        ]
        style = {
            "layers": [
                {
                    "id": "natural-point-label",
                    "type": "symbol",
                    "minzoom": 4,
                    "filter": ["==", ["geometry-type"], "Point"],
                    "layout": {
                        "icon-image": ["get", "maki"],
                        "icon-size": 1,
                        "text-anchor": text_anchor,
                        "text-offset": text_offset,
                    },
                    "paint": {"icon-opacity": icon_opacity},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            [layer["id"] for layer in result["layers"]],
            [
                "natural-point-label-below-z17-text",
                "natural-point-label-below-z17-icon",
                "natural-point-label-z17-plus-text",
                "natural-point-label-z17-plus-icon",
            ],
        )
        below_text, below_icon, high_text, high_icon = result["layers"]
        self.assertEqual(below_text["maxzoom"], 17.0)
        self.assertEqual(below_icon["maxzoom"], 17.0)
        self.assertEqual(high_text["minzoom"], 17.0)
        self.assertEqual(high_icon["minzoom"], 17.0)
        self.assertEqual(below_text["layout"]["text-anchor"], "center")
        self.assertEqual(below_text["layout"]["text-offset"], [0, 0])
        self.assertNotIn("icon-image", below_text["layout"])
        self.assertNotIn("icon-size", below_text["layout"])
        self.assertNotIn("icon-opacity", below_text["paint"])
        self.assertEqual(below_text["filter"], ["all", ["==", ["geometry-type"], "Point"], ["<", ["get", "sizerank"], 5.0]])
        self.assertEqual(below_icon["layout"]["text-anchor"], "top")
        self.assertEqual(below_icon["layout"]["text-offset"], [0, 0.8])
        self.assertEqual(below_icon["layout"]["icon-image"][0:2], ["match", ["get", "maki"]])
        self.assertNotIn("icon-opacity", below_icon["paint"])
        self.assertEqual(high_text["filter"][-1], ["<", ["get", "sizerank"], 13.0])
        self.assertEqual(high_icon["filter"][-1], [">=", ["get", "sizerank"], 13.0])
        self.assertEqual(style["layers"][0]["paint"]["icon-opacity"], icon_opacity)

    def test_filter_simplification_splits_generated_poi_icon_visibility_layer(self):
        icon_opacity = [
            "step",
            ["zoom"],
            ["step", ["get", "sizerank"], 0, 5.0, 1],
            17.0,
            ["step", ["get", "sizerank"], 0, 13.0, 1],
        ]
        text_anchor = [
            "step",
            ["zoom"],
            ["step", ["get", "sizerank"], "center", 5.0, "top"],
            17.0,
            ["step", ["get", "sizerank"], "center", 13.0, "top"],
        ]
        text_offset = [
            "step",
            ["zoom"],
            ["step", ["get", "sizerank"], ["literal", [0, 0]], 5.0, ["literal", [0, 0.8]]],
            17.0,
            ["step", ["get", "sizerank"], ["literal", [0, 0]], 13.0, ["literal", [0, 0.8]]],
        ]
        style = {
            "layers": [
                {
                    "id": "poi-label-z17-plus",
                    "type": "symbol",
                    "minzoom": 17,
                    "filter": ["<=", ["get", "filterrank"], 4],
                    "layout": {"icon-image": "restaurant", "text-anchor": text_anchor, "text-offset": text_offset},
                    "paint": {"icon-opacity": icon_opacity},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual([layer["id"] for layer in result["layers"]], ["poi-label-z17-plus-z17-plus-text", "poi-label-z17-plus-z17-plus-icon"])
        text_layer, icon_layer = result["layers"]
        self.assertEqual(text_layer["filter"], ["all", ["<=", ["get", "filterrank"], 4], ["<", ["get", "sizerank"], 13.0]])
        self.assertEqual(icon_layer["filter"], ["all", ["<=", ["get", "filterrank"], 4], [">=", ["get", "sizerank"], 13.0]])
        self.assertNotIn("icon-image", text_layer["layout"])
        self.assertEqual(icon_layer["layout"]["icon-image"], "restaurant")
        self.assertNotIn("icon-opacity", text_layer["paint"])
        self.assertNotIn("icon-opacity", icon_layer["paint"])

    def test_filter_simplification_clamps_minor_line_zoom_override_to_layer_bounds(self):
        minor_filter = [
            "match",
            ["get", "class"],
            ["track"],
            True,
            "service",
            ["step", ["zoom"], False, 14, True],
            False,
        ]
        style = {
            "layers": [
                {"id": "road-minor", "type": "line", "minzoom": 13, "maxzoom": 14, "filter": minor_filter},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["filter"],
            ["match", ["get", "class"], ["track"], True, "service", False, False],
        )

    def test_filter_simplification_normalizes_nested_zoom_arithmetic(self):
        style = {
            "layers": [
                {
                    "id": "road-number-shield",
                    "type": "symbol",
                    "filter": [
                        "<=",
                        ["-", ["to-number", ["get", "sizerank"]], ["interpolate", ["linear"], ["zoom"], 12, 0, 18, 14]],
                        14,
                    ]
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(
            result["layers"][0]["filter"],
            ["<=", ["to-number", ["get", "sizerank"]], 14],
        )

    def test_filter_simplification_normalizes_zoom_arithmetic_operators(self):
        style = {
            "layers": [
                {"id": "road-label", "type": "symbol", "filter": ["==", ["+", ["zoom"], 2], 14]},
                {"id": "road-label", "type": "symbol", "filter": ["==", ["-", ["zoom"], 2], 10]},
                {"id": "road-label", "type": "symbol", "filter": ["==", ["*", ["zoom"], 2], 24]},
                {"id": "road-label", "type": "symbol", "filter": ["==", ["/", ["zoom"], 2], 6]},
                {"id": "road-label", "type": "symbol", "filter": ["==", ["/", ["zoom"], 0], 0]},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["filter"], ["==", 14.0, 14])
        self.assertEqual(result["layers"][1]["filter"], ["==", 10.0, 10])
        self.assertEqual(result["layers"][2]["filter"], ["==", 24.0, 24])
        self.assertEqual(result["layers"][3]["filter"], ["==", 6.0, 6])
        self.assertEqual(result["layers"][4]["filter"], ["==", ["/", 12.0, 0], 0])

    def test_filter_simplification_normalizes_zoom_inside_literal_and_match_outputs(self):
        style = {
            "layers": [
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": ["==", ["+", ["literal", [["zoom"]]], 2], 0],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": ["==", ["+", ["match", ["get", "class"], "primary", ["zoom"], 0], 2], 14],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": [
                        "match",
                        ["get", "class"],
                        ["zoom"],
                        ["==", ["step", ["zoom"], "low", 14, "high"], "low"],
                        False,
                    ],
                },
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["filter"], ["==", ["+", ["literal", [["zoom"]]], 2], 0])
        self.assertEqual(
            result["layers"][1]["filter"],
            ["==", ["+", ["match", ["get", "class"], "primary", 12.0, 0], 2], 14],
        )
        self.assertEqual(
            result["layers"][2]["filter"],
            ["match", ["get", "class"], ["zoom"], ["==", "low", "low"], False],
        )

    def test_filter_simplification_handles_unsupported_zoom_step_shapes(self):
        style = {
            "layers": [
                {"id": "road-label", "type": "symbol", "filter": ["==", ["step", ["zoom"], "low"], "low"]},
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": ["==", ["step", ["get", "rank"], "low", 14, "high"], "low"],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": ["==", ["step", ["zoom"], "low", 14, "high"], "low"],
                },
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["filter"], ["==", ["step", ["zoom"], "low"], "low"])
        self.assertEqual(
            result["layers"][1]["filter"],
            ["==", ["step", ["get", "rank"], "low", 14, "high"], "low"],
        )
        self.assertEqual(result["layers"][2]["filter"], ["==", "low", "low"])

    def test_filter_simplification_handles_zoom_interpolate_edge_cases(self):
        style = {
            "layers": [
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": ["==", ["interpolate", ["linear"], ["zoom"], 12, 0], 0],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": ["==", ["interpolate", ["linear"], ["get", "rank"], 0, 0, 10, 10], 0],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "filter": ["==", ["interpolate", ["linear"], ["zoom"], "low", 0, "high", 10], 0],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "minzoom": 15,
                    "filter": ["==", ["interpolate", ["linear"], ["zoom"], 12, 0, 18, 12], 6],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "minzoom": 15,
                    "filter": ["==", ["interpolate", ["linear"], ["zoom"], 12, "low", 18, "high"], "low"],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "minzoom": 20,
                    "filter": ["==", ["interpolate", ["linear"], ["zoom"], 12, 0, 18, 12], 12],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "minzoom": 15,
                    "filter": ["==", ["interpolate", ["exponential", 2], ["zoom"], 12, 0, 18, 12], 0],
                },
                {
                    "id": "road-label",
                    "type": "symbol",
                    "minzoom": 15,
                    "filter": ["==", ["interpolate", ["cubic-bezier", 0, 0, 1, 1], ["zoom"], 12, 0, 18, 12], 0],
                },
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["filter"], ["==", 0, 0])
        self.assertEqual(
            result["layers"][1]["filter"],
            ["==", ["interpolate", ["linear"], ["get", "rank"], 0, 0, 10, 10], 0],
        )
        self.assertEqual(
            result["layers"][2]["filter"],
            ["==", ["interpolate", ["linear"], ["zoom"], "low", 0, "high", 10], 0],
        )
        self.assertEqual(result["layers"][3]["filter"], ["==", 6.0, 6])
        self.assertEqual(result["layers"][4]["filter"], ["==", "low", "low"])
        self.assertEqual(result["layers"][5]["filter"], ["==", 12, 12])
        self.assertEqual(result["layers"][6]["filter"][0], "==")
        self.assertAlmostEqual(result["layers"][6]["filter"][1], 1.3333333333333333)
        self.assertEqual(result["layers"][6]["filter"][2], 0)
        self.assertEqual(
            result["layers"][7]["filter"],
            ["==", ["interpolate", ["cubic-bezier", 0, 0, 1, 1], ["zoom"], 12, 0, 18, 12], 0],
        )

    def test_filter_simplification_preserves_zoom_dependent_geometry_filters(self):
        filter_expression = [
            "all",
            ["==", ["get", "class"], "path"],
            ["step", ["zoom"], ["match", ["get", "type"], ["steps", "sidewalk"], False, True], 16, True],
        ]
        style = {"layers": [{"type": "line", "filter": filter_expression}]}

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["filter"], filter_expression)

    def test_filter_simplification_preserves_non_target_symbol_zoom_filters(self):
        filter_expression = ["<=", ["get", "filterrank"], ["step", ["zoom"], 0, 16, 2]]
        style = {
            "layers": [
                {"id": "poi-label", "type": "symbol", "filter": filter_expression},
                {"id": "natural-point-label", "type": "symbol", "filter": filter_expression},
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["filter"], filter_expression)
        self.assertEqual(result["layers"][1]["filter"], filter_expression)

    def test_line_cap_step_expression_uses_high_zoom_choice(self):
        style = {
            "layers": [
                {
                    "paint": {},
                    "layout": {"line-cap": ["step", ["zoom"], "butt", 14, "round"]},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["line-cap"], "round")

    def test_line_join_step_expression_uses_high_zoom_choice(self):
        style = {
            "layers": [
                {
                    "paint": {},
                    "layout": {"line-join": ["step", ["zoom"], "miter", 14, "round"]},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["line-join"], "round")

    def test_unsupported_line_join_expression_is_left_unchanged(self):
        expression = ["match", ["get", "class"], "path", "round", "miter"]
        style = {
            "layers": [
                {
                    "paint": {},
                    "layout": {"line-join": expression},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["line-join"], expression)

    def test_data_driven_line_join_step_expression_is_left_unchanged(self):
        expression = ["step", ["get", "rank"], "miter", 3, "round"]
        style = {
            "layers": [
                {
                    "paint": {},
                    "layout": {"line-join": expression},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["line-join"], expression)

    def test_line_join_step_expression_with_unsupported_high_zoom_choice_is_left_unchanged(self):
        expression = ["step", ["zoom"], "miter", 14, "none"]
        style = {
            "layers": [
                {
                    "paint": {},
                    "layout": {"line-join": expression},
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["line-join"], expression)

    def test_mapbox_font_stack_uses_qgis_safe_fallback(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-font": ["DIN Pro Medium", "Arial Unicode MS Regular"],
                        "text-field": ["get", "name"],
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-font"], [QGIS_TEXT_FONT_FALLBACK])
        self.assertEqual(
            style["layers"][0]["layout"]["text-font"],
            ["DIN Pro Medium", "Arial Unicode MS Regular"],
        )

    def test_mapbox_text_font_expression_is_left_unchanged(self):
        expression = [
            "step",
            ["zoom"],
            ["literal", [QGIS_TEXT_FONT_FALLBACK]],
            12,
            ["literal", ["DIN Pro Medium"]],
        ]
        style = {"layers": [{"layout": {"text-font": expression}}]}

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-font"], expression)

    def test_format_text_field_expression_uses_primary_label_field(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "format",
                            ["get", "name"],
                            {"font-scale": 1.0},
                            "\n",
                            {},
                            ["get", "ele"],
                            {"font-scale": 0.8},
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name"])

    def test_nested_format_text_field_expression_prefers_generic_name_fallback(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "format",
                            ["coalesce", ["get", "name_en"], ["get", "name"]],
                            {"font-scale": 1.0},
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name"])

    def test_coalesce_text_field_expression_searches_nested_stringified_field(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "coalesce",
                            ["to-string", ["get", "name"]],
                            "",
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name"])

    def test_coalesce_text_field_expression_preserves_direct_fallback_before_nested_optional_field(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "coalesce",
                            ["concat", ["get", "ref"], " ", ["get", "name"]],
                            ["get", "name"],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name"])

    def test_coalesce_text_field_expression_preserves_direct_fallback_before_stringified_optional_field(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "coalesce",
                            ["to-string", ["get", "ref"]],
                            ["get", "name"],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name"])

    def test_coalesce_text_field_expression_prefers_generic_name_over_formatted_locale(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "coalesce",
                            ["format", ["get", "name_en"], {}],
                            ["get", "name"],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name"])

    def test_coalesce_text_field_expression_keeps_first_reference_without_generic_name(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "coalesce",
                            ["get", "name_en"],
                            ["get", "name_fr"],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name_en"])

    def test_coalesce_text_field_expression_preserves_non_locale_primary_before_name(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "coalesce",
                            ["get", "ref"],
                            ["get", "name"],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "ref"])

    def test_concat_text_field_expression_uses_first_stringified_field(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "concat",
                            ["to-string", ["get", "ref"]],
                            " ",
                            ["get", "name"],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "ref"])

    def test_step_text_field_expression_uses_nested_generic_name_reference(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "step",
                            ["zoom"],
                            "",
                            13,
                            [
                                "match",
                                ["get", "mode"],
                                ["rail", "metro_rail"],
                                ["coalesce", ["get", "name_en"], ["get", "name"]],
                                "",
                            ],
                            18,
                            ["coalesce", ["get", "name_en"], ["get", "name"]],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name"])

    def test_step_text_field_expression_ignores_input_field_and_uses_label_output(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "step",
                            ["get", "sizerank"],
                            [
                                "case",
                                ["has", "ref"],
                                ["concat", ["get", "ref"], " -\n", ["coalesce", ["get", "name_en"], ["get", "name"]]],
                                ["coalesce", ["get", "name_en"], ["get", "name"]],
                            ],
                            15,
                            ["get", "ref"],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "ref"])

    def test_case_text_field_expression_uses_label_output_not_condition(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "case",
                            ["get", "show_label"],
                            "",
                            ["coalesce", ["get", "name_en"], ["get", "name"]],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "name"])

    def test_match_text_field_expression_keeps_first_reference_without_generic_name(self):
        style = {
            "layers": [
                {
                    "layout": {
                        "text-field": [
                            "match",
                            ["get", "mode"],
                            "rail",
                            ["get", "station_ref"],
                            ["get", "stop_ref"],
                        ]
                    },
                }
            ]
        }

        result = simplify_mapbox_style_expressions(style)

        self.assertEqual(result["layers"][0]["layout"]["text-field"], ["get", "station_ref"])

    def test_original_style_not_mutated(self):
        expr = ["match", ["get", "class"], "motorway", "hsl(15, 100%, 75%)", "hsl(35, 89%, 75%)"]
        style = {"layers": [{"paint": {"line-color": expr}, "layout": {}}]}
        _ = simplify_mapbox_style_expressions(style)
        # Original should not be changed
        self.assertIsInstance(style["layers"][0]["paint"]["line-color"], list)


if __name__ == "__main__":
    unittest.main()
