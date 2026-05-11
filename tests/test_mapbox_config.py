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
    nearest_native_web_mercator_zoom_level,
    native_web_mercator_resolution_for_zoom,
    simplify_mapbox_style_expressions,
    build_xyz_layer_uri,
    preset_defaults,
    preset_requires_custom_style,
    resolve_background_style,
    snap_web_mercator_bounds_to_native_zoom,
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

    def test_original_style_not_mutated(self):
        expr = ["match", ["get", "class"], "motorway", "hsl(15, 100%, 75%)", "hsl(35, 89%, 75%)"]
        style = {"layers": [{"paint": {"line-color": expr}, "layout": {}}]}
        _ = simplify_mapbox_style_expressions(style)
        # Original should not be changed
        self.assertIsInstance(style["layers"][0]["paint"]["line-color"], list)


if __name__ == "__main__":
    unittest.main()
