import unittest

import tests._path  # noqa: F401,E402

from mapbox_config import (  # noqa: E402
    MapboxConfigError,
    build_background_layer_name,
    build_mapbox_tiles_url,
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
        self.assertIn("styles/v1/my%20user/style%2Fid/tiles/256/{z}/{x}/{y}", url)
        self.assertIn("access_token=pk.test%20token", url)
        self.assertNotIn("@2x", url)

    def test_xyz_uri_wraps_tiles_url(self):
        uri = build_xyz_layer_uri("pk.123", "mapbox", "outdoors-v12")
        self.assertTrue(uri.startswith("type=xyz&url=https://api.mapbox.com/"))
        self.assertIn("{z}/{x}/{y}@2x", uri)
        self.assertIn("zmin=0&zmax=22", uri)

    def test_layer_name_prefers_preset_label(self):
        self.assertEqual(
            build_background_layer_name("Satellite", "mapbox", "satellite-streets-v12"),
            "qfit background — Satellite",
        )
        self.assertEqual(
            build_background_layer_name("Custom", "ebelo", "winter-wonderland"),
            "qfit background — ebelo/winter-wonderland",
        )


if __name__ == "__main__":
    unittest.main()
