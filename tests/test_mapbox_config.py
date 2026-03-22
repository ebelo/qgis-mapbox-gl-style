import unittest

import tests._path  # noqa: F401,E402

from mapbox_config import (  # noqa: E402
    DEFAULT_MAPBOX_RETINA,
    DEFAULT_MAPBOX_TILE_PIXEL_RATIO,
    DEFAULT_MAPBOX_TILE_SIZE,
    TILE_MODE_RASTER,
    TILE_MODE_VECTOR,
    TILE_MODES,
    MapboxConfigError,
    build_background_layer_name,
    build_mapbox_style_json_url,
    build_mapbox_tiles_url,
    build_mapbox_vector_tiles_url,
    build_vector_tile_layer_uri,
    extract_mapbox_vector_source_ids,
    simplify_mapbox_style_expressions,
    build_xyz_layer_uri,
    preset_defaults,
    preset_requires_custom_style,
    resolve_background_style,
)


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


    def test_vector_tile_url_uses_v4_endpoint(self):
        url = build_mapbox_vector_tiles_url("pk.token", "mapbox", "outdoors-v12")
        self.assertIn("api.mapbox.com/v4/mapbox.outdoors-v12", url)
        self.assertIn("{z}/{x}/{y}.mvt", url)
        self.assertIn("access_token=pk.token", url)

    def test_style_json_url_uses_styles_endpoint(self):
        url = build_mapbox_style_json_url("pk.token", "mapbox", "outdoors-v12")
        self.assertIn("api.mapbox.com/styles/v1/mapbox/outdoors-v12", url)
        self.assertIn("access_token=pk.token", url)

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

    def test_interpolate_expression_resolved_to_last_color(self):
        style = {
            "layers": [
                {
                    "paint": {
                        "line-color": ["interpolate", ["linear"], ["zoom"], 13, "hsl(75, 25%, 68%)", 16, "hsl(60, 0%, 75%)"]
                    },
                    "layout": {},
                }
            ]
        }
        result = simplify_mapbox_style_expressions(style)
        self.assertEqual(result["layers"][0]["paint"]["line-color"], "hsl(60, 0%, 75%)")

    def test_non_color_expressions_not_touched(self):
        style = {
            "layers": [
                {
                    "paint": {
                        "line-width": ["interpolate", ["exponential", 1.5], ["zoom"], 9, 1, 22, 10],
                        "line-color": "hsl(100, 50%, 60%)",
                    },
                    "layout": {},
                }
            ]
        }
        result = simplify_mapbox_style_expressions(style)
        # line-width expression is not touched
        self.assertIsInstance(result["layers"][0]["paint"]["line-width"], list)
        self.assertEqual(result["layers"][0]["paint"]["line-color"], "hsl(100, 50%, 60%)")

    def test_original_style_not_mutated(self):
        expr = ["match", ["get", "class"], "motorway", "hsl(15, 100%, 75%)", "hsl(35, 89%, 75%)"]
        style = {"layers": [{"paint": {"line-color": expr}, "layout": {}}]}
        _ = simplify_mapbox_style_expressions(style)
        # Original should not be changed
        self.assertIsInstance(style["layers"][0]["paint"]["line-color"], list)


if __name__ == "__main__":
    unittest.main()
