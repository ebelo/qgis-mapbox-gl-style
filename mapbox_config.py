from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, quote, urlencode, unquote, urlparse, urlunparse
from urllib.request import urlopen


class MapboxConfigError(ValueError):
    """Raised when the configured Mapbox background settings are incomplete."""


@dataclass(frozen=True)
class MapboxSpriteResources:
    """Mapbox sprite sheet resources for QGIS Mapbox GL style conversion."""

    definitions: dict[str, object]
    image_bytes: bytes


def _is_mapbox_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    return hostname.lower().split(".")[-2:] == ["mapbox", "com"]


def _format_mapbox_sprite_url(
    token: str,
    owner: str,
    style_id: str,
    sprite_path_segments: tuple[str, ...],
    *,
    file_type: str,
    retina: bool,
) -> str:
    retina_suffix = "@2x" if retina else ""
    extra_sprite_path = "".join(f"/{quote(segment, safe='')}" for segment in sprite_path_segments)
    return (
        "https://api.mapbox.com/styles/v1/{owner}/{style_id}{extra}/sprite{retina}.{file_type}"
        "?access_token={token}"
    ).format(
        owner=quote(owner, safe=""),
        style_id=quote(style_id, safe=""),
        extra=extra_sprite_path,
        retina=retina_suffix,
        file_type=file_type,
        token=quote(token, safe=""),
    )


BACKGROUND_LAYER_PREFIX = "qfit background"
DEFAULT_BACKGROUND_PRESET = "Outdoor"
DEFAULT_MAPBOX_TILE_SIZE = 512
DEFAULT_MAPBOX_RETINA = False
DEFAULT_MAPBOX_TILE_PIXEL_RATIO = 2
WEB_MERCATOR_WORLD_WIDTH_M = 40075016.685578488
DEFAULT_MAPBOX_MIN_ZOOM = 0
DEFAULT_MAPBOX_MAX_ZOOM = 22
_MAPBOX_PIXEL_TO_MM = 25.4 / 96.0
_MAX_LINE_WIDTH_MM = 3.0  # ~11px at 96 DPI — sane max for cartographic lines and blur widths
QGIS_TEXT_FONT_FALLBACK = "Noto Sans"
_ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE = object()
_LAYOUT_SIMPLIFICATION_NOT_AVAILABLE = object()
_ICON_IMAGE_EMPTY_MATCH_FALLBACKS_BY_LAYER = {
    "gate-label": "gate",
}
_ICON_IMAGE_GET_MATCH_FALLBACKS_BY_LAYER_FIELD = {
    ("airport-label", "maki"): {
        "fallback": "airport",
        "values": ("airport", "airfield", "heliport", "rocket"),
    },
    ("natural-point-label", "maki"): {
        "fallback": "marker",
        "values": ("marker", "mountain", "volcano", "waterfall"),
    },
    ("transit-label", "network"): {
        "fallback": "rail",
        "input": ["get", "maki"],
        "values": ("bicycle-share", "bus", "entrance", "ferry", "rail", "rail-light", "rail-metro"),
    },
}

_POI_LABEL_ICON_IMAGE = [
    "case",
    ["has", "maki_beta"],
    ["coalesce", ["image", ["get", "maki_beta"]], ["image", ["get", "maki"]]],
    ["image", ["get", "maki"]],
]
_POI_LABEL_MAKI_ICON_VALUES = (
    # Complete Mapbox Streets v8 poi_label.maki value set. Each value is also
    # present in the Mapbox Outdoors sprite sheet used by qfit's runtime path.
    "alcohol-shop",
    "american-football",
    "amusement-park",
    "aquarium",
    "art-gallery",
    "attraction",
    "bakery",
    "bank",
    "bar",
    "basketball",
    "beach",
    "beer",
    "bicycle",
    "bowling-alley",
    "bridge",
    "cafe",
    "campsite",
    "car",
    "car-rental",
    "car-repair",
    "casino",
    "castle",
    "cemetery",
    "charging-station",
    "cinema",
    "clothing-store",
    "college",
    "communications-tower",
    "confectionery",
    "convenience",
    "dentist",
    "doctor",
    "dog-park",
    "drinking-water",
    "embassy",
    "farm",
    "fast-food",
    "fire-station",
    "fitness-centre",
    "fuel",
    "furniture",
    "garden",
    "globe",
    "golf",
    "grocery",
    "harbor",
    "hardware",
    "horse-riding",
    "hospital",
    "ice-cream",
    "information",
    "jewelry-store",
    "laundry",
    "library",
    "lodging",
    "marker",
    "mobile-phone",
    "monument",
    "museum",
    "music",
    "optician",
    "park",
    "parking",
    "parking-garage",
    "pharmacy",
    "picnic-site",
    "pitch",
    "place-of-worship",
    "playground",
    "police",
    "post",
    "prison",
    "ranger-station",
    "religious-buddhist",
    "religious-christian",
    "religious-jewish",
    "religious-muslim",
    "restaurant",
    "restaurant-noodle",
    "restaurant-pizza",
    "restaurant-seafood",
    "school",
    "shoe",
    "shop",
    "skateboard",
    "slipway",
    "stadium",
    "suitcase",
    "swimming",
    "table-tennis",
    "tennis",
    "theatre",
    "toilet",
    "town-hall",
    "veterinary",
    "viewpoint",
    "volleyball",
    "watch",
    "watermill",
    "windmill",
    "zoo",
)

_ROAD_NUMBER_SHIELD_LAYER_ID = "road-number-shield"
_ROAD_EXIT_SHIELD_LAYER_ID = "road-exit-shield"
_ROAD_EXIT_SHIELD_ICON_IMAGE = ["concat", "motorway-exit-", ["to-string", ["get", "reflen"]]]
_BOUNDARY_BG_LINE_OPACITY_LAYER_IDS = {"admin-0-boundary-bg", "admin-1-boundary-bg"}
_AIRPORT_LABEL_LAYER_ID = "airport-label"
_TRANSIT_LABEL_LAYER_ID = "transit-label"
_TRANSIT_LABEL_STOP_TYPE_EXCLUSION = ["!=", ["get", "stop_type"], "entrance"]
_TRANSIT_LABEL_ENTRANCE_TEXT_ANCHOR = ["match", ["get", "stop_type"], "entrance", "left", "top"]
_TRANSIT_LABEL_ENTRANCE_TEXT_JUSTIFY = ["match", ["get", "stop_type"], "entrance", "left", "center"]
_TRANSIT_LABEL_ENTRANCE_TEXT_OFFSET = [
    "match",
    ["get", "stop_type"],
    "entrance",
    ["literal", [1, 0]],
    ["literal", [0, 0.8]],
]
_TRANSIT_LABEL_ENTRANCE_TEXT_MAX_WIDTH = ["match", ["get", "stop_type"], "entrance", 15, 9]
_TRANSIT_LABEL_NON_ENTRANCE_LAYOUT_VALUES = {
    "text-anchor": (_TRANSIT_LABEL_ENTRANCE_TEXT_ANCHOR, "top"),
    "text-justify": (_TRANSIT_LABEL_ENTRANCE_TEXT_JUSTIFY, "center"),
    "text-offset": (_TRANSIT_LABEL_ENTRANCE_TEXT_OFFSET, [0, 0.8]),
    "text-max-width": (_TRANSIT_LABEL_ENTRANCE_TEXT_MAX_WIDTH, 9.0),
}
_ROAD_SHIELD_SPRITE_BASES_BY_REFLEN = {
    2: (
        "al-motorway",
        "ch-motorway",
        "cy-motorway",
        "de-motorway",
        "default",
        "hu-motorway",
        "it-motorway",
        "pk-motorway",
        "rectangle-blue",
        "rectangle-green",
        "rectangle-red",
        "rectangle-white",
        "rectangle-yellow",
        "si-motorway",
        "th-motorway",
        "th-motorway-toll",
    ),
    3: (
        "ch-motorway",
        "cy-motorway",
        "de-motorway",
        "default",
        "gr-motorway",
        "hr-motorway",
        "hu-motorway",
        "it-motorway",
        "pk-motorway",
        "rectangle-blue",
        "rectangle-green",
        "rectangle-red",
        "rectangle-white",
        "rectangle-yellow",
        "tr-motorway",
    ),
    4: (
        "default",
        "gr-motorway",
        "hr-motorway",
        "rectangle-blue",
        "rectangle-green",
        "rectangle-red",
        "rectangle-white",
        "rectangle-yellow",
        "tr-motorway",
    ),
    5: (
        "default",
        "rectangle-blue",
        "rectangle-green",
        "rectangle-red",
        "rectangle-white",
        "rectangle-yellow",
        "tr-motorway",
    ),
    6: (
        "default",
        "rectangle-blue",
        "rectangle-green",
        "rectangle-red",
        "rectangle-white",
        "rectangle-yellow",
        "tr-motorway",
    ),
}

_BACKGROUND_PRESETS = {
    "Outdoor": {
        "style_owner": "mapbox",
        "style_id": "outdoors-v12",
        "description": "Mapbox Outdoors",
        "requires_custom_style": False,
    },
    "Light": {
        "style_owner": "mapbox",
        "style_id": "light-v11",
        "description": "Mapbox Light",
        "requires_custom_style": False,
    },
    "Satellite": {
        "style_owner": "mapbox",
        "style_id": "satellite-streets-v12",
        "description": "Mapbox Satellite Streets",
        "requires_custom_style": False,
    },
    "Winter (custom style)": {
        "style_owner": "",
        "style_id": "",
        "description": "Your own winter-themed Mapbox Studio style",
        "requires_custom_style": True,
    },
    "Custom": {
        "style_owner": "",
        "style_id": "",
        "description": "Any Mapbox Studio style owner/style_id",
        "requires_custom_style": True,
    },
}


def background_preset_names() -> list[str]:
    return list(_BACKGROUND_PRESETS.keys())


def get_background_preset(name: str | None) -> dict[str, object]:
    return _BACKGROUND_PRESETS.get(name or DEFAULT_BACKGROUND_PRESET, _BACKGROUND_PRESETS[DEFAULT_BACKGROUND_PRESET])


def preset_requires_custom_style(name: str | None) -> bool:
    preset = get_background_preset(name)
    return bool(preset["requires_custom_style"])


def preset_defaults(name: str | None) -> tuple[str, str]:
    preset = get_background_preset(name)
    return str(preset["style_owner"]), str(preset["style_id"])


def resolve_background_style(
    preset_name: str | None,
    style_owner: str = "",
    style_id: str = "",
) -> tuple[str, str]:
    preset = get_background_preset(preset_name)
    if not preset_requires_custom_style(preset_name):
        return preset_defaults(preset_name)

    owner = style_owner.strip()
    resolved_style_id = style_id.strip()
    if not owner or not resolved_style_id:
        raise MapboxConfigError(
            "Enter a Mapbox style owner and style ID for the selected background preset."
        )
    return owner, resolved_style_id


def build_mapbox_tiles_url(
    access_token: str,
    style_owner: str,
    style_id: str,
    *,
    tile_size: int = DEFAULT_MAPBOX_TILE_SIZE,
    retina: bool = DEFAULT_MAPBOX_RETINA,
) -> str:
    token = access_token.strip()
    owner = style_owner.strip()
    resolved_style_id = style_id.strip()

    if not token:
        raise MapboxConfigError("Enter a Mapbox access token to load the selected background map.")
    if not owner or not resolved_style_id:
        raise MapboxConfigError("Enter a Mapbox style owner and style ID first.")

    retina_suffix = "@2x" if retina else ""
    return (
        "https://api.mapbox.com/styles/v1/{owner}/{style_id}/tiles/{tile_size}/{{z}}/{{x}}/{{y}}{retina}"
        "?access_token={token}"
    ).format(
        owner=quote(owner, safe=""),
        style_id=quote(resolved_style_id, safe=""),
        tile_size=tile_size,
        retina=retina_suffix,
        token=quote(token, safe=""),
    )


TILE_MODE_RASTER = "Raster"
TILE_MODE_VECTOR = "Vector"
TILE_MODES = [TILE_MODE_RASTER, TILE_MODE_VECTOR]


def build_xyz_layer_uri(access_token: str, style_owner: str, style_id: str) -> str:
    url = build_mapbox_tiles_url(access_token, style_owner, style_id)
    return "type=xyz&url={url}&zmin=0&zmax=22&tilePixelRatio={tile_pixel_ratio}".format(
        url=quote(url, safe=":/?{}=%@"),
        tile_pixel_ratio=DEFAULT_MAPBOX_TILE_PIXEL_RATIO,
    )


def native_web_mercator_resolution_for_zoom(
    zoom_level: int,
    *,
    tile_size: int = DEFAULT_MAPBOX_TILE_SIZE,
) -> float:
    normalized_zoom_level = max(DEFAULT_MAPBOX_MIN_ZOOM, int(zoom_level))
    return WEB_MERCATOR_WORLD_WIDTH_M / float(tile_size * (2 ** normalized_zoom_level))


def nearest_native_web_mercator_zoom_level(
    resolution_m_per_pixel: float,
    *,
    tile_size: int = DEFAULT_MAPBOX_TILE_SIZE,
    min_zoom: int = DEFAULT_MAPBOX_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAPBOX_MAX_ZOOM,
) -> int:
    if resolution_m_per_pixel <= 0:
        return int(min_zoom)

    zoom_levels = range(int(min_zoom), int(max_zoom) + 1)
    return min(
        zoom_levels,
        key=lambda zoom_level: abs(
            native_web_mercator_resolution_for_zoom(zoom_level, tile_size=tile_size)
            - float(resolution_m_per_pixel)
        ),
    )


def snap_web_mercator_bounds_to_native_zoom(
    bounds: tuple[float, float, float, float],
    viewport_width_px: int,
    viewport_height_px: int,
    *,
    tile_size: int = DEFAULT_MAPBOX_TILE_SIZE,
    min_zoom: int = DEFAULT_MAPBOX_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAPBOX_MAX_ZOOM,
) -> tuple[tuple[float, float, float, float], int]:
    min_x, min_y, max_x, max_y = bounds
    width_m = max(float(max_x) - float(min_x), 0.0)
    height_m = max(float(max_y) - float(min_y), 0.0)
    width_px = max(int(viewport_width_px or 0), 1)
    height_px = max(int(viewport_height_px or 0), 1)

    current_resolution = max(width_m / float(width_px), height_m / float(height_px), 0.0)
    snapped_zoom_level = nearest_native_web_mercator_zoom_level(
        current_resolution,
        tile_size=tile_size,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
    )
    snapped_resolution = native_web_mercator_resolution_for_zoom(
        snapped_zoom_level,
        tile_size=tile_size,
    )

    center_x = (float(min_x) + float(max_x)) / 2.0
    center_y = (float(min_y) + float(max_y)) / 2.0
    snapped_width_m = snapped_resolution * float(width_px)
    snapped_height_m = snapped_resolution * float(height_px)
    snapped_bounds = (
        center_x - (snapped_width_m / 2.0),
        center_y - (snapped_height_m / 2.0),
        center_x + (snapped_width_m / 2.0),
        center_y + (snapped_height_m / 2.0),
    )
    return snapped_bounds, snapped_zoom_level


def _validated_mapbox_style_parts(access_token: str, style_owner: str, style_id: str) -> tuple[str, str, str]:
    token = access_token.strip()
    owner = style_owner.strip()
    resolved_style_id = style_id.strip()

    if not token:
        raise MapboxConfigError("Enter a Mapbox access token to load the selected background map.")
    if not owner or not resolved_style_id:
        raise MapboxConfigError("Enter a Mapbox style owner and style ID first.")
    return token, owner, resolved_style_id


def fetch_mapbox_style_definition(
    access_token: str,
    style_owner: str,
    style_id: str,
) -> dict[str, object]:
    """Fetch and parse the Mapbox style JSON for a style."""
    style_url = build_mapbox_style_json_url(access_token, style_owner, style_id)
    with urlopen(style_url, timeout=20) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


_REPRESENTATIVE_STYLE_ZOOM = 12.0
_ZOOM_BOUND_EPSILON = 1e-9
_FULL_OPACITY = 1.0
_FULL_OPACITY_EPSILON = 1e-9
_FULL_OPACITY_PROPS = {"fill-opacity", "line-opacity"}
_ZOOM_NORMALIZED_SYMBOL_FILTER_LAYER_IDS = {
    "bridge-oneway-arrow-blue",
    "path-pedestrian-label",
    "road-label",
    "road-number-shield",
    "road-oneway-arrow-blue",
    "settlement-major-label",
    "settlement-minor-label",
    "tunnel-oneway-arrow-blue",
    "transit-label",
}
_ZOOM_NORMALIZED_FILL_FILTER_LAYER_IDS = {
    "hillshade",
    "landuse",
}
_ZOOM_NORMALIZED_LINE_FILTER_LAYER_IDS = {
    "bridge-minor",
    "bridge-minor-case",
    "road-motorway-trunk",
    "road-motorway-trunk-case",
    "road-minor",
    "road-minor-case",
    "tunnel-minor",
    "tunnel-minor-case",
}
_REGIONAL_MAJOR_ROAD_WIDTH_LAYER_IDS = {
    "road-motorway-trunk",
    "road-motorway-trunk-case",
    "road-primary",
    "road-primary-case",
    "road-secondary-tertiary",
    "road-secondary-tertiary-case",
}
_REGIONAL_MAJOR_ROAD_STROKE_WIDTH_PROPS = {"line-width", "line-gap-width"}
_REGIONAL_CORE_ROAD_WIDTH_LAYER_IDS = {
    "road-motorway-trunk",
    "road-motorway-trunk-case",
    "road-primary",
    "road-primary-case",
}
_MAJOR_LINK_WIDTH_LAYER_IDS = {
    "bridge-major-link",
    "bridge-major-link-2",
    "bridge-major-link-2-case",
    "bridge-major-link-case",
    "road-major-link",
    "road-major-link-case",
    "tunnel-major-link",
    "tunnel-major-link-case",
}
_MAJOR_LINK_WIDTH_PROPS = {"line-width", "line-gap-width"}
_MAJOR_LINK_WIDTH_MINIMUM_MM_BY_PROP = {
    "line-width": 0.1,
    "line-gap-width": 0.0,
}
_ROAD_CLASS_LINE_COLOR_VARIANTS_BY_LAYER_ID = {
    "bridge-major-link": (("motorway_link", "motorway-link"), ("trunk_link", "trunk-link")),
    "bridge-major-link-2": (("motorway_link", "motorway-link"), ("trunk_link", "trunk-link")),
    "bridge-motorway-trunk": (("motorway", "motorway"), ("trunk", "trunk")),
    "bridge-motorway-trunk-2": (("motorway", "motorway"), ("trunk", "trunk")),
    "road-major-link": (("motorway_link", "motorway-link"), ("trunk_link", "trunk-link")),
    "road-motorway-trunk": (("motorway", "motorway"), ("trunk", "trunk")),
    "tunnel-major-link": (("motorway_link", "motorway-link"), ("trunk_link", "trunk-link")),
    "tunnel-motorway-trunk": (("motorway", "motorway"), ("trunk", "trunk")),
}
_ROAD_CLASS_LINE_COLOR_SUFFIXES = tuple(
    sorted(
        {suffix for variants in _ROAD_CLASS_LINE_COLOR_VARIANTS_BY_LAYER_ID.values() for _class_value, suffix in variants},
        key=len,
        reverse=True,
    )
)
_ROAD_CLASS_LINE_COLOR_MIN_ZOOM = 12.0
_MAJOR_LINK_WIDTH_BANDS: tuple[tuple[str, float | None, float | None, float], ...] = (
    ("z12-to-z16", 12.0, 16.0, 14.0),
    ("z16-plus", 16.0, None, 16.0),
)
_REGIONAL_MAJOR_ROAD_WIDTH_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z3-to-z5", None, 5.0),
    ("z5-to-z6", 5.0, 6.0),
    ("z6-to-z9", 6.0, 9.0),
    ("z9-to-z12", 9.0, 12.0),
)
_REGIONAL_MAJOR_ROAD_WIDTH_HIGH_ZOOM_MIN = 12.0
_REGIONAL_MAJOR_ROAD_WIDTH_QGIS_SCALE = 1.3
_REGIONAL_MAJOR_ROAD_MIN_WIDTH_MM = 0.32
_REGIONAL_CORE_ROAD_WIDTH_QGIS_SCALE = 2.1
_REGIONAL_CORE_ROAD_MIN_WIDTH_MM = 0.60
_PATH_TYPE_FILTER_SPLIT_LAYER_IDS = {
    "bridge-path-bg",
    "road-path",
    "road-path-bg",
}
_PATH_BACKGROUND_LINE_COLOR_LAYER_IDS = {
    "bridge-path-bg",
    "road-path-bg",
}
_PATH_BACKGROUND_LINE_COLOR_EXPRESSION = [
    "match",
    ["get", "type"],
    "piste",
    "hsl(215, 80%, 48%)",
    ["mountain_bike", "hiking", "trail", "cycleway", "footway", "path", "bridleway"],
    "hsl(35, 80%, 48%)",
    "hsl(60, 1%, 64%)",
]
_PATH_BACKGROUND_OUTDOOR_TYPES = ["mountain_bike", "hiking", "trail", "cycleway", "footway", "path", "bridleway"]
_PATH_BACKGROUND_LINE_COLOR_VARIANTS: tuple[tuple[str, object, str], ...] = (
    ("piste", ["match", ["get", "type"], "piste", True, False], "hsl(215, 80%, 48%)"),
    (
        "outdoor",
        ["match", ["get", "type"], _PATH_BACKGROUND_OUTDOOR_TYPES, True, False],
        "hsl(35, 80%, 48%)",
    ),
    (
        "remaining",
        ["match", ["get", "type"], ["piste", *_PATH_BACKGROUND_OUTDOOR_TYPES], False, True],
        "hsl(60, 1%, 64%)",
    ),
)
_PATH_TYPE_FILTER_LOW_ZOOM_TYPES = ["steps", "sidewalk", "crossing"]
_PATH_TYPE_FILTER_LOW_ZOOM_INVERTED_MATCH = [
    "!",
    ["match", ["get", "type"], _PATH_TYPE_FILTER_LOW_ZOOM_TYPES, True, False],
]
_PATH_TYPE_FILTER_LOW_ZOOM_SIMPLIFIED_MATCH = [
    "match",
    ["get", "type"],
    _PATH_TYPE_FILTER_LOW_ZOOM_TYPES,
    False,
    True,
]
_PATH_TYPE_FILTER_SPLIT_ZOOM = 16.0
_NATURAL_POINT_LABEL_LAYER_ID = "natural-point-label"
_POI_LABEL_LAYER_ID = "poi-label"
_GATE_LABEL_LAYER_ID = "gate-label"
_CONTINENT_LABEL_LAYER_ID = "continent-label"
_COUNTRY_LABEL_LAYER_ID = "country-label"
_SETTLEMENT_MAJOR_LABEL_LAYER_ID = "settlement-major-label"
_SETTLEMENT_MINOR_LABEL_LAYER_ID = "settlement-minor-label"
_GATE_LABEL_ICON_IMAGE_EXPRESSION = ["match", ["get", "type"], "gate", "gate", "lift_gate", "lift-gate", ""]
_GATE_LABEL_ICON_IMAGE_VARIANTS: tuple[tuple[str, object, str], ...] = (
    ("gate", ["==", ["get", "type"], "gate"], "gate"),
    ("lift-gate", ["==", ["get", "type"], "lift_gate"], "lift-gate"),
)
_POI_FILTER_RANK_ZOOM_STEP = ["step", ["zoom"], 0, 16, 1, 17, 2]
_POI_FILTER_RANK_ZOOM_BANDS: tuple[tuple[str, float | None, float | None, float], ...] = (
    ("below-z16", None, 16.0, 0.0),
    ("z16-to-z17", 16.0, 17.0, 1.0),
    ("z17-plus", 17.0, None, 2.0),
)
_LABEL_ICON_VISIBILITY_SPLIT_ZOOM = 17.0
_LABEL_ICON_LOW_ZOOM_SIZERANK_THRESHOLD = 5.0
_LABEL_ICON_HIGH_ZOOM_SIZERANK_THRESHOLD = 13.0
_LABEL_ICON_OPACITY_EXPRESSION = [
    "step",
    ["zoom"],
    ["step", ["get", "sizerank"], 0, _LABEL_ICON_LOW_ZOOM_SIZERANK_THRESHOLD, 1],
    _LABEL_ICON_VISIBILITY_SPLIT_ZOOM,
    ["step", ["get", "sizerank"], 0, _LABEL_ICON_HIGH_ZOOM_SIZERANK_THRESHOLD, 1],
]
_LABEL_ICON_TEXT_ANCHOR_EXPRESSION = [
    "step",
    ["zoom"],
    ["step", ["get", "sizerank"], "center", _LABEL_ICON_LOW_ZOOM_SIZERANK_THRESHOLD, "top"],
    _LABEL_ICON_VISIBILITY_SPLIT_ZOOM,
    ["step", ["get", "sizerank"], "center", _LABEL_ICON_HIGH_ZOOM_SIZERANK_THRESHOLD, "top"],
]
_LABEL_ICON_TEXT_OFFSET_EXPRESSION = [
    "step",
    ["zoom"],
    ["step", ["get", "sizerank"], ["literal", [0, 0]], _LABEL_ICON_LOW_ZOOM_SIZERANK_THRESHOLD, ["literal", [0, 0.8]]],
    _LABEL_ICON_VISIBILITY_SPLIT_ZOOM,
    ["step", ["get", "sizerank"], ["literal", [0, 0]], _LABEL_ICON_HIGH_ZOOM_SIZERANK_THRESHOLD, ["literal", [0, 0.8]]],
]
_LABEL_ICON_VISIBILITY_ZOOM_BANDS: tuple[tuple[str, float | None, float | None, float], ...] = (
    ("below-z17", None, _LABEL_ICON_VISIBILITY_SPLIT_ZOOM, _LABEL_ICON_LOW_ZOOM_SIZERANK_THRESHOLD),
    ("z17-plus", _LABEL_ICON_VISIBILITY_SPLIT_ZOOM, None, _LABEL_ICON_HIGH_ZOOM_SIZERANK_THRESHOLD),
)
_SETTLEMENT_DOT_ICON_SPLIT_ZOOM = 8.0
_SETTLEMENT_SYMBOL_SORT_KEY_EXPRESSION = ["get", "symbolrank"]
_SETTLEMENT_DOT_ICON_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z2-to-z4", 2.0, 4.0),
    ("z4-to-z6", 4.0, 6.0),
    ("z6-to-z7", 6.0, 7.0),
    ("z7-to-z8", 7.0, _SETTLEMENT_DOT_ICON_SPLIT_ZOOM),
)
_SETTLEMENT_DOT_ICON_IMAGE_EXPRESSION = [
    "step",
    ["zoom"],
    [
        "case",
        ["==", ["get", "capital"], 2],
        "border-dot-13",
        ["step", ["get", "symbolrank"], "dot-11", 9, "dot-10", 11, "dot-9"],
    ],
    _SETTLEMENT_DOT_ICON_SPLIT_ZOOM,
    "",
]
_SETTLEMENT_DOT_TEXT_ANCHOR_EXPRESSION = ["step", ["zoom"], ["get", "text_anchor"], _SETTLEMENT_DOT_ICON_SPLIT_ZOOM, "center"]
_SETTLEMENT_DOT_TEXT_RADIAL_OFFSET_EXPRESSION = [
    "step",
    ["zoom"],
    ["match", ["get", "capital"], 2, 0.6, 0.55],
    _SETTLEMENT_DOT_ICON_SPLIT_ZOOM,
    0,
]
_SETTLEMENT_DOT_LEFT_TEXT_ANCHORS = ["left", "bottom-left", "top-left"]
_SETTLEMENT_DOT_RIGHT_TEXT_ANCHORS = ["right", "bottom-right", "top-right"]
_SETTLEMENT_DOT_SIDE_TEXT_ANCHORS = [*_SETTLEMENT_DOT_LEFT_TEXT_ANCHORS, *_SETTLEMENT_DOT_RIGHT_TEXT_ANCHORS]
_SETTLEMENT_DOT_TEXT_JUSTIFY_LOW_ZOOM = [
    "match",
    ["get", "text_anchor"],
    _SETTLEMENT_DOT_LEFT_TEXT_ANCHORS,
    "left",
    _SETTLEMENT_DOT_RIGHT_TEXT_ANCHORS,
    "right",
    "center",
]
_SETTLEMENT_DOT_TEXT_JUSTIFY_EXPRESSION = [
    "step",
    ["zoom"],
    _SETTLEMENT_DOT_TEXT_JUSTIFY_LOW_ZOOM,
    _SETTLEMENT_DOT_ICON_SPLIT_ZOOM,
    "center",
]
_SETTLEMENT_DOT_ICON_VARIANTS: tuple[tuple[str, object, str, float], ...] = (
    ("capital-border-dot", ["==", ["get", "capital"], 2], "border-dot-13", 0.6),
    (
        "dot-11",
        ["all", ["!=", ["get", "capital"], 2], ["<", ["get", "symbolrank"], 9]],
        "dot-11",
        0.55,
    ),
    (
        "dot-10",
        ["all", ["!=", ["get", "capital"], 2], [">=", ["get", "symbolrank"], 9], ["<", ["get", "symbolrank"], 11]],
        "dot-10",
        0.55,
    ),
    ("dot-9", ["all", ["!=", ["get", "capital"], 2], [">=", ["get", "symbolrank"], 11]], "dot-9", 0.55),
)
_SETTLEMENT_MAJOR_LOW_ZOOM_TEXT_JUSTIFY_VARIANTS: tuple[tuple[str, object, str], ...] = (
    (
        "left",
        ["match", ["get", "text_anchor"], _SETTLEMENT_DOT_LEFT_TEXT_ANCHORS, True, False],
        "left",
    ),
    (
        "right",
        ["match", ["get", "text_anchor"], _SETTLEMENT_DOT_RIGHT_TEXT_ANCHORS, True, False],
        "right",
    ),
    (
        "center",
        ["match", ["get", "text_anchor"], _SETTLEMENT_DOT_SIDE_TEXT_ANCHORS, False, True],
        "center",
    ),
)
_COUNTRY_LABEL_LEFT_TEXT_ANCHORS = ["left", "bottom-left", "top-left"]
_COUNTRY_LABEL_RIGHT_TEXT_ANCHORS = ["right", "bottom-right", "top-right"]
_COUNTRY_LABEL_SIDE_TEXT_ANCHORS = [*_COUNTRY_LABEL_LEFT_TEXT_ANCHORS, *_COUNTRY_LABEL_RIGHT_TEXT_ANCHORS]
_COUNTRY_LABEL_LAYOUT_TEXT_JUSTIFY_EXPRESSION = [
    "step",
    ["zoom"],
    [
        "match",
        ["get", "text_anchor"],
        _COUNTRY_LABEL_LEFT_TEXT_ANCHORS,
        "left",
        _COUNTRY_LABEL_RIGHT_TEXT_ANCHORS,
        "right",
        "center",
    ],
    7,
    "auto",
]
_COUNTRY_LABEL_LAYOUT_TEXT_RADIAL_OFFSET_EXPRESSION = ["step", ["zoom"], 0.6, 8, 0]
_COUNTRY_LABEL_BELOW_Z7_BAND_SUFFIX = "below-z7"
_COUNTRY_LABEL_LAYOUT_ZOOM_BANDS: tuple[tuple[str, float | None, float | None, str | None, float | None], ...] = (
    (_COUNTRY_LABEL_BELOW_Z7_BAND_SUFFIX, None, 7.0, None, 0.6),
    ("z7-to-z8", 7.0, 8.0, "auto", 0.6),
    ("z8-plus", 8.0, None, "auto", 0.0),
)
_COUNTRY_LABEL_LOW_ZOOM_TEXT_JUSTIFY_VARIANTS: tuple[tuple[str, object, str], ...] = (
    (
        "left",
        ["match", ["get", "text_anchor"], _COUNTRY_LABEL_LEFT_TEXT_ANCHORS, True, False],
        "left",
    ),
    (
        "right",
        ["match", ["get", "text_anchor"], _COUNTRY_LABEL_RIGHT_TEXT_ANCHORS, True, False],
        "right",
    ),
    (
        "center",
        ["match", ["get", "text_anchor"], _COUNTRY_LABEL_SIDE_TEXT_ANCHORS, False, True],
        "center",
    ),
)
_CONTINENT_LABEL_TEXT_OPACITY_EXPRESSION = [
    "interpolate",
    ["linear"],
    ["zoom"],
    0,
    0.8,
    1.5,
    0.5,
    2.5,
    0,
]
_CONTINENT_LABEL_TEXT_OPACITY_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("below-z1_5", None, 1.5),
    ("z1_5-to-z2_5", 1.5, 2.5),
    ("z2_5-plus", 2.5, None),
)
_CLIFF_LAYER_ID = "cliff"
_CLIFF_LINE_PATTERN = "cliff"
_CLIFF_LINE_PATTERN_FALLBACK_COLOR = "#388a0f"
_CLIFF_LINE_PATTERN_FALLBACK_DASHARRAY = [1.0, 0.75]
_CLIFF_LINE_PATTERN_FALLBACK_WIDTH = 1.5
_CLIFF_LINE_OPACITY_EXPRESSION = [
    "interpolate",
    ["linear"],
    ["zoom"],
    15,
    0,
    15.25,
    1,
]
_CLIFF_LINE_OPACITY_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z15-to-z15_25", 15.0, 15.25),
    ("z15_25-plus", 15.25, None),
)
_BUILDING_FILL_OPACITY_EXPRESSIONS = {
    "building": ["interpolate", ["linear"], ["zoom"], 15, 0, 16, 1],
    "building-underground": ["interpolate", ["linear"], ["zoom"], 15, 0, 16, 0.5],
}
_BUILDING_FILL_OPACITY_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z15-to-z16", 15.0, 16.0),
    ("z16-plus", 16.0, None),
)
_HILLSHADE_LAYER_ID = "hillshade"
_HILLSHADE_SHADOW_FILL_COLOR = "hsla(66, 38%, 17%, 0.08)"
_HILLSHADE_HIGHLIGHT_FILL_COLOR = "hsla(60, 20%, 95%, 0.14)"
_HILLSHADE_HIGHLIGHT_MIN_ZOOM = 11.0
_HILLSHADE_CLASS_SPLIT_MAX_ZOOM = 13.0
_HILLSHADE_FILL_COLOR_EXPRESSION = [
    "interpolate",
    ["linear"],
    ["zoom"],
    14,
    [
        "match",
        ["get", "class"],
        "shadow",
        _HILLSHADE_SHADOW_FILL_COLOR,
        _HILLSHADE_HIGHLIGHT_FILL_COLOR,
    ],
    16,
    [
        "match",
        ["get", "class"],
        "shadow",
        "hsla(66, 38%, 17%, 0)",
        "hsla(60, 20%, 95%, 0)",
    ],
]
_LANDCOVER_LAYER_ID = "landcover"
_LANDCOVER_FILL_OPACITY_EXPRESSIONS = {
    _LANDCOVER_LAYER_ID: ["interpolate", ["exponential", 1.5], ["zoom"], 8, 0.8, 12, 0],
}
_LANDCOVER_FILL_OPACITY_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("below-z8", None, 8.0),
    ("z8-to-z10", 8.0, 10.0),
    ("z10-to-z12", 10.0, 12.0),
)
_LANDUSE_LAYER_ID = "landuse"
_LANDUSE_WOOD_FILL_COLOR = "hsla(103, 50%, 60%, 0.8)"
_LANDUSE_SCRUB_FILL_COLOR = "hsla(98, 47%, 68%, 0.6)"
_LANDUSE_AGRICULTURE_FILL_COLOR = "hsla(98, 50%, 74%, 0.6)"
_LANDUSE_PARK_SPECIAL_FILL_COLOR = "hsl(98, 38%, 68%)"
_LANDUSE_PARK_FILL_COLOR = "hsl(98, 55%, 70%)"
_LANDUSE_AIRPORT_FILL_COLOR = "hsl(230, 40%, 82%)"
_LANDUSE_CEMETERY_FILL_COLOR = "hsl(98, 45%, 75%)"
_LANDUSE_GLACIER_FILL_COLOR = "hsl(205, 45%, 95%)"
_LANDUSE_HOSPITAL_FILL_COLOR = "hsl(20, 45%, 82%)"
_LANDUSE_PITCH_FILL_COLOR = "hsl(88, 65%, 75%)"
_LANDUSE_SAND_FILL_COLOR = "hsl(69, 60%, 72%)"
_LANDUSE_ROCK_FILL_COLOR = "hsl(60, 0%, 85%)"
_LANDUSE_ROCK_HIGH_ZOOM_FILL_COLOR = "hsla(60, 0%, 85%, 0.5)"
_LANDUSE_SCHOOL_FILL_COLOR = "hsl(40, 45%, 78%)"
_LANDUSE_COMMERCIAL_AREA_FILL_COLOR = "hsl(55, 45%, 85%)"
_LANDUSE_COMMERCIAL_AREA_HIGH_ZOOM_FILL_COLOR = "hsla(55, 45%, 85%, 0.5)"
_LANDUSE_RESIDENTIAL_FILL_COLOR = "hsl(60, 7%, 87%)"
_LANDUSE_INDUSTRIAL_FILL_COLOR = "hsl(230, 20%, 85%)"
_LANDUSE_FALLBACK_FILL_COLOR = "hsl(60, 22%, 72%)"
_LANDUSE_LOW_ZOOM_FILL_COLORS = [
    "match",
    ["get", "class"],
    "wood",
    _LANDUSE_WOOD_FILL_COLOR,
    "scrub",
    _LANDUSE_SCRUB_FILL_COLOR,
    "agriculture",
    _LANDUSE_AGRICULTURE_FILL_COLOR,
    "park",
    [
        "match",
        ["get", "type"],
        ["garden", "playground", "zoo"],
        _LANDUSE_PARK_SPECIAL_FILL_COLOR,
        _LANDUSE_PARK_FILL_COLOR,
    ],
    "grass",
    _LANDUSE_AGRICULTURE_FILL_COLOR,
    "airport",
    _LANDUSE_AIRPORT_FILL_COLOR,
    "cemetery",
    _LANDUSE_CEMETERY_FILL_COLOR,
    "glacier",
    _LANDUSE_GLACIER_FILL_COLOR,
    "hospital",
    _LANDUSE_HOSPITAL_FILL_COLOR,
    "pitch",
    _LANDUSE_PITCH_FILL_COLOR,
    "sand",
    _LANDUSE_SAND_FILL_COLOR,
    "rock",
    _LANDUSE_ROCK_FILL_COLOR,
    "school",
    _LANDUSE_SCHOOL_FILL_COLOR,
    "commercial_area",
    _LANDUSE_COMMERCIAL_AREA_FILL_COLOR,
    "residential",
    _LANDUSE_RESIDENTIAL_FILL_COLOR,
    ["facility", "industrial"],
    _LANDUSE_INDUSTRIAL_FILL_COLOR,
    _LANDUSE_FALLBACK_FILL_COLOR,
]
# This intentionally mirrors the live Mapbox Outdoors z16 stop. Residential is
# absent in Mapbox's expression here, and qfit's residential z10+ opacity band is
# fully transparent.
_LANDUSE_HIGH_ZOOM_FILL_COLORS = [
    "match",
    ["get", "class"],
    "wood",
    _LANDUSE_WOOD_FILL_COLOR,
    "scrub",
    _LANDUSE_SCRUB_FILL_COLOR,
    "agriculture",
    _LANDUSE_AGRICULTURE_FILL_COLOR,
    "park",
    [
        "match",
        ["get", "type"],
        ["garden", "playground", "zoo"],
        _LANDUSE_PARK_SPECIAL_FILL_COLOR,
        _LANDUSE_PARK_FILL_COLOR,
    ],
    "grass",
    _LANDUSE_AGRICULTURE_FILL_COLOR,
    "airport",
    _LANDUSE_AIRPORT_FILL_COLOR,
    "cemetery",
    _LANDUSE_CEMETERY_FILL_COLOR,
    "glacier",
    _LANDUSE_GLACIER_FILL_COLOR,
    "hospital",
    _LANDUSE_HOSPITAL_FILL_COLOR,
    "pitch",
    _LANDUSE_PITCH_FILL_COLOR,
    "sand",
    _LANDUSE_SAND_FILL_COLOR,
    "rock",
    _LANDUSE_ROCK_HIGH_ZOOM_FILL_COLOR,
    "school",
    _LANDUSE_SCHOOL_FILL_COLOR,
    "commercial_area",
    _LANDUSE_COMMERCIAL_AREA_HIGH_ZOOM_FILL_COLOR,
    ["facility", "industrial"],
    _LANDUSE_INDUSTRIAL_FILL_COLOR,
    _LANDUSE_FALLBACK_FILL_COLOR,
]
_LANDUSE_FILL_COLOR_EXPRESSION = [
    "interpolate",
    ["linear"],
    ["zoom"],
    15,
    _LANDUSE_LOW_ZOOM_FILL_COLORS,
    16,
    _LANDUSE_HIGH_ZOOM_FILL_COLORS,
]
_LANDUSE_FILL_OPACITY_EXPRESSION = [
    "interpolate",
    ["linear"],
    ["zoom"],
    8,
    ["match", ["get", "class"], "residential", 0.8, 0.2],
    10,
    ["match", ["get", "class"], "residential", 0, 1],
]
_LANDUSE_FILL_OPACITY_VARIANTS: tuple[
    tuple[str, object, float | None, float | None, bool],
    ...,
] = (
    (
        "residential-below-z8",
        ["match", ["get", "class"], "residential", True, False],
        None,
        8.0,
        True,
    ),
    (
        "residential-z8-to-z10",
        ["match", ["get", "class"], "residential", True, False],
        8.0,
        10.0,
        True,
    ),
    (
        "residential-z10-plus",
        ["match", ["get", "class"], "residential", True, False],
        10.0,
        None,
        True,
    ),
    (
        "other-below-z8",
        ["match", ["get", "class"], "residential", False, True],
        None,
        8.0,
        False,
    ),
    (
        "other-z8-to-z10",
        ["match", ["get", "class"], "residential", False, True],
        8.0,
        10.0,
        False,
    ),
    (
        "other-z10-plus",
        ["match", ["get", "class"], "residential", False, True],
        10.0,
        None,
        False,
    ),
)
_LANDUSE_CLASS_FILL_COLOR_EXCLUDED_OPACITY_SUFFIXES = {"other-below-z8"}
_LANDUSE_CLASS_FILL_COLOR_SPLIT_LAYER_IDS = {
    f"{_LANDUSE_LAYER_ID}-{suffix}"
    for suffix, _class_filter, _band_minzoom, _band_maxzoom, residential in _LANDUSE_FILL_OPACITY_VARIANTS
    if not residential and suffix not in _LANDUSE_CLASS_FILL_COLOR_EXCLUDED_OPACITY_SUFFIXES
}
_LANDUSE_AIRPORT_HIGH_ZOOM_FILL_OPACITY = 0.66
# QGIS renders the full-opacity airport landuse wash too strongly over
# aeroway polygons in the Geneva z14 comparison. Keep this override narrow.
_LANDUSE_CLASS_FILL_OPACITY_OVERRIDES = {
    (f"{_LANDUSE_LAYER_ID}-other-z10-plus", "airport"): _LANDUSE_AIRPORT_HIGH_ZOOM_FILL_OPACITY,
}
_LANDUSE_CLASS_FILL_COLOR_VARIANTS: tuple[tuple[str, object, str], ...] = (
    ("wood", ["match", ["get", "class"], "wood", True, False], _LANDUSE_WOOD_FILL_COLOR),
    ("scrub", ["match", ["get", "class"], "scrub", True, False], _LANDUSE_SCRUB_FILL_COLOR),
    ("agriculture", ["match", ["get", "class"], "agriculture", True, False], _LANDUSE_AGRICULTURE_FILL_COLOR),
    ("grass", ["match", ["get", "class"], "grass", True, False], _LANDUSE_AGRICULTURE_FILL_COLOR),
    ("glacier", ["match", ["get", "class"], "glacier", True, False], _LANDUSE_GLACIER_FILL_COLOR),
    ("sand", ["match", ["get", "class"], "sand", True, False], _LANDUSE_SAND_FILL_COLOR),
    (
        "park-special",
        [
            "all",
            ["match", ["get", "class"], "park", True, False],
            ["match", ["get", "type"], ["garden", "playground", "zoo"], True, False],
        ],
        _LANDUSE_PARK_SPECIAL_FILL_COLOR,
    ),
    (
        "park",
        [
            "all",
            ["match", ["get", "class"], "park", True, False],
            ["match", ["get", "type"], ["garden", "playground", "zoo"], False, True],
        ],
        _LANDUSE_PARK_FILL_COLOR,
    ),
    ("airport", ["match", ["get", "class"], "airport", True, False], _LANDUSE_AIRPORT_FILL_COLOR),
    ("cemetery", ["match", ["get", "class"], "cemetery", True, False], _LANDUSE_CEMETERY_FILL_COLOR),
    ("hospital", ["match", ["get", "class"], "hospital", True, False], _LANDUSE_HOSPITAL_FILL_COLOR),
    ("pitch", ["match", ["get", "class"], "pitch", True, False], _LANDUSE_PITCH_FILL_COLOR),
    ("school", ["match", ["get", "class"], "school", True, False], _LANDUSE_SCHOOL_FILL_COLOR),
    (
        "industrial",
        ["match", ["get", "class"], ["facility", "industrial"], True, False],
        _LANDUSE_INDUSTRIAL_FILL_COLOR,
    ),
    (
        "remaining",
        [
            "match",
            ["get", "class"],
            [
                "wood",
                "scrub",
                "agriculture",
                "grass",
                "glacier",
                "sand",
                "park",
                "airport",
                "cemetery",
                "hospital",
                "pitch",
                "school",
                "facility",
                "industrial",
            ],
            False,
            True,
        ],
        _LANDUSE_FALLBACK_FILL_COLOR,
    ),
)
_LANDCOVER_CROP_FILL_COLOR = "hsla(68, 55%, 70%, 0.6)"
_LANDCOVER_FALLBACK_FILL_COLOR = "hsl(98, 48%, 67%)"
_LANDCOVER_FILL_COLOR_EXPRESSION = [
    "match",
    ["get", "class"],
    "wood",
    _LANDUSE_WOOD_FILL_COLOR,
    "scrub",
    _LANDUSE_SCRUB_FILL_COLOR,
    "crop",
    _LANDCOVER_CROP_FILL_COLOR,
    "grass",
    _LANDUSE_AGRICULTURE_FILL_COLOR,
    "snow",
    _LANDUSE_GLACIER_FILL_COLOR,
    _LANDCOVER_FALLBACK_FILL_COLOR,
]
_LANDCOVER_CLASS_FILL_COLOR_SPLIT_LAYER_IDS = {
    f"{_LANDCOVER_LAYER_ID}-{suffix}" for suffix, _band_minzoom, _band_maxzoom in _LANDCOVER_FILL_OPACITY_ZOOM_BANDS
}
_LANDCOVER_CLASS_FILL_COLOR_VARIANTS: tuple[tuple[str, object, str], ...] = (
    ("wood", ["match", ["get", "class"], "wood", True, False], _LANDUSE_WOOD_FILL_COLOR),
    ("scrub", ["match", ["get", "class"], "scrub", True, False], _LANDUSE_SCRUB_FILL_COLOR),
    ("crop", ["match", ["get", "class"], "crop", True, False], _LANDCOVER_CROP_FILL_COLOR),
    ("grass", ["match", ["get", "class"], "grass", True, False], _LANDUSE_AGRICULTURE_FILL_COLOR),
    ("snow", ["match", ["get", "class"], "snow", True, False], _LANDUSE_GLACIER_FILL_COLOR),
    (
        "remaining",
        ["match", ["get", "class"], ["wood", "scrub", "crop", "grass", "snow"], False, True],
        _LANDCOVER_FALLBACK_FILL_COLOR,
    ),
)
_NATIONAL_PARK_LAYER_ID = "national-park"
_NATIONAL_PARK_FILL_OPACITY_EXPRESSIONS = {
    _NATIONAL_PARK_LAYER_ID: ["interpolate", ["linear"], ["zoom"], 5, 0, 6, 0.6, 12, 0.2],
}
_NATIONAL_PARK_FILL_OPACITY_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z5-to-z6", 5.0, 6.0),
    ("z6-to-z9", 6.0, 9.0),
    ("z9-to-z12", 9.0, 12.0),
    ("z12-plus", 12.0, None),
)
_WETLAND_LAYER_ID = "wetland"
_WETLAND_FILL_OPACITY_EXPRESSIONS = {
    _WETLAND_LAYER_ID: ["interpolate", ["linear"], ["zoom"], 10, 0.25, 10.5, 0.15],
}
_WETLAND_FILL_OPACITY_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("below-z10", None, 10.0),
    ("z10-to-z10_5", 10.0, 10.5),
    ("z10_5-plus", 10.5, None),
)
_ROAD_PEDESTRIAN_POLYGON_PATTERN_LAYER_ID = "road-pedestrian-polygon-pattern"
_ROAD_PEDESTRIAN_POLYGON_PATTERN_FILL_OPACITY_EXPRESSIONS = {
    _ROAD_PEDESTRIAN_POLYGON_PATTERN_LAYER_ID: ["interpolate", ["linear"], ["zoom"], 16, 0, 17, 1],
}
_ROAD_PEDESTRIAN_POLYGON_PATTERN_FILL_OPACITY_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z16-to-z17", 16.0, 17.0),
    ("z17-plus", 17.0, None),
)
_RAIL_TRACK_LINE_OPACITY_EXPRESSION = ["interpolate", ["linear"], ["zoom"], 13.75, 0, 14, 1]
_RAIL_TRACK_LINE_OPACITY_EXPRESSIONS = {
    "road-rail-tracks": copy.deepcopy(_RAIL_TRACK_LINE_OPACITY_EXPRESSION),
    "bridge-rail-tracks": copy.deepcopy(_RAIL_TRACK_LINE_OPACITY_EXPRESSION),
}
_RAIL_TRACK_LINE_OPACITY_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("below-z13_75", None, 13.75),
    ("z13_75-to-z14", 13.75, 14.0),
    ("z14-plus", 14.0, None),
)
_GATE_FENCE_HEDGE_LAYER_ID = "gate-fence-hedge"
_GATE_FENCE_HEDGE_LINE_OPACITY_EXPRESSION = ["match", ["get", "class"], "gate", 0.5, 1]
_GATE_FENCE_HEDGE_LINE_OPACITY_VARIANTS: tuple[tuple[str, object, float], ...] = (
    ("gate", ["match", ["get", "class"], "gate", True, False], 0.5),
    ("fence-hedge", ["match", ["get", "class"], "gate", False, True], 1.0),
)
_WATER_SHADOW_TRANSLATE_EXPRESSION = [
    "interpolate",
    ["exponential", 1.2],
    ["zoom"],
    7,
    ["literal", [0, 0]],
    16,
    ["literal", [-1, -1]],
]
_WATER_SHADOW_TRANSLATE_LAYERS: dict[str, tuple[str, str]] = {
    "water-shadow": ("fill", "fill-translate"),
    "waterway-shadow": ("line", "line-translate"),
}
_WATER_SHADOW_TRANSLATE_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z10-to-z13", 10.0, 13.0),
    ("z13-to-z16", 13.0, 16.0),
    ("z16-plus", 16.0, None),
)
_AEROWAY_POLYGON_LAYER_ID = "aeroway-polygon"
_AEROWAY_POLYGON_FILL_COLOR = "hsl(230, 36%, 74%)"
_AEROWAY_POLYGON_QGIS_CONTRAST_FILL_COLOR = "hsl(230, 36%, 70%)"
_AEROWAY_LINE_LAYER_ID = "aeroway-line"
_AEROWAY_LINE_WIDTH_EXPRESSION = [
    "interpolate",
    ["exponential", 1.5],
    ["zoom"],
    9,
    ["match", ["get", "type"], "runway", 1, 0.5],
    18,
    ["match", ["get", "type"], "runway", 80, 20],
]
_AEROWAY_LINE_WIDTH_TYPE_VARIANTS: tuple[tuple[str, object, float, float], ...] = (
    ("runway", ["match", ["get", "type"], "runway", True, False], 1.0, 80.0),
    ("other", ["match", ["get", "type"], "runway", False, True], 0.5, 20.0),
)
_AEROWAY_LINE_WIDTH_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z9-to-z14", 9.0, 14.0),
    ("z14-to-z16", 14.0, 16.0),
    ("z16-plus", 16.0, None),
)
_WATERWAY_LAYER_ID = "waterway"
_WATERWAY_SHADOW_LAYER_ID = "waterway-shadow"
_WATERWAY_LINE_WIDTH_EXPRESSION = [
    "interpolate",
    ["exponential", 1.3],
    ["zoom"],
    9,
    ["match", ["get", "class"], ["canal", "river"], 0.1, 0],
    20,
    ["match", ["get", "class"], ["canal", "river"], 8, 3],
]
_WATERWAY_LINE_WIDTH_CLASS_VARIANTS: tuple[tuple[str, object, float, float], ...] = (
    ("canal-river", ["match", ["get", "class"], ["canal", "river"], True, False], 0.1, 8.0),
    ("other", ["match", ["get", "class"], ["canal", "river"], False, True], 0.0, 3.0),
)
_WATERWAY_LINE_WIDTH_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z8-to-z13", 8.0, 13.0),
    ("z13-to-z16", 13.0, 16.0),
    ("z16-plus", 16.0, None),
)
_TURNING_FEATURE_LAYER_ID = "turning-feature"
_TURNING_FEATURE_OUTLINE_LAYER_ID = "turning-feature-outline"
_TURNING_FEATURE_CIRCLE_RADIUS_EXPRESSION = [
    "interpolate",
    ["exponential", 1.5],
    ["zoom"],
    15,
    4.5,
    16,
    8,
    18,
    20,
    22,
    200,
]
_TURNING_FEATURE_CIRCLE_STROKE_WIDTH_EXPRESSION = ["interpolate", ["linear"], ["zoom"], 15, 0.8, 16, 1.2, 18, 2]
_TURNING_FEATURE_CIRCLE_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z15-to-z16", 15.0, 16.0),
    ("z16-to-z18", 16.0, 18.0),
    ("z18-to-z22", 18.0, 22.0),
    ("z22-plus", 22.0, None),
)
_WATERWAY_LABEL_LAYER_ID = "waterway-label"
_WATERWAY_LABEL_SYMBOL_SPACING_EXPRESSION = [
    "interpolate",
    ["linear", 1],
    ["zoom"],
    15,
    250,
    17,
    400,
]
_WATERWAY_LABEL_SYMBOL_SPACING_ZOOM_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("z13-to-z15", 13.0, 15.0),
    ("z15-to-z17", 15.0, 17.0),
    ("z17-plus", 17.0, None),
)
_WATER_LINE_LABEL_LAYER_ID = "water-line-label"
_WATER_POINT_LABEL_LAYER_ID = "water-point-label"
_WATER_LABEL_TYPOGRAPHY_LAYER_IDS = (
    _WATER_LINE_LABEL_LAYER_ID,
    _WATER_POINT_LABEL_LAYER_ID,
)
_WATER_LINE_LABEL_TEXT_LETTER_SPACING_EXPRESSION = [
    "match",
    ["get", "class"],
    "ocean",
    0.25,
    ["sea", "bay"],
    0.15,
    0,
]
_WATER_POINT_LABEL_TEXT_LETTER_SPACING_EXPRESSION = [
    "match",
    ["get", "class"],
    "ocean",
    0.25,
    ["bay", "sea"],
    0.15,
    0.01,
]
_WATER_POINT_LABEL_TEXT_MAX_WIDTH_EXPRESSION = [
    "match",
    ["get", "class"],
    "ocean",
    4,
    "sea",
    5,
    ["bay", "water"],
    7,
    10,
]
_WATER_LINE_LABEL_TYPOGRAPHY_VARIANTS: tuple[tuple[str, object, float], ...] = (
    ("ocean", ["match", ["get", "class"], ["ocean"], True, False], 0.25),
    ("sea-bay", ["match", ["get", "class"], ["sea", "bay"], True, False], 0.15),
    ("other", ["match", ["get", "class"], ["ocean", "sea", "bay"], False, True], 0.0),
)
_WATER_POINT_LABEL_TYPOGRAPHY_VARIANTS: tuple[tuple[str, object, float, float], ...] = (
    ("ocean", ["match", ["get", "class"], ["ocean"], True, False], 0.25, 4.0),
    ("sea", ["match", ["get", "class"], ["sea"], True, False], 0.15, 5.0),
    ("bay", ["match", ["get", "class"], ["bay"], True, False], 0.15, 7.0),
    ("water", ["match", ["get", "class"], ["water"], True, False], 0.01, 7.0),
    ("other", ["match", ["get", "class"], ["ocean", "sea", "bay", "water"], False, True], 0.01, 10.0),
)
_CONTOUR_LINE_LAYER_ID = "contour-line"
_CONTOUR_LINE_OPACITY_EXPRESSION = [
    "interpolate",
    ["linear"],
    ["zoom"],
    11,
    ["match", ["get", "index"], [1, 2], 0.15, 0.3],
    13,
    ["match", ["get", "index"], [1, 2], 0.3, 0.5],
]
_CONTOUR_LINE_OPACITY_VARIANTS: tuple[
    tuple[str, object, float | None, float | None, float],
    ...,
] = (
    ("index-minor-below-z11", ["match", ["get", "index"], [1, 2], True, False], None, 11.0, 0.15),
    ("index-minor-z11-to-z13", ["match", ["get", "index"], [1, 2], True, False], 11.0, 13.0, 0.225),
    ("index-minor-z13-plus", ["match", ["get", "index"], [1, 2], True, False], 13.0, None, 0.3),
    ("index-major-below-z11", ["match", ["get", "index"], [1, 2], False, True], None, 11.0, 0.3),
    ("index-major-z11-to-z13", ["match", ["get", "index"], [1, 2], False, True], 11.0, 13.0, 0.4),
    ("index-major-z13-plus", ["match", ["get", "index"], [1, 2], False, True], 13.0, None, 0.5),
)
_FILTER_NORMALIZATION_ZOOM_OVERRIDES = {
    "bridge-minor": 14.0,
    "bridge-minor-case": 14.0,
    "road-minor": 14.0,
    "road-minor-case": 14.0,
    "tunnel-minor": 14.0,
    "tunnel-minor-case": 14.0,
}
assert set(_FILTER_NORMALIZATION_ZOOM_OVERRIDES).issubset(_ZOOM_NORMALIZED_LINE_FILTER_LAYER_IDS), (
    "Filter zoom overrides must also be present in the line filter normalization allowlist."
)


def _is_literal_color(value: object) -> bool:
    return isinstance(value, str) and (
        value.startswith("hsl") or value.startswith("#") or value.startswith("rgb")
    )


def _zoom_stops(expr: list[object]) -> list[tuple[float, object]]:
    """Return ``(zoom, value)`` pairs for zoom-based Mapbox expressions."""
    if len(expr) < 5 or expr[2] != ["zoom"]:
        return []
    stops: list[tuple[float, object]] = []
    for index in range(3, len(expr) - 1, 2):
        zoom = expr[index]
        if isinstance(zoom, (int, float)):
            stops.append((float(zoom), expr[index + 1]))
    return stops


def _nearest_zoom_stop_value(expr: list[object], target_zoom: float = _REPRESENTATIVE_STYLE_ZOOM) -> object | None:
    stops = _zoom_stops(expr)
    if not stops:
        return None
    return min(stops, key=lambda stop: abs(stop[0] - target_zoom))[1]


def _nearest_interpolate_output_value(
    expr: list[object],
    target_stop: float = _REPRESENTATIVE_STYLE_ZOOM,
) -> object | None:
    """Return a representative output value from any Mapbox interpolate expression."""
    if len(expr) < 5:
        return None
    stops: list[tuple[float, object]] = []
    for index in range(3, len(expr) - 1, 2):
        stop = expr[index]
        if isinstance(stop, (int, float)):
            stops.append((float(stop), expr[index + 1]))
    if not stops:
        return None
    return min(stops, key=lambda stop: abs(stop[0] - target_stop))[1]


def _step_zoom_value(expr: list[object], target_zoom: float = _REPRESENTATIVE_STYLE_ZOOM) -> object | None:
    """Evaluate a simple ``['step', ['zoom'], ...]`` expression at target zoom."""
    if len(expr) < 3 or expr[1] != ["zoom"]:
        return None
    value = expr[2]
    for index in range(3, len(expr) - 1, 2):
        threshold = expr[index]
        if not isinstance(threshold, (int, float)):
            continue
        if target_zoom < float(threshold):
            break
        value = expr[index + 1]
    return value


def _numeric_zoom_bound(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _zoom_in_layer_range(target_zoom: float, minzoom: object, maxzoom: object) -> float | None:
    minimum_zoom = _numeric_zoom_bound(minzoom)
    maximum_zoom = _numeric_zoom_bound(maxzoom)
    if minimum_zoom is not None and maximum_zoom is not None and minimum_zoom >= maximum_zoom:
        return None
    if minimum_zoom is not None and target_zoom < minimum_zoom:
        target_zoom = minimum_zoom
    if maximum_zoom is not None and target_zoom >= maximum_zoom:
        target_zoom = maximum_zoom - _ZOOM_BOUND_EPSILON
    if minimum_zoom is not None and target_zoom < minimum_zoom:
        return None
    return target_zoom


def _representative_zoom_in_layer_range(minzoom: object, maxzoom: object) -> float | None:
    return _zoom_in_layer_range(_REPRESENTATIVE_STYLE_ZOOM, minzoom, maxzoom)


def _has_valid_zoom_step_thresholds(expr: list[object]) -> bool:
    return all(
        not isinstance(expr[index], bool) and isinstance(expr[index], (int, float))
        for index in range(3, len(expr) - 1, 2)
    )


def _step_outputs(expr: list[object]) -> list[object]:
    return [expr[2], *(expr[index + 1] for index in range(3, len(expr) - 1, 2))]


def _step_has_only_non_empty_literal_outputs(expr: list[object]) -> bool:
    return all(isinstance(output, str) and output for output in _step_outputs(expr))


def _step_has_future_icon_outputs(expr: list[object], target_zoom: float) -> bool:
    return any(
        float(expr[index]) > target_zoom and expr[index + 1] != ""
        for index in range(3, len(expr) - 1, 2)
    )


def _literal_step_icon_image(expr: object, *, minzoom: object = None, maxzoom: object = None) -> object:
    if not isinstance(expr, list) or len(expr) < 3 or expr[0] != "step" or expr[1] != ["zoom"]:
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    if not _has_valid_zoom_step_thresholds(expr):
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    target_zoom = _representative_zoom_in_layer_range(minzoom, maxzoom)
    if target_zoom is None:
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    representative = _step_zoom_value(expr, target_zoom=target_zoom)
    if not isinstance(representative, str):
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    if not representative:
        if _step_has_future_icon_outputs(expr, target_zoom):
            return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
        return representative
    if _step_has_only_non_empty_literal_outputs(expr):
        return representative
    return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE


def _icon_image_empty_match_fallback(layer_id: object, expr: object) -> object:
    """Replace known empty icon-image match fallbacks with existing sprites.

    Mapbox uses an empty fallback on the Outdoors gate-label layer, but QGIS'
    converter still tries to retrieve a sprite named ``""``. Limit this to
    audited layers whose other match outputs are literal sprite names.
    """
    fallback = _ICON_IMAGE_EMPTY_MATCH_FALLBACKS_BY_LAYER.get(str(layer_id or ""))
    if fallback is None or not isinstance(expr, list) or len(expr) < 5 or expr[0] != "match":
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    if expr[-1] != "":
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    outputs = [expr[index] for index in range(3, len(expr) - 1, 2)]
    if outputs and all(isinstance(output, str) and output for output in outputs):
        updated = copy.deepcopy(expr)
        updated[-1] = fallback
        return updated
    return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE


def _icon_image_get_fallback(layer_id: object, expr: object) -> object:
    """Replace audited icon-image get expressions with literal sprite match fallbacks."""
    if not isinstance(expr, list) or len(expr) != 2 or expr[0] != "get" or not isinstance(expr[1], str):
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    match_fallback = _ICON_IMAGE_GET_MATCH_FALLBACKS_BY_LAYER_FIELD.get((str(layer_id or ""), expr[1]))
    if match_fallback is None:
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    values = match_fallback["values"]
    fallback = match_fallback["fallback"]
    input_expr = match_fallback.get("input", expr)
    return ["match", copy.deepcopy(input_expr), *(item for value in values for item in (value, value)), fallback]


def _maki_icon_match(values: tuple[str, ...], fallback: str) -> list[object]:
    return ["match", ["get", "maki"], *(item for value in values for item in (value, value)), fallback]


def _poi_label_icon_image_fallback(layer_id: object, expr: object) -> object:
    """Replace Mapbox Outdoors' dynamic POI sprite expression with audited maki sprites."""
    if str(layer_id or "") != _POI_LABEL_LAYER_ID or expr != _POI_LABEL_ICON_IMAGE:
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    # The live z8-z18 #949 camera sample only observed maki_beta=terminal, which
    # is not present in the Outdoors sprite sheet. Fall back to the base maki
    # field so QGIS can render finite sprite matches instead of dropping icons.
    return _maki_icon_match(_POI_LABEL_MAKI_ICON_VALUES, "marker")


def _road_exit_shield_icon_fallback(layer_id: object, expr: object) -> object:
    """Replace Mapbox Outdoors road-exit shield concat sprites with a finite match."""
    if str(layer_id or "") != _ROAD_EXIT_SHIELD_LAYER_ID or expr != _ROAD_EXIT_SHIELD_ICON_IMAGE:
        return _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE
    # QGIS validates the match fallback against the loaded sprite sheet up front,
    # even though audited road-exit features use reflen values in the 1..9 range.
    # Keep the fallback on an existing sprite instead of a missing sentinel so this
    # replacement does not reintroduce a sprite-retrieval warning. Malformed or
    # out-of-range reflen values therefore fall back to the one-character shield.
    return [
        "match",
        ["get", "reflen"],
        *(item for reflen in range(1, 10) for item in (reflen, f"motorway-exit-{reflen}")),
        "motorway-exit-1",
    ]


def _expression_references_get_field(expr: object, field_name: str) -> bool:
    if isinstance(expr, list):
        if expr == ["get", field_name]:
            return True
        return any(_expression_references_get_field(item, field_name) for item in expr[1:])
    return False


def _is_road_number_shield_icon_image(expr: object) -> bool:
    return (
        isinstance(expr, list)
        and len(expr) == 4
        and expr[0] == "case"
        and expr[1] == ["has", "shield_beta"]
        and _expression_references_get_field(expr, "reflen")
        and _expression_references_get_field(expr, "shield")
        and _expression_references_get_field(expr, "shield_beta")
    )


def _road_shield_icon_match(field_name: str, reflen: int) -> list[object]:
    fallback = f"default-{reflen}"
    values = _ROAD_SHIELD_SPRITE_BASES_BY_REFLEN[reflen]
    return [
        "match",
        ["get", field_name],
        *(item for value in values for item in (value, f"{value}-{reflen}")),
        fallback,
    ]


def _with_additional_filter_clauses(filter_value: object, *clauses: object) -> object:
    filter_copy = copy.deepcopy(filter_value)
    if isinstance(filter_copy, list) and filter_copy[:1] == ["all"]:
        return [*filter_copy, *(copy.deepcopy(clause) for clause in clauses)]
    if isinstance(filter_copy, list):
        return ["all", filter_copy, *(copy.deepcopy(clause) for clause in clauses)]
    return ["all", *(copy.deepcopy(clause) for clause in clauses)]


def _filter_contains_clause(filter_value: object, clause: object) -> bool:
    if filter_value == clause:
        return True
    if isinstance(filter_value, list) and filter_value[:1] == ["all"]:
        return any(_filter_contains_clause(child, clause) for child in filter_value[1:])
    return False


def _transit_label_non_entrance_layout_value(
    prop: str,
    value: object,
    filter_value: object,
) -> object:
    """Collapse transit-label entrance layout matches when entrances are filtered out."""
    if not _filter_contains_clause(filter_value, _TRANSIT_LABEL_STOP_TYPE_EXCLUSION):
        return _LAYOUT_SIMPLIFICATION_NOT_AVAILABLE
    expected = _TRANSIT_LABEL_NON_ENTRANCE_LAYOUT_VALUES.get(prop)
    if expected is None:
        return _LAYOUT_SIMPLIFICATION_NOT_AVAILABLE
    expression, literal_value = expected
    if value != expression:
        return _LAYOUT_SIMPLIFICATION_NOT_AVAILABLE
    return copy.deepcopy(literal_value)


def _is_path_type_low_zoom_filter(value: object) -> bool:
    return value in (_PATH_TYPE_FILTER_LOW_ZOOM_INVERTED_MATCH, _PATH_TYPE_FILTER_LOW_ZOOM_SIMPLIFIED_MATCH)


def _is_path_type_high_zoom_filter(value: object) -> bool:
    return value == ["!=", ["get", "type"], "steps"]


def _path_type_zoom_step_filter_clause(value: object) -> tuple[float, object, object] | None:
    if not isinstance(value, list) or len(value) != 5 or value[0] != "step" or value[1] != ["zoom"]:
        return None
    threshold = _numeric_zoom_bound(value[3])
    if threshold is None or abs(threshold - _PATH_TYPE_FILTER_SPLIT_ZOOM) > _ZOOM_BOUND_EPSILON:
        return None
    low_zoom_filter = value[2]
    high_zoom_filter = value[4]
    if not _is_path_type_low_zoom_filter(low_zoom_filter) or not _is_path_type_high_zoom_filter(high_zoom_filter):
        return None
    return threshold, low_zoom_filter, high_zoom_filter


def _filter_with_replaced_clause(filter_value: object, clause_index: int, replacement: object) -> object:
    filter_copy = copy.deepcopy(filter_value)
    if isinstance(filter_copy, list) and filter_copy[:1] == ["all"] and 0 < clause_index < len(filter_copy):
        filter_copy[clause_index] = copy.deepcopy(replacement)
        return filter_copy
    return copy.deepcopy(replacement)


def _zoom_band_label(zoom: float) -> str:
    return str(int(zoom)) if zoom.is_integer() else str(zoom).replace(".", "_")


def _path_type_filter_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited path filters at their Mapbox zoom threshold for QGIS.

    QGIS cannot parse the Mapbox ``step(['zoom'], ...)`` filter clause used by
    the Outdoors path layers.  A single representative snapshot either hides
    sidewalks/crossings at high zoom or renders them too early at mid zoom, so
    preserve the Mapbox behavior by emitting two static zoom-band layers.
    """
    layer_id = str(layer.get("id") or "")
    if layer_id not in _PATH_TYPE_FILTER_SPLIT_LAYER_IDS or layer.get("type") != "line":
        return None
    filter_value = layer.get("filter")
    if not isinstance(filter_value, list) or filter_value[:1] != ["all"]:
        return None

    for clause_index, clause in enumerate(filter_value[1:], start=1):
        path_type_clause = _path_type_zoom_step_filter_clause(clause)
        if path_type_clause is None:
            continue

        threshold, low_zoom_filter, high_zoom_filter = path_type_clause
        existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
        existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
        if existing_maxzoom is not None and existing_maxzoom <= threshold:
            low_layer = copy.deepcopy(layer)
            low_layer["filter"] = _filter_with_replaced_clause(filter_value, clause_index, low_zoom_filter)
            return [low_layer]
        if existing_minzoom is not None and existing_minzoom >= threshold:
            high_layer = copy.deepcopy(layer)
            high_layer["filter"] = _filter_with_replaced_clause(filter_value, clause_index, high_zoom_filter)
            return [high_layer]

        zoom_label = _zoom_band_label(threshold)
        low_layer = copy.deepcopy(layer)
        low_layer["id"] = f"{layer_id}-below-z{zoom_label}"
        low_layer["filter"] = _filter_with_replaced_clause(filter_value, clause_index, low_zoom_filter)
        low_layer["maxzoom"] = threshold

        high_layer = copy.deepcopy(layer)
        high_layer["id"] = f"{layer_id}-z{zoom_label}-plus"
        high_layer["filter"] = _filter_with_replaced_clause(filter_value, clause_index, high_zoom_filter)
        high_layer["minzoom"] = threshold
        return [low_layer, high_layer]
    return None


def _split_path_type_filter_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _path_type_filter_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _path_background_line_color_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited path background casing colors into QGIS-safe type layers."""
    base_layer_id = _path_background_line_color_base_layer_id(layer.get("id"))
    paint = layer.get("paint")
    if (
        base_layer_id is None
        or layer.get("type") != "line"
        or not isinstance(paint, dict)
        or paint.get("line-color") != _PATH_BACKGROUND_LINE_COLOR_EXPRESSION
    ):
        return None
    layer_id = str(layer.get("id") or base_layer_id)
    variants: list[dict[str, object]] = []
    for suffix, type_filter, line_color in _PATH_BACKGROUND_LINE_COLOR_VARIANTS:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{layer_id}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), type_filter)
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["line-color"] = line_color
        variants.append(variant)
    return variants or None


def _split_path_background_line_color_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _path_background_line_color_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _numeric_match_filter_with_offset(value: object, offset: float) -> object | None:
    if not isinstance(value, list) or len(value) < 5 or value[0] != "match" or (len(value) - 3) % 2 != 0:
        return None
    adjusted = ["match", copy.deepcopy(value[1])]
    for index in range(2, len(value) - 1, 2):
        output_value = _numeric_expression_value(value[index + 1])
        if output_value is None:
            return None
        adjusted.extend([copy.deepcopy(value[index]), output_value + offset])
    fallback_value = _numeric_expression_value(value[-1])
    if fallback_value is None:
        return None
    adjusted.append(fallback_value + offset)
    return adjusted


def _poi_filterrank_components(filter_value: object) -> tuple[object, object] | None:
    if (
        not isinstance(filter_value, list)
        or len(filter_value) != 3
        or filter_value[0] != "<="
        or filter_value[1] != ["get", "filterrank"]
    ):
        return None
    threshold = filter_value[2]
    if not isinstance(threshold, list) or len(threshold) != 3 or threshold[0] != "+":
        return None
    left, right = threshold[1], threshold[2]
    if left == _POI_FILTER_RANK_ZOOM_STEP:
        return left, right
    if right == _POI_FILTER_RANK_ZOOM_STEP:
        return right, left
    return None


def _zoom_ranges_overlap(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
) -> bool:
    if existing_maxzoom is not None and band_minzoom is not None and existing_maxzoom <= band_minzoom:
        return False
    if existing_minzoom is not None and band_maxzoom is not None and existing_minzoom >= band_maxzoom:
        return False
    return True


def _apply_zoom_band_bounds(
    layer: dict[str, object],
    band_minzoom: float | None,
    band_maxzoom: float | None,
) -> dict[str, object]:
    bounded_layer = copy.deepcopy(layer)
    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    if band_minzoom is not None and (existing_minzoom is None or existing_minzoom < band_minzoom):
        bounded_layer["minzoom"] = band_minzoom
    if band_maxzoom is not None and (existing_maxzoom is None or existing_maxzoom > band_maxzoom):
        bounded_layer["maxzoom"] = band_maxzoom
    return bounded_layer


def _poi_label_filter_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split the audited Outdoors POI filterrank zoom bump into QGIS-safe bands."""
    if str(layer.get("id") or "") != _POI_LABEL_LAYER_ID or layer.get("type") != "symbol":
        return None
    components = _poi_filterrank_components(layer.get("filter"))
    if components is None:
        return None
    _, class_rank_match = components
    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants_with_suffixes: list[tuple[str, dict[str, object]]] = []
    for suffix, band_minzoom, band_maxzoom, rank_offset in _POI_FILTER_RANK_ZOOM_BANDS:
        if not _zoom_ranges_overlap(existing_minzoom, existing_maxzoom, band_minzoom, band_maxzoom):
            continue
        adjusted_rank_match = _numeric_match_filter_with_offset(class_rank_match, rank_offset)
        if adjusted_rank_match is None:
            return None
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["filter"] = ["<=", ["get", "filterrank"], adjusted_rank_match]
        variants_with_suffixes.append((suffix, variant))
    variants = [variant for _suffix, variant in variants_with_suffixes]
    if len(variants) > 1:
        for suffix, variant in variants_with_suffixes:
            variant["id"] = f"{_POI_LABEL_LAYER_ID}-{suffix}"
    return variants or None


def _split_poi_label_filter_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _poi_label_filter_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _road_number_shield_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    if str(layer.get("id") or "") != _ROAD_NUMBER_SHIELD_LAYER_ID:
        return None
    layout = layer.get("layout")
    if not isinstance(layout, dict) or not _is_road_number_shield_icon_image(layout.get("icon-image")):
        return None

    variants: list[dict[str, object]] = []
    for reflen in sorted(_ROAD_SHIELD_SPRITE_BASES_BY_REFLEN):
        beta_layer = copy.deepcopy(layer)
        beta_layer["id"] = f"{_ROAD_NUMBER_SHIELD_LAYER_ID}-{reflen}-beta"
        beta_layer["filter"] = _with_additional_filter_clauses(
            layer.get("filter"),
            ["==", ["get", "reflen"], reflen],
            ["has", "shield_beta"],
        )
        beta_layer["layout"]["icon-image"] = _road_shield_icon_match("shield_beta", reflen)
        variants.append(beta_layer)

        shield_layer = copy.deepcopy(layer)
        shield_layer["id"] = f"{_ROAD_NUMBER_SHIELD_LAYER_ID}-{reflen}"
        shield_layer["filter"] = _with_additional_filter_clauses(
            layer.get("filter"),
            ["==", ["get", "reflen"], reflen],
            ["!", ["has", "shield_beta"]],
        )
        shield_layer["layout"]["icon-image"] = _road_shield_icon_match("shield", reflen)
        variants.append(shield_layer)
    return variants


def _is_road_number_shield_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _ROAD_NUMBER_SHIELD_LAYER_ID or normalized.startswith(f"{_ROAD_NUMBER_SHIELD_LAYER_ID}-")


def _is_poi_label_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _POI_LABEL_LAYER_ID or normalized.startswith(f"{_POI_LABEL_LAYER_ID}-")


def _is_gate_label_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _GATE_LABEL_LAYER_ID or normalized.startswith(f"{_GATE_LABEL_LAYER_ID}-")


def _is_natural_point_label_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _NATURAL_POINT_LABEL_LAYER_ID or normalized.startswith(f"{_NATURAL_POINT_LABEL_LAYER_ID}-")


def _is_continent_label_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _CONTINENT_LABEL_LAYER_ID or normalized.startswith(f"{_CONTINENT_LABEL_LAYER_ID}-")


def _is_cliff_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _CLIFF_LAYER_ID or normalized.startswith(f"{_CLIFF_LAYER_ID}-")


def _is_country_label_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _COUNTRY_LABEL_LAYER_ID or normalized.startswith(f"{_COUNTRY_LABEL_LAYER_ID}-")


def _is_settlement_major_label_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _SETTLEMENT_MAJOR_LABEL_LAYER_ID or normalized.startswith(f"{_SETTLEMENT_MAJOR_LABEL_LAYER_ID}-")


def _is_settlement_minor_label_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _SETTLEMENT_MINOR_LABEL_LAYER_ID or normalized.startswith(f"{_SETTLEMENT_MINOR_LABEL_LAYER_ID}-")


def _landcover_fill_opacity_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    if normalized == _LANDCOVER_LAYER_ID:
        return _LANDCOVER_LAYER_ID
    for suffix, _band_minzoom, _band_maxzoom in _LANDCOVER_FILL_OPACITY_ZOOM_BANDS:
        opacity_variant_id = f"{_LANDCOVER_LAYER_ID}-{suffix}"
        if normalized == opacity_variant_id or normalized.startswith(f"{opacity_variant_id}-"):
            return _LANDCOVER_LAYER_ID
    return None


def _landuse_fill_opacity_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    if normalized == _LANDUSE_LAYER_ID:
        return _LANDUSE_LAYER_ID
    for suffix, _class_filter, _band_minzoom, _band_maxzoom, _fill_opacity in _LANDUSE_FILL_OPACITY_VARIANTS:
        opacity_variant_id = f"{_LANDUSE_LAYER_ID}-{suffix}"
        if normalized == opacity_variant_id or normalized.startswith(f"{opacity_variant_id}-"):
            return _LANDUSE_LAYER_ID
    return None


def _path_background_line_color_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    for base_layer_id in _PATH_BACKGROUND_LINE_COLOR_LAYER_IDS:
        if normalized == base_layer_id or normalized.startswith(f"{base_layer_id}-"):
            return base_layer_id
    return None


def _regional_major_road_width_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    for suffix, _band_minzoom, _band_maxzoom in _REGIONAL_MAJOR_ROAD_WIDTH_BANDS:
        band_suffix = f"-{suffix}"
        if normalized.endswith(band_suffix):
            base_layer_id = normalized[: -len(band_suffix)]
            if base_layer_id in _REGIONAL_MAJOR_ROAD_WIDTH_LAYER_IDS:
                return base_layer_id
    return None


def _major_link_width_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    for suffix, _band_minzoom, _band_maxzoom, _target_zoom in _MAJOR_LINK_WIDTH_BANDS:
        band_suffix = f"-{suffix}"
        if normalized.endswith(band_suffix):
            base_layer_id = normalized[: -len(band_suffix)]
            if base_layer_id in _MAJOR_LINK_WIDTH_LAYER_IDS:
                return base_layer_id
    return None


def _hillshade_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    if normalized in {
        _HILLSHADE_LAYER_ID,
        f"{_HILLSHADE_LAYER_ID}-shadow",
        f"{_HILLSHADE_LAYER_ID}-highlight",
        f"{_HILLSHADE_LAYER_ID}-z13-plus",
    }:
        return _HILLSHADE_LAYER_ID
    return None


def _water_label_typography_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    for base_layer_id in _WATER_LABEL_TYPOGRAPHY_LAYER_IDS:
        if normalized == base_layer_id or normalized.startswith(f"{base_layer_id}-"):
            return base_layer_id
    return None


def _waterway_line_width_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    if normalized == _WATERWAY_LAYER_ID or normalized.startswith(f"{_WATERWAY_LAYER_ID}-canal-river-"):
        return _WATERWAY_LAYER_ID
    if normalized.startswith(f"{_WATERWAY_LAYER_ID}-other-"):
        return _WATERWAY_LAYER_ID
    if normalized == _WATERWAY_SHADOW_LAYER_ID or normalized.startswith(f"{_WATERWAY_SHADOW_LAYER_ID}-"):
        return _WATERWAY_SHADOW_LAYER_ID
    return None


def _aeroway_line_width_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    if normalized == _AEROWAY_LINE_LAYER_ID:
        return _AEROWAY_LINE_LAYER_ID
    for type_suffix, _class_filter, _lower_width, _upper_width in _AEROWAY_LINE_WIDTH_TYPE_VARIANTS:
        if normalized.startswith(f"{_AEROWAY_LINE_LAYER_ID}-{type_suffix}-"):
            return _AEROWAY_LINE_LAYER_ID
    return None


def base_mapbox_style_layer_id_for_qfit(layer_id: object) -> str:
    """Return the original Mapbox layer id for qfit-created layer variants."""
    for resolved_layer_id in (
        _road_class_line_color_base_layer_id(layer_id),
        _hillshade_base_layer_id(layer_id),
        _aeroway_line_width_base_layer_id(layer_id),
        _waterway_line_width_base_layer_id(layer_id),
        _water_label_typography_base_layer_id(layer_id),
        _regional_major_road_width_base_layer_id(layer_id),
        _major_link_width_base_layer_id(layer_id),
        _landcover_fill_opacity_base_layer_id(layer_id),
        _landuse_fill_opacity_base_layer_id(layer_id),
        _path_background_line_color_base_layer_id(layer_id),
    ):
        if resolved_layer_id is not None:
            return resolved_layer_id
    for matches_layer_id, base_layer_id in (
        (_is_waterway_label_layer_id, _WATERWAY_LABEL_LAYER_ID),
        (_is_road_number_shield_layer_id, _ROAD_NUMBER_SHIELD_LAYER_ID),
        (_is_poi_label_layer_id, _POI_LABEL_LAYER_ID),
        (_is_gate_label_layer_id, _GATE_LABEL_LAYER_ID),
        (_is_natural_point_label_layer_id, _NATURAL_POINT_LABEL_LAYER_ID),
        (_is_continent_label_layer_id, _CONTINENT_LABEL_LAYER_ID),
        (_is_cliff_layer_id, _CLIFF_LAYER_ID),
        (_is_country_label_layer_id, _COUNTRY_LABEL_LAYER_ID),
        (_is_settlement_major_label_layer_id, _SETTLEMENT_MAJOR_LABEL_LAYER_ID),
        (_is_settlement_minor_label_layer_id, _SETTLEMENT_MINOR_LABEL_LAYER_ID),
    ):
        if matches_layer_id(layer_id):
            return base_layer_id
    return str(layer_id or "")


def _is_waterway_label_layer_id(layer_id: object) -> bool:
    normalized = str(layer_id or "")
    return normalized == _WATERWAY_LABEL_LAYER_ID or normalized.startswith(
        f"{_WATERWAY_LABEL_LAYER_ID}-"
    )


def _is_regional_major_road_width_variant(layer_id: object) -> bool:
    return _regional_major_road_width_base_layer_id(layer_id) is not None


def _regional_major_road_width_scale(layer_id: object) -> float:
    base_layer_id = base_mapbox_style_layer_id_for_qfit(layer_id)
    if base_layer_id in _REGIONAL_CORE_ROAD_WIDTH_LAYER_IDS:
        return _REGIONAL_CORE_ROAD_WIDTH_QGIS_SCALE
    return _REGIONAL_MAJOR_ROAD_WIDTH_QGIS_SCALE


def _regional_major_road_min_width_mm(layer_id: object) -> float:
    base_layer_id = base_mapbox_style_layer_id_for_qfit(layer_id)
    if base_layer_id in _REGIONAL_CORE_ROAD_WIDTH_LAYER_IDS:
        return _REGIONAL_CORE_ROAD_MIN_WIDTH_MM
    return _REGIONAL_MAJOR_ROAD_MIN_WIDTH_MM


def _effective_zoom_band(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
) -> tuple[float | None, float | None] | None:
    effective_minzoom = existing_minzoom
    if band_minzoom is not None:
        effective_minzoom = (
            max(existing_minzoom, band_minzoom)
            if existing_minzoom is not None
            else band_minzoom
        )

    effective_maxzoom = existing_maxzoom
    if band_maxzoom is not None:
        effective_maxzoom = (
            min(existing_maxzoom, band_maxzoom)
            if existing_maxzoom is not None
            else band_maxzoom
        )

    if effective_minzoom is not None and effective_maxzoom is not None and effective_minzoom >= effective_maxzoom:
        return None
    return effective_minzoom, effective_maxzoom


def _set_zoom_bounds(layer: dict[str, object], minzoom: float | None, maxzoom: float | None) -> None:
    if minzoom is None:
        layer.pop("minzoom", None)
    else:
        layer["minzoom"] = minzoom
    if maxzoom is None:
        layer.pop("maxzoom", None)
    else:
        layer["maxzoom"] = maxzoom


def _match_output_for_value(expr: object, value: object) -> object | None:
    if not isinstance(expr, list) or len(expr) < 5 or expr[0] != "match" or (len(expr) - 3) % 2 != 0:
        return None
    for index in range(2, len(expr) - 1, 2):
        candidate = expr[index]
        if candidate == value or (isinstance(candidate, list) and value in candidate):
            return expr[index + 1]
    return expr[-1]


def _line_color_for_road_class(expr: object, class_value: str, target_zoom: float) -> str | None:
    if _is_literal_color(expr):
        return str(expr)
    if not isinstance(expr, list) or not expr:
        return None
    if expr[0] == "step" and expr[1:2] == [["zoom"]]:
        return _line_color_for_road_class(_step_zoom_value(expr, target_zoom=target_zoom), class_value, target_zoom)
    if expr[0] != "match" or expr[1] != ["get", "class"]:
        return None
    output = _match_output_for_value(expr, class_value)
    return str(output) if _is_literal_color(output) else None


def _road_class_line_color_base_layer_id(layer_id: object) -> str | None:
    normalized = str(layer_id or "")
    for suffix in _ROAD_CLASS_LINE_COLOR_SUFFIXES:
        color_suffix = f"-{suffix}"
        if not normalized.endswith(color_suffix):
            continue
        candidate = normalized[: -len(color_suffix)]
        for base_layer_id in (
            _regional_major_road_width_base_layer_id(candidate),
            _major_link_width_base_layer_id(candidate),
            candidate,
        ):
            if base_layer_id in _ROAD_CLASS_LINE_COLOR_VARIANTS_BY_LAYER_ID:
                return base_layer_id
    return None


def _road_class_line_color_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited motorway/trunk class colors into static QGIS layers."""
    base_layer_id = base_mapbox_style_layer_id_for_qfit(layer.get("id"))
    variants = _ROAD_CLASS_LINE_COLOR_VARIANTS_BY_LAYER_ID.get(base_layer_id)
    paint = layer.get("paint")
    if variants is None or layer.get("type") != "line" or not isinstance(paint, dict):
        return None
    line_color = paint.get("line-color")
    if not isinstance(line_color, list):
        return None

    target_zoom = _representative_zoom_in_layer_range(layer.get("minzoom"), layer.get("maxzoom"))
    if target_zoom is None:
        return None
    minimum_zoom = _numeric_zoom_bound(layer.get("minzoom"))
    if minimum_zoom is None or minimum_zoom < _ROAD_CLASS_LINE_COLOR_MIN_ZOOM:
        return None

    resolved_variants: list[tuple[str, str, str]] = []
    for class_value, suffix in variants:
        color = _line_color_for_road_class(line_color, class_value, target_zoom)
        if color is None:
            return None
        resolved_variants.append((class_value, suffix, color))

    if len({color for _class_value, _suffix, color in resolved_variants}) <= 1:
        return None

    layer_id = str(layer.get("id") or base_layer_id)
    class_layers: list[dict[str, object]] = []
    for class_value, suffix, color in resolved_variants:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{layer_id}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(
            layer.get("filter"),
            ["==", ["get", "class"], class_value],
        )
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["line-color"] = color
        class_layers.append(variant)
    return class_layers


def _split_road_class_line_color_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _road_class_line_color_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _regional_major_road_width_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split z3-z12 major road strokes so regional views keep Mapbox widths."""
    base_layer_id = base_mapbox_style_layer_id_for_qfit(layer.get("id"))
    if base_layer_id not in _REGIONAL_MAJOR_ROAD_WIDTH_LAYER_IDS or layer.get("type") != "line":
        return None
    paint = layer.get("paint")
    if not isinstance(paint, dict) or not any(
        isinstance(paint.get(prop), list) for prop in _REGIONAL_MAJOR_ROAD_STROKE_WIDTH_PROPS
    ):
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    if existing_minzoom is not None and existing_minzoom >= _REGIONAL_MAJOR_ROAD_WIDTH_HIGH_ZOOM_MIN:
        return None
    if (
        existing_minzoom is not None
        and existing_maxzoom is not None
        and existing_maxzoom <= existing_minzoom
    ):
        return None

    variants: list[dict[str, object]] = []
    layer_id = str(layer.get("id") or base_layer_id)
    for suffix, band_minzoom, band_maxzoom in _REGIONAL_MAJOR_ROAD_WIDTH_BANDS:
        zoom_band = _effective_zoom_band(existing_minzoom, existing_maxzoom, band_minzoom, band_maxzoom)
        if zoom_band is None:
            continue
        variant = copy.deepcopy(layer)
        variant["id"] = f"{layer_id}-{suffix}"
        _set_zoom_bounds(variant, *zoom_band)
        variants.append(variant)

    high_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        _REGIONAL_MAJOR_ROAD_WIDTH_HIGH_ZOOM_MIN,
        None,
    )
    if not variants:
        return None
    if high_zoom_band is not None:
        high_zoom_variant = copy.deepcopy(layer)
        _set_zoom_bounds(high_zoom_variant, *high_zoom_band)
        variants.append(high_zoom_variant)
    return variants


def _split_regional_major_road_width_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _regional_major_road_width_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _extract_zoom_scalar_size_at_zoom(expr: object, target_zoom: float) -> float | None:
    if isinstance(expr, bool):
        return None
    if isinstance(expr, (int, float)):
        return float(expr)
    if not isinstance(expr, list) or len(expr) < 4:
        return None
    if expr[0] == "step" and expr[1] == ["zoom"]:
        outputs = _step_outputs(expr)
        if all(_numeric_expression_value(output) is not None for output in outputs):
            return _numeric_expression_value(_step_zoom_value(expr, target_zoom=target_zoom))
    if expr[0] == "interpolate" and len(expr) >= 5 and expr[2] == ["zoom"]:
        outputs = [expr[index] for index in range(4, len(expr), 2)]
        if all(_numeric_expression_value(output) is not None for output in outputs):
            return _numeric_expression_value(_interpolate_filter_value_at_zoom(expr, target_zoom))
    return None


def _major_link_width_mm(expr: object, target_zoom: float, *, minimum_mm: float) -> float | None:
    size = _extract_zoom_scalar_size_at_zoom(expr, target_zoom)
    if size is None:
        return None
    return max(minimum_mm, min(size * _MAPBOX_PIXEL_TO_MM, _MAX_LINE_WIDTH_MM))


def _has_major_link_width_expression(layer: dict[str, object]) -> bool:
    layer_id = str(layer.get("id") or "")
    if layer_id not in _MAJOR_LINK_WIDTH_LAYER_IDS or layer.get("type") != "line":
        return False
    paint = layer.get("paint")
    return isinstance(paint, dict) and any(
        isinstance(paint.get(prop), list) for prop in _MAJOR_LINK_WIDTH_PROPS
    )


def _apply_major_link_width_values_for_qgis(paint: dict[str, object], target_zoom: float) -> bool:
    changed = False
    for prop in _MAJOR_LINK_WIDTH_PROPS:
        minimum_mm = _MAJOR_LINK_WIDTH_MINIMUM_MM_BY_PROP[prop]
        width_mm = _major_link_width_mm(paint.get(prop), target_zoom, minimum_mm=minimum_mm)
        if width_mm is not None:
            paint[prop] = width_mm
            changed = True
    return changed


def _major_link_width_layer_variant(
    layer: dict[str, object],
    layer_id: str,
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band: tuple[str, float | None, float | None, float],
) -> dict[str, object] | None:
    suffix, band_minzoom, band_maxzoom, target_zoom = band
    zoom_band = _effective_zoom_band(existing_minzoom, existing_maxzoom, band_minzoom, band_maxzoom)
    if zoom_band is None:
        return None
    target_zoom = _zoom_in_layer_range(target_zoom, *zoom_band)
    if target_zoom is None:
        return None
    variant = copy.deepcopy(layer)
    variant["id"] = f"{layer_id}-{suffix}"
    _set_zoom_bounds(variant, *zoom_band)
    variant_paint = variant.get("paint")
    if not isinstance(variant_paint, dict):
        return None
    return variant if _apply_major_link_width_values_for_qgis(variant_paint, target_zoom) else None


def _major_link_width_passthrough_layer_variant(
    layer: dict[str, object],
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
) -> dict[str, object] | None:
    first_split_minzoom = _MAJOR_LINK_WIDTH_BANDS[0][1]
    if first_split_minzoom is None:
        return None
    zoom_band = _effective_zoom_band(existing_minzoom, existing_maxzoom, None, first_split_minzoom)
    if zoom_band is None:
        return None
    variant = copy.deepcopy(layer)
    _set_zoom_bounds(variant, *zoom_band)
    return variant


def _major_link_width_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited z12+ motorway/trunk link widths into static QGIS zoom bands."""
    if not _has_major_link_width_expression(layer):
        return None

    layer_id = str(layer.get("id") or "")
    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    split_variants: list[dict[str, object]] = []
    for band in _MAJOR_LINK_WIDTH_BANDS:
        variant = _major_link_width_layer_variant(
            layer,
            layer_id,
            existing_minzoom,
            existing_maxzoom,
            band,
        )
        if variant is not None:
            split_variants.append(variant)
    if not split_variants:
        return None
    variants: list[dict[str, object]] = []
    passthrough_variant = _major_link_width_passthrough_layer_variant(
        layer,
        existing_minzoom,
        existing_maxzoom,
    )
    if passthrough_variant is not None:
        variants.append(passthrough_variant)
    variants.extend(split_variants)
    return variants


def _split_major_link_width_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _major_link_width_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _has_settlement_dot_icon_expression(layer: dict[str, object]) -> bool:
    base_layer_id = base_mapbox_style_layer_id_for_qfit(layer.get("id"))
    layout = layer.get("layout")
    return (
        base_layer_id in {_SETTLEMENT_MAJOR_LABEL_LAYER_ID, _SETTLEMENT_MINOR_LABEL_LAYER_ID}
        and layer.get("type") == "symbol"
        and isinstance(layout, dict)
        and layout.get("icon-image") == _SETTLEMENT_DOT_ICON_IMAGE_EXPRESSION
        and layout.get("text-anchor") == _SETTLEMENT_DOT_TEXT_ANCHOR_EXPRESSION
        and layout.get("text-radial-offset") == _SETTLEMENT_DOT_TEXT_RADIAL_OFFSET_EXPRESSION
    )


def _set_settlement_low_zoom_dot_label_layout(
    layer: dict[str, object],
    *,
    icon_image: str,
    text_radial_offset: float,
) -> None:
    layout = layer.get("layout")
    if not isinstance(layout, dict):
        return
    layout["icon-image"] = icon_image
    layout["text-anchor"] = ["get", "text_anchor"]
    layout["text-radial-offset"] = text_radial_offset
    if layout.get("text-justify") == _SETTLEMENT_DOT_TEXT_JUSTIFY_EXPRESSION:
        layout["text-justify"] = copy.deepcopy(_SETTLEMENT_DOT_TEXT_JUSTIFY_LOW_ZOOM)


def _set_settlement_high_zoom_text_layout(layer: dict[str, object]) -> None:
    layout = layer.get("layout")
    if not isinstance(layout, dict):
        return
    layout.pop("icon-image", None)
    layout["text-anchor"] = "center"
    layout["text-radial-offset"] = 0
    if layout.get("text-justify") == _SETTLEMENT_DOT_TEXT_JUSTIFY_EXPRESSION:
        layout["text-justify"] = "center"


def _settlement_major_low_zoom_text_justify_variants(layer: dict[str, object]) -> list[dict[str, object]]:
    layout = layer.get("layout")
    if not isinstance(layout, dict) or layout.get("text-justify") != _SETTLEMENT_DOT_TEXT_JUSTIFY_LOW_ZOOM:
        return [layer]
    layer_id = str(layer.get("id") or _SETTLEMENT_MAJOR_LABEL_LAYER_ID)
    variants: list[dict[str, object]] = []
    for suffix, filter_clause, text_justify in _SETTLEMENT_MAJOR_LOW_ZOOM_TEXT_JUSTIFY_VARIANTS:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{layer_id}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(variant.get("filter"), filter_clause)
        variant_layout = variant.get("layout")
        if isinstance(variant_layout, dict):
            variant_layout["text-justify"] = text_justify
        variants.append(variant)
    return variants


def _settlement_dot_icon_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split Mapbox's low-zoom settlement dot icons from z8+ centered labels."""
    if not _has_settlement_dot_icon_expression(layer):
        return None
    base_layer_id = base_mapbox_style_layer_id_for_qfit(layer.get("id"))
    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    layer_id = str(layer.get("id") or base_layer_id)
    variants: list[dict[str, object]] = []

    for zoom_suffix, band_minzoom, band_maxzoom in _SETTLEMENT_DOT_ICON_ZOOM_BANDS:
        if not _zoom_ranges_overlap(existing_minzoom, existing_maxzoom, band_minzoom, band_maxzoom):
            continue
        for suffix, icon_filter, icon_image, text_radial_offset in _SETTLEMENT_DOT_ICON_VARIANTS:
            icon_layer = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
            icon_layer["id"] = f"{layer_id}-{zoom_suffix}-{suffix}"
            icon_layer["filter"] = _with_additional_filter_clauses(layer.get("filter"), icon_filter)
            _set_settlement_low_zoom_dot_label_layout(
                icon_layer,
                icon_image=icon_image,
                text_radial_offset=text_radial_offset,
            )
            if base_layer_id == _SETTLEMENT_MAJOR_LABEL_LAYER_ID:
                variants.extend(_settlement_major_low_zoom_text_justify_variants(icon_layer))
            else:
                variants.append(icon_layer)

    if _zoom_ranges_overlap(existing_minzoom, existing_maxzoom, _SETTLEMENT_DOT_ICON_SPLIT_ZOOM, None):
        text_layer = _apply_zoom_band_bounds(layer, _SETTLEMENT_DOT_ICON_SPLIT_ZOOM, None)
        text_layer["id"] = f"{layer_id}-z8-plus"
        _set_settlement_high_zoom_text_layout(text_layer)
        variants.append(text_layer)

    return variants or None


def _split_settlement_dot_icon_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _settlement_dot_icon_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _has_country_label_layout_expression(layer: dict[str, object]) -> bool:
    layout = layer.get("layout")
    return (
        base_mapbox_style_layer_id_for_qfit(layer.get("id")) == _COUNTRY_LABEL_LAYER_ID
        and layer.get("type") == "symbol"
        and isinstance(layout, dict)
        and layout.get("text-justify") == _COUNTRY_LABEL_LAYOUT_TEXT_JUSTIFY_EXPRESSION
        and layout.get("text-radial-offset") == _COUNTRY_LABEL_LAYOUT_TEXT_RADIAL_OFFSET_EXPRESSION
    )


def _set_country_label_static_layout(
    layer: dict[str, object],
    *,
    text_justify: str | None,
    text_radial_offset: float | None,
) -> None:
    layout = layer.get("layout")
    if not isinstance(layout, dict):
        return
    if text_justify is not None:
        layout["text-justify"] = text_justify
    if text_radial_offset is not None:
        layout["text-radial-offset"] = text_radial_offset


def _country_label_low_zoom_text_justify_variants(
    layer: dict[str, object],
    *,
    layer_id: str,
    text_radial_offset: float | None,
) -> list[dict[str, object]]:
    variants: list[dict[str, object]] = []
    for suffix, filter_clause, text_justify in _COUNTRY_LABEL_LOW_ZOOM_TEXT_JUSTIFY_VARIANTS:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{layer_id}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(variant.get("filter"), filter_clause)
        _set_country_label_static_layout(
            variant,
            text_justify=text_justify,
            text_radial_offset=text_radial_offset,
        )
        variants.append(variant)
    return variants


def _country_label_layout_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited country-label zoom/layout expressions into QGIS-safe static variants."""
    if not _has_country_label_layout_expression(layer):
        return None
    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    layer_id = str(layer.get("id") or _COUNTRY_LABEL_LAYER_ID)
    variants: list[dict[str, object]] = []
    has_static_variant = False
    for suffix, band_minzoom, band_maxzoom, text_justify, text_radial_offset in _COUNTRY_LABEL_LAYOUT_ZOOM_BANDS:
        if not _zoom_ranges_overlap(existing_minzoom, existing_maxzoom, band_minzoom, band_maxzoom):
            continue
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["id"] = f"{layer_id}-{suffix}"
        if suffix == _COUNTRY_LABEL_BELOW_Z7_BAND_SUFFIX and text_justify is None:
            variants.extend(
                _country_label_low_zoom_text_justify_variants(
                    variant,
                    layer_id=str(variant["id"]),
                    text_radial_offset=text_radial_offset,
                )
            )
            has_static_variant = True
            continue
        if text_justify is not None or text_radial_offset is not None:
            _set_country_label_static_layout(
                variant,
                text_justify=text_justify,
                text_radial_offset=text_radial_offset,
            )
            has_static_variant = True
        variants.append(variant)
    return variants if has_static_variant else None


def _split_country_label_layout_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _country_label_layout_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _has_continent_label_text_opacity_expression(layer: dict[str, object]) -> bool:
    paint = layer.get("paint")
    return (
        base_mapbox_style_layer_id_for_qfit(layer.get("id")) == _CONTINENT_LABEL_LAYER_ID
        and layer.get("type") == "symbol"
        and isinstance(paint, dict)
        and paint.get("text-opacity") == _CONTINENT_LABEL_TEXT_OPACITY_EXPRESSION
    )


def _zoom_band_representative_zoom(minzoom: float | None, maxzoom: float | None) -> float:
    if minzoom is not None and maxzoom is not None:
        return (minzoom + maxzoom) / 2.0
    if minzoom is not None:
        return minzoom
    if maxzoom is not None:
        return max(0.0, maxzoom - _ZOOM_BOUND_EPSILON)
    return _REPRESENTATIVE_STYLE_ZOOM


def _continent_label_text_opacity_for_zoom_band(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
) -> float | None:
    effective_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        band_minzoom,
        band_maxzoom,
    )
    if effective_zoom_band is None:
        return None
    representative_zoom = _zoom_band_representative_zoom(*effective_zoom_band)
    opacity = _interpolate_filter_value_at_zoom(_CONTINENT_LABEL_TEXT_OPACITY_EXPRESSION, representative_zoom)
    return _clamp_opacity_value(opacity)


def _continent_label_text_opacity_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split continent-label opacity fade into static zoom bands for QGIS."""
    if not _has_continent_label_text_opacity_expression(layer):
        return None
    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    layer_id = str(layer.get("id") or _CONTINENT_LABEL_LAYER_ID)
    variants: list[dict[str, object]] = []
    for suffix, band_minzoom, band_maxzoom in _CONTINENT_LABEL_TEXT_OPACITY_ZOOM_BANDS:
        text_opacity = _continent_label_text_opacity_for_zoom_band(
            existing_minzoom,
            existing_maxzoom,
            band_minzoom,
            band_maxzoom,
        )
        if text_opacity is None:
            continue
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["id"] = f"{layer_id}-{suffix}"
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["text-opacity"] = text_opacity
        variants.append(variant)
    return variants or None


def _split_continent_label_text_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _continent_label_text_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _has_cliff_line_pattern(layer: dict[str, object]) -> bool:
    paint = layer.get("paint")
    return (
        base_mapbox_style_layer_id_for_qfit(layer.get("id")) == _CLIFF_LAYER_ID
        and layer.get("type") == "line"
        and isinstance(paint, dict)
        and paint.get("line-pattern") == _CLIFF_LINE_PATTERN
    )


def _set_cliff_line_pattern_fallback(layer: dict[str, object]) -> None:
    paint = layer.get("paint")
    if not isinstance(paint, dict):
        return
    paint.pop("line-pattern", None)
    paint.setdefault("line-color", _CLIFF_LINE_PATTERN_FALLBACK_COLOR)
    paint["line-dasharray"] = copy.deepcopy(_CLIFF_LINE_PATTERN_FALLBACK_DASHARRAY)
    paint["line-width"] = _CLIFF_LINE_PATTERN_FALLBACK_WIDTH


def _cliff_line_opacity_for_zoom_band(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
) -> float | None:
    effective_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        band_minzoom,
        band_maxzoom,
    )
    if effective_zoom_band is None:
        return None
    representative_zoom = _zoom_band_representative_zoom(*effective_zoom_band)
    opacity = _interpolate_filter_value_at_zoom(_CLIFF_LINE_OPACITY_EXPRESSION, representative_zoom)
    return _clamp_opacity_value(opacity)


def _cliff_line_pattern_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Replace Mapbox's cliff sprite pattern with a simple QGIS-safe dashed stroke."""
    if not _has_cliff_line_pattern(layer):
        return None

    paint = layer.get("paint")
    if not isinstance(paint, dict):
        return None
    if paint.get("line-opacity") != _CLIFF_LINE_OPACITY_EXPRESSION:
        fallback_layer = copy.deepcopy(layer)
        _set_cliff_line_pattern_fallback(fallback_layer)
        return [fallback_layer]

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    layer_id = str(layer.get("id") or _CLIFF_LAYER_ID)
    variants: list[dict[str, object]] = []
    for suffix, band_minzoom, band_maxzoom in _CLIFF_LINE_OPACITY_ZOOM_BANDS:
        line_opacity = _cliff_line_opacity_for_zoom_band(
            existing_minzoom,
            existing_maxzoom,
            band_minzoom,
            band_maxzoom,
        )
        if line_opacity is None:
            continue
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["id"] = f"{layer_id}-{suffix}"
        _set_cliff_line_pattern_fallback(variant)
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["line-opacity"] = line_opacity
        variants.append(variant)
    return variants or None


def _split_cliff_line_pattern_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _cliff_line_pattern_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _zoom_expression_opacity_layer_variants(
    layer: dict[str, object],
    *,
    layer_type: str,
    paint_property: str,
    expressions_by_layer_id: dict[str, list[object]],
    zoom_bands: tuple[tuple[str, float | None, float | None], ...],
) -> list[dict[str, object]] | None:
    layer_id = str(layer.get("id") or "")
    expected_expression = expressions_by_layer_id.get(layer_id)
    paint = layer.get("paint")
    if layer.get("type") != layer_type or expected_expression is None or not isinstance(paint, dict):
        return None
    if paint.get(paint_property) != expected_expression:
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants: list[dict[str, object]] = []
    for suffix, band_minzoom, band_maxzoom in zoom_bands:
        effective_zoom_band = _effective_zoom_band(
            existing_minzoom,
            existing_maxzoom,
            band_minzoom,
            band_maxzoom,
        )
        if effective_zoom_band is None:
            continue
        representative_zoom = _zoom_band_representative_zoom(*effective_zoom_band)
        fill_opacity = _clamp_opacity_value(_interpolate_filter_value_at_zoom(expected_expression, representative_zoom))
        if fill_opacity is None:
            continue
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["id"] = f"{layer_id}-{suffix}"
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint[paint_property] = fill_opacity
        variants.append(variant)
    return variants or None


def _building_fill_opacity_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited building fill opacity fades into static QGIS zoom bands."""
    return _zoom_expression_opacity_layer_variants(
        layer,
        layer_type="fill",
        paint_property="fill-opacity",
        expressions_by_layer_id=_BUILDING_FILL_OPACITY_EXPRESSIONS,
        zoom_bands=_BUILDING_FILL_OPACITY_ZOOM_BANDS,
    )


def _split_building_fill_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _building_fill_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _hillshade_fill_color_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split Mapbox hillshade shadows and highlights into static QGIS layers."""
    paint = layer.get("paint")
    if (
        str(layer.get("id") or "") != _HILLSHADE_LAYER_ID
        or layer.get("type") != "fill"
        or not isinstance(paint, dict)
        or paint.get("fill-color") != _HILLSHADE_FILL_COLOR_EXPRESSION
    ):
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants: list[dict[str, object]] = []

    split_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        None,
        _HILLSHADE_CLASS_SPLIT_MAX_ZOOM,
    )
    if split_zoom_band is not None:
        shadow_layer = copy.deepcopy(layer)
        shadow_layer["id"] = f"{_HILLSHADE_LAYER_ID}-shadow"
        _set_zoom_bounds(shadow_layer, *split_zoom_band)
        shadow_layer["filter"] = _with_additional_filter_clauses(
            layer.get("filter"),
            ["==", ["get", "class"], "shadow"],
        )
        shadow_paint = shadow_layer["paint"]
        assert isinstance(shadow_paint, dict)
        shadow_paint["fill-color"] = _HILLSHADE_SHADOW_FILL_COLOR
        variants.append(shadow_layer)

    highlight_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        _HILLSHADE_HIGHLIGHT_MIN_ZOOM,
        _HILLSHADE_CLASS_SPLIT_MAX_ZOOM,
    )
    if highlight_zoom_band is not None:
        highlight_layer = copy.deepcopy(layer)
        highlight_layer["id"] = f"{_HILLSHADE_LAYER_ID}-highlight"
        _set_zoom_bounds(highlight_layer, *highlight_zoom_band)
        highlight_layer["filter"] = _with_additional_filter_clauses(
            layer.get("filter"),
            ["!=", ["get", "class"], "shadow"],
        )
        highlight_paint = highlight_layer["paint"]
        assert isinstance(highlight_paint, dict)
        highlight_paint["fill-color"] = _HILLSHADE_HIGHLIGHT_FILL_COLOR
        variants.append(highlight_layer)

    high_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        _HILLSHADE_CLASS_SPLIT_MAX_ZOOM,
        None,
    )
    if high_zoom_band is not None:
        high_zoom_layer = copy.deepcopy(layer)
        high_zoom_layer["id"] = f"{_HILLSHADE_LAYER_ID}-z13-plus"
        _set_zoom_bounds(high_zoom_layer, *high_zoom_band)
        high_zoom_paint = high_zoom_layer["paint"]
        assert isinstance(high_zoom_paint, dict)
        high_zoom_paint["fill-color"] = _HILLSHADE_HIGHLIGHT_FILL_COLOR
        variants.append(high_zoom_layer)

    return variants or None


def _split_hillshade_fill_color_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _hillshade_fill_color_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _landcover_fill_opacity_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited landcover fill opacity fade-out into static QGIS zoom bands."""
    return _zoom_expression_opacity_layer_variants(
        layer,
        layer_type="fill",
        paint_property="fill-opacity",
        expressions_by_layer_id=_LANDCOVER_FILL_OPACITY_EXPRESSIONS,
        zoom_bands=_LANDCOVER_FILL_OPACITY_ZOOM_BANDS,
    )


def _split_landcover_fill_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _landcover_fill_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _landcover_class_fill_color_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split landcover opacity bands into QGIS-safe class colors."""
    layer_id = str(layer.get("id") or "")
    paint = layer.get("paint")
    if (
        layer_id not in _LANDCOVER_CLASS_FILL_COLOR_SPLIT_LAYER_IDS
        or base_mapbox_style_layer_id_for_qfit(layer_id) != _LANDCOVER_LAYER_ID
        or layer.get("type") != "fill"
        or not isinstance(paint, dict)
        or paint.get("fill-color") != _LANDCOVER_FILL_COLOR_EXPRESSION
    ):
        return None

    variants: list[dict[str, object]] = []
    for suffix, class_filter, fill_color in _LANDCOVER_CLASS_FILL_COLOR_VARIANTS:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{layer_id}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), class_filter)
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["fill-color"] = fill_color
        variants.append(variant)
    return variants or None


def _split_landcover_class_fill_color_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _landcover_class_fill_color_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _landuse_fill_opacity_at_zoom(zoom: float, *, residential: bool) -> float | None:
    lower_opacity = 0.8 if residential else 0.2
    upper_opacity = 0.0 if residential else 1.0
    if zoom <= 8.0:
        return lower_opacity
    if zoom >= 10.0:
        return upper_opacity
    factor = _interpolate_filter_factor(["linear"], zoom, 8.0, 10.0)
    if factor is None:
        return None
    return _clamp_opacity_value(lower_opacity + ((upper_opacity - lower_opacity) * factor))


def _landuse_fill_opacity_for_zoom_band(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
    *,
    residential: bool,
) -> float | None:
    effective_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        band_minzoom,
        band_maxzoom,
    )
    if effective_zoom_band is None:
        return None
    representative_zoom = _zoom_band_representative_zoom(*effective_zoom_band)
    return _landuse_fill_opacity_at_zoom(representative_zoom, residential=residential)


def _landuse_fill_opacity_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited landuse opacity class fade into static QGIS zoom/class bands."""
    layer_id = str(layer.get("id") or "")
    paint = layer.get("paint")
    if layer_id != _LANDUSE_LAYER_ID or layer.get("type") != "fill" or not isinstance(paint, dict):
        return None
    if paint.get("fill-opacity") != _LANDUSE_FILL_OPACITY_EXPRESSION:
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants: list[dict[str, object]] = []
    for suffix, class_filter, band_minzoom, band_maxzoom, residential in _LANDUSE_FILL_OPACITY_VARIANTS:
        fill_opacity = _landuse_fill_opacity_for_zoom_band(
            existing_minzoom,
            existing_maxzoom,
            band_minzoom,
            band_maxzoom,
            residential=residential,
        )
        if fill_opacity is None:
            continue
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["id"] = f"{layer_id}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), class_filter)
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["fill-opacity"] = fill_opacity
        variants.append(variant)
    return variants or None


def _split_landuse_fill_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _landuse_fill_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _landuse_class_fill_color_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split visible non-residential landuse bands into QGIS-safe class colors."""
    layer_id = str(layer.get("id") or "")
    paint = layer.get("paint")
    if (
        layer_id not in _LANDUSE_CLASS_FILL_COLOR_SPLIT_LAYER_IDS
        or base_mapbox_style_layer_id_for_qfit(layer_id) != _LANDUSE_LAYER_ID
        or layer.get("type") != "fill"
        or not isinstance(paint, dict)
        or paint.get("fill-color") != _LANDUSE_FILL_COLOR_EXPRESSION
    ):
        return None

    variants: list[dict[str, object]] = []
    for suffix, class_filter, fill_color in _LANDUSE_CLASS_FILL_COLOR_VARIANTS:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{layer_id}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), class_filter)
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["fill-color"] = fill_color
        fill_opacity = _LANDUSE_CLASS_FILL_OPACITY_OVERRIDES.get((layer_id, suffix))
        if fill_opacity is not None:
            variant_paint["fill-opacity"] = fill_opacity
        variants.append(variant)
    return variants or None


def _split_landuse_class_fill_color_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _landuse_class_fill_color_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _national_park_fill_opacity_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited national-park fill opacity fade into static QGIS zoom bands."""
    return _zoom_expression_opacity_layer_variants(
        layer,
        layer_type="fill",
        paint_property="fill-opacity",
        expressions_by_layer_id=_NATIONAL_PARK_FILL_OPACITY_EXPRESSIONS,
        zoom_bands=_NATIONAL_PARK_FILL_OPACITY_ZOOM_BANDS,
    )


def _split_national_park_fill_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _national_park_fill_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _wetland_fill_opacity_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited wetland fill opacity fade into static QGIS zoom bands."""
    return _zoom_expression_opacity_layer_variants(
        layer,
        layer_type="fill",
        paint_property="fill-opacity",
        expressions_by_layer_id=_WETLAND_FILL_OPACITY_EXPRESSIONS,
        zoom_bands=_WETLAND_FILL_OPACITY_ZOOM_BANDS,
    )


def _split_wetland_fill_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _wetland_fill_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _road_pedestrian_polygon_pattern_fill_opacity_layer_variants(
    layer: dict[str, object],
) -> list[dict[str, object]] | None:
    """Split audited pedestrian polygon pattern opacity fade into static QGIS zoom bands."""
    return _zoom_expression_opacity_layer_variants(
        layer,
        layer_type="fill",
        paint_property="fill-opacity",
        expressions_by_layer_id=_ROAD_PEDESTRIAN_POLYGON_PATTERN_FILL_OPACITY_EXPRESSIONS,
        zoom_bands=_ROAD_PEDESTRIAN_POLYGON_PATTERN_FILL_OPACITY_ZOOM_BANDS,
    )


def _split_road_pedestrian_polygon_pattern_fill_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _road_pedestrian_polygon_pattern_fill_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _rail_track_line_opacity_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited rail track opacity fade into static QGIS zoom bands."""
    return _zoom_expression_opacity_layer_variants(
        layer,
        layer_type="line",
        paint_property="line-opacity",
        expressions_by_layer_id=_RAIL_TRACK_LINE_OPACITY_EXPRESSIONS,
        zoom_bands=_RAIL_TRACK_LINE_OPACITY_ZOOM_BANDS,
    )


def _split_rail_track_line_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _rail_track_line_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _gate_fence_hedge_line_opacity_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited gate/fence/hedge opacity match into static QGIS class variants."""
    layer_id = str(layer.get("id") or "")
    paint = layer.get("paint")
    if layer_id != _GATE_FENCE_HEDGE_LAYER_ID or layer.get("type") != "line" or not isinstance(paint, dict):
        return None
    if paint.get("line-opacity") != _GATE_FENCE_HEDGE_LINE_OPACITY_EXPRESSION:
        return None

    variants: list[dict[str, object]] = []
    for suffix, class_filter, line_opacity in _GATE_FENCE_HEDGE_LINE_OPACITY_VARIANTS:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{layer_id}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), class_filter)
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["line-opacity"] = line_opacity
        variants.append(variant)
    return variants or None


def _split_gate_fence_hedge_line_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _gate_fence_hedge_line_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _literal_translate_vector(value: object) -> list[float] | None:
    vector = value
    if isinstance(value, list) and len(value) == 2 and value[0] == "literal":
        vector = value[1]
    if not isinstance(vector, list) or len(vector) != 2:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in vector):
        return None
    return [float(vector[0]), float(vector[1])]


def _water_shadow_translate_at_zoom(zoom: float) -> list[float] | None:
    expr = _WATER_SHADOW_TRANSLATE_EXPRESSION
    if len(expr) < 7:
        return None
    lower_stop = expr[3]
    lower_translate = _literal_translate_vector(expr[4])
    upper_stop = expr[5]
    upper_translate = _literal_translate_vector(expr[6])
    if not isinstance(lower_stop, (int, float)) or isinstance(lower_stop, bool):
        return None
    if not isinstance(upper_stop, (int, float)) or isinstance(upper_stop, bool):
        return None
    if lower_translate is None or upper_translate is None:
        return None
    lower_zoom = float(lower_stop)
    upper_zoom = float(upper_stop)
    if zoom <= lower_zoom:
        return lower_translate
    if zoom >= upper_zoom:
        return upper_translate
    factor = _interpolate_filter_factor(expr[1], zoom, lower_zoom, upper_zoom)
    if factor is None:
        return None
    return [
        lower_value + ((upper_value - lower_value) * factor)
        for lower_value, upper_value in zip(lower_translate, upper_translate)
    ]


def _water_shadow_translate_for_zoom_band(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
) -> list[float] | None:
    effective_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        band_minzoom,
        band_maxzoom,
    )
    if effective_zoom_band is None:
        return None
    representative_zoom = _zoom_band_representative_zoom(*effective_zoom_band)
    return _water_shadow_translate_at_zoom(representative_zoom)


def _water_shadow_translate_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited water shadow translate fade into static QGIS zoom bands."""
    layer_id = str(layer.get("id") or "")
    expected = _WATER_SHADOW_TRANSLATE_LAYERS.get(layer_id)
    paint = layer.get("paint")
    if expected is None or not isinstance(paint, dict):
        return None
    layer_type, paint_property = expected
    if layer.get("type") != layer_type or paint.get(paint_property) != _WATER_SHADOW_TRANSLATE_EXPRESSION:
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants: list[dict[str, object]] = []
    for suffix, band_minzoom, band_maxzoom in _WATER_SHADOW_TRANSLATE_ZOOM_BANDS:
        translate = _water_shadow_translate_for_zoom_band(
            existing_minzoom,
            existing_maxzoom,
            band_minzoom,
            band_maxzoom,
        )
        if translate is None:
            continue
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["id"] = f"{layer_id}-{suffix}"
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint[paint_property] = translate
        variants.append(variant)
    return variants or None


def _split_water_shadow_translate_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _water_shadow_translate_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _aeroway_line_width_px_at_zoom(target_zoom: float, lower_width: float, upper_width: float) -> float | None:
    if target_zoom <= 9.0:
        return lower_width
    if target_zoom >= 18.0:
        return upper_width
    factor = _interpolate_filter_factor(["exponential", 1.5], target_zoom, 9.0, 18.0)
    if factor is None:
        return None
    return lower_width + ((upper_width - lower_width) * factor)


def _aeroway_line_width_mm_for_zoom_band(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
    lower_width: float,
    upper_width: float,
) -> tuple[float, float | None, float | None] | None:
    effective_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        band_minzoom,
        band_maxzoom,
    )
    if effective_zoom_band is None:
        return None
    representative_zoom = _zoom_band_representative_zoom(*effective_zoom_band)
    width_px = _aeroway_line_width_px_at_zoom(representative_zoom, lower_width, upper_width)
    if width_px is None:
        return None
    return min(max(width_px * _MAPBOX_PIXEL_TO_MM, 0.0), _MAX_LINE_WIDTH_MM), *effective_zoom_band


def _aeroway_line_width_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split Mapbox aeroway runway widths into static QGIS zoom bands."""
    layer_id = str(layer.get("id") or "")
    paint = layer.get("paint")
    if (
        layer_id != _AEROWAY_LINE_LAYER_ID
        or layer.get("type") != "line"
        or not isinstance(paint, dict)
        or paint.get("line-width") != _AEROWAY_LINE_WIDTH_EXPRESSION
    ):
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants: list[dict[str, object]] = []
    for band_suffix, band_minzoom, band_maxzoom in _AEROWAY_LINE_WIDTH_ZOOM_BANDS:
        for type_suffix, type_filter, lower_width, upper_width in _AEROWAY_LINE_WIDTH_TYPE_VARIANTS:
            width = _aeroway_line_width_mm_for_zoom_band(
                existing_minzoom,
                existing_maxzoom,
                band_minzoom,
                band_maxzoom,
                lower_width,
                upper_width,
            )
            if width is None:
                continue
            line_width, effective_minzoom, effective_maxzoom = width
            variant = copy.deepcopy(layer)
            variant["id"] = f"{layer_id}-{type_suffix}-{band_suffix}"
            _set_zoom_bounds(variant, effective_minzoom, effective_maxzoom)
            variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), type_filter)
            variant_paint = variant["paint"]
            assert isinstance(variant_paint, dict)
            variant_paint["line-width"] = line_width
            variants.append(variant)
    return variants or None


def _split_aeroway_line_width_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _aeroway_line_width_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _waterway_line_width_px_at_zoom(target_zoom: float, lower_width: float, upper_width: float) -> float | None:
    if target_zoom <= 9.0:
        return lower_width
    if target_zoom >= 20.0:
        return upper_width
    factor = _interpolate_filter_factor(["exponential", 1.3], target_zoom, 9.0, 20.0)
    if factor is None:
        return None
    return lower_width + ((upper_width - lower_width) * factor)


def _waterway_line_width_mm_for_zoom_band(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
    lower_width: float,
    upper_width: float,
) -> tuple[float, float | None, float | None] | None:
    effective_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        band_minzoom,
        band_maxzoom,
    )
    if effective_zoom_band is None:
        return None
    representative_zoom = _zoom_band_representative_zoom(*effective_zoom_band)
    width_px = _waterway_line_width_px_at_zoom(representative_zoom, lower_width, upper_width)
    if width_px is None:
        return None
    return min(max(width_px * _MAPBOX_PIXEL_TO_MM, 0.0), _MAX_LINE_WIDTH_MM), *effective_zoom_band


def _waterway_line_width_layer_id(layer_id: str, class_suffix: str, band_suffix: str) -> str:
    if layer_id.startswith(f"{_WATERWAY_SHADOW_LAYER_ID}-"):
        return f"{layer_id}-{class_suffix}"
    return f"{layer_id}-{class_suffix}-{band_suffix}"


def _waterway_line_width_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split Mapbox waterway width classes into static QGIS zoom bands."""
    layer_id = str(layer.get("id") or "")
    base_layer_id = _waterway_line_width_base_layer_id(layer_id)
    paint = layer.get("paint")
    if (
        base_layer_id not in {_WATERWAY_LAYER_ID, _WATERWAY_SHADOW_LAYER_ID}
        or layer.get("type") != "line"
        or not isinstance(paint, dict)
        or paint.get("line-width") != _WATERWAY_LINE_WIDTH_EXPRESSION
    ):
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants: list[dict[str, object]] = []
    for band_suffix, band_minzoom, band_maxzoom in _WATERWAY_LINE_WIDTH_ZOOM_BANDS:
        for class_suffix, class_filter, lower_width, upper_width in _WATERWAY_LINE_WIDTH_CLASS_VARIANTS:
            width = _waterway_line_width_mm_for_zoom_band(
                existing_minzoom,
                existing_maxzoom,
                band_minzoom,
                band_maxzoom,
                lower_width,
                upper_width,
            )
            if width is None:
                continue
            line_width, effective_minzoom, effective_maxzoom = width
            variant = copy.deepcopy(layer)
            variant["id"] = _waterway_line_width_layer_id(layer_id, class_suffix, band_suffix)
            _set_zoom_bounds(variant, effective_minzoom, effective_maxzoom)
            variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), class_filter)
            variant_paint = variant["paint"]
            assert isinstance(variant_paint, dict)
            variant_paint["line-width"] = line_width
            variants.append(variant)
    return variants or None


def _split_waterway_line_width_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _waterway_line_width_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _circle_size_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def _turning_feature_circle_size_for_zoom_band(
    expression: list[object],
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
) -> float | None:
    effective_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        band_minzoom,
        band_maxzoom,
    )
    if effective_zoom_band is None:
        return None
    representative_zoom = _zoom_band_representative_zoom(*effective_zoom_band)
    return _circle_size_value(_interpolate_filter_value_at_zoom(expression, representative_zoom))


def _turning_feature_circle_layer_match(layer: dict[str, object]) -> tuple[str, bool] | None:
    layer_id = str(layer.get("id") or "")
    if layer_id not in {_TURNING_FEATURE_LAYER_ID, _TURNING_FEATURE_OUTLINE_LAYER_ID} or layer.get("type") != "circle":
        return None
    paint = layer.get("paint")
    if not isinstance(paint, dict) or paint.get("circle-radius") != _TURNING_FEATURE_CIRCLE_RADIUS_EXPRESSION:
        return None
    has_stroke_width = layer_id == _TURNING_FEATURE_OUTLINE_LAYER_ID
    if has_stroke_width and paint.get("circle-stroke-width") != _TURNING_FEATURE_CIRCLE_STROKE_WIDTH_EXPRESSION:
        return None
    return layer_id, has_stroke_width


def _turning_feature_circle_stroke_width_for_zoom_band(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
) -> float | None:
    return _turning_feature_circle_size_for_zoom_band(
        _TURNING_FEATURE_CIRCLE_STROKE_WIDTH_EXPRESSION,
        existing_minzoom,
        existing_maxzoom,
        band_minzoom,
        band_maxzoom,
    )


def _turning_feature_circle_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited turning circle radii/strokes into static QGIS zoom bands."""
    layer_match = _turning_feature_circle_layer_match(layer)
    if layer_match is None:
        return None
    layer_id, has_stroke_width = layer_match

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants: list[dict[str, object]] = []
    for suffix, band_minzoom, band_maxzoom in _TURNING_FEATURE_CIRCLE_ZOOM_BANDS:
        circle_radius = _turning_feature_circle_size_for_zoom_band(
            _TURNING_FEATURE_CIRCLE_RADIUS_EXPRESSION,
            existing_minzoom,
            existing_maxzoom,
            band_minzoom,
            band_maxzoom,
        )
        if circle_radius is None:
            continue
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["id"] = f"{layer_id}-{suffix}"
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["circle-radius"] = circle_radius
        if has_stroke_width:
            circle_stroke_width = _turning_feature_circle_stroke_width_for_zoom_band(
                existing_minzoom,
                existing_maxzoom,
                band_minzoom,
                band_maxzoom,
            )
            if circle_stroke_width is None:
                return None
            variant_paint["circle-stroke-width"] = circle_stroke_width
        variants.append(variant)
    return variants or None


def _split_turning_feature_circle_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _turning_feature_circle_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _waterway_label_symbol_spacing_at_zoom(zoom: float) -> float | None:
    expr = _WATERWAY_LABEL_SYMBOL_SPACING_EXPRESSION
    if len(expr) < 7 or expr[2] != ["zoom"]:
        return None
    lower_stop = _numeric_expression_value(expr[3])
    lower_spacing = _numeric_expression_value(expr[4])
    upper_stop = _numeric_expression_value(expr[5])
    upper_spacing = _numeric_expression_value(expr[6])
    if (
        lower_stop is None
        or lower_spacing is None
        or upper_stop is None
        or upper_spacing is None
    ):
        return None
    if zoom <= lower_stop:
        return lower_spacing
    if zoom >= upper_stop:
        return upper_spacing
    interpolation_type = ["linear"] if expr[1] == ["linear", 1] else expr[1]
    factor = _interpolate_filter_factor(interpolation_type, zoom, lower_stop, upper_stop)
    if factor is None:
        return None
    return lower_spacing + ((upper_spacing - lower_spacing) * factor)


def _waterway_label_symbol_spacing_for_zoom_band(
    existing_minzoom: float | None,
    existing_maxzoom: float | None,
    band_minzoom: float | None,
    band_maxzoom: float | None,
) -> float | None:
    effective_zoom_band = _effective_zoom_band(
        existing_minzoom,
        existing_maxzoom,
        band_minzoom,
        band_maxzoom,
    )
    if effective_zoom_band is None:
        return None
    representative_zoom = _zoom_band_representative_zoom(*effective_zoom_band)
    return _waterway_label_symbol_spacing_at_zoom(representative_zoom)


def _waterway_label_symbol_spacing_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited waterway label symbol spacing into static QGIS zoom bands."""
    if str(layer.get("id") or "") != _WATERWAY_LABEL_LAYER_ID or layer.get("type") != "symbol":
        return None
    layout = layer.get("layout")
    if not isinstance(layout, dict) or layout.get("symbol-spacing") != _WATERWAY_LABEL_SYMBOL_SPACING_EXPRESSION:
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants: list[dict[str, object]] = []
    for suffix, band_minzoom, band_maxzoom in _WATERWAY_LABEL_SYMBOL_SPACING_ZOOM_BANDS:
        symbol_spacing = _waterway_label_symbol_spacing_for_zoom_band(
            existing_minzoom,
            existing_maxzoom,
            band_minzoom,
            band_maxzoom,
        )
        if symbol_spacing is None:
            continue
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["id"] = f"{_WATERWAY_LABEL_LAYER_ID}-{suffix}"
        variant_layout = variant["layout"]
        assert isinstance(variant_layout, dict)
        variant_layout["symbol-spacing"] = symbol_spacing
        variants.append(variant)
    return variants or None


def _split_waterway_label_symbol_spacing_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _waterway_label_symbol_spacing_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _water_line_label_typography_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited water line label class spacing into static QGIS layers."""
    if str(layer.get("id") or "") != _WATER_LINE_LABEL_LAYER_ID or layer.get("type") != "symbol":
        return None
    layout = layer.get("layout")
    if not isinstance(layout, dict):
        return None
    if layout.get("text-letter-spacing") != _WATER_LINE_LABEL_TEXT_LETTER_SPACING_EXPRESSION:
        return None

    variants: list[dict[str, object]] = []
    for suffix, class_filter, text_letter_spacing in _WATER_LINE_LABEL_TYPOGRAPHY_VARIANTS:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{_WATER_LINE_LABEL_LAYER_ID}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), class_filter)
        variant_layout = variant["layout"]
        assert isinstance(variant_layout, dict)
        variant_layout["text-letter-spacing"] = text_letter_spacing
        variants.append(variant)
    return variants


def _water_point_label_typography_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited water point label class typography into static QGIS layers."""
    if str(layer.get("id") or "") != _WATER_POINT_LABEL_LAYER_ID or layer.get("type") != "symbol":
        return None
    layout = layer.get("layout")
    if not isinstance(layout, dict):
        return None
    if (
        layout.get("text-letter-spacing") != _WATER_POINT_LABEL_TEXT_LETTER_SPACING_EXPRESSION
        or layout.get("text-max-width") != _WATER_POINT_LABEL_TEXT_MAX_WIDTH_EXPRESSION
    ):
        return None

    variants: list[dict[str, object]] = []
    for suffix, class_filter, text_letter_spacing, text_max_width in _WATER_POINT_LABEL_TYPOGRAPHY_VARIANTS:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{_WATER_POINT_LABEL_LAYER_ID}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), class_filter)
        variant_layout = variant["layout"]
        assert isinstance(variant_layout, dict)
        variant_layout["text-letter-spacing"] = text_letter_spacing
        variant_layout["text-max-width"] = text_max_width
        variants.append(variant)
    return variants


def _water_label_typography_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    variants = _water_line_label_typography_layer_variants(layer)
    if variants is not None:
        return variants
    return _water_point_label_typography_layer_variants(layer)


def _split_water_label_typography_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _water_label_typography_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _contour_line_opacity_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited contour index opacity expressions into static QGIS zoom bands."""
    layer_id = str(layer.get("id") or "")
    paint = layer.get("paint")
    if layer_id != _CONTOUR_LINE_LAYER_ID or layer.get("type") != "line" or not isinstance(paint, dict):
        return None
    if paint.get("line-opacity") != _CONTOUR_LINE_OPACITY_EXPRESSION:
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    variants: list[dict[str, object]] = []
    for suffix, index_filter, band_minzoom, band_maxzoom, line_opacity in _CONTOUR_LINE_OPACITY_VARIANTS:
        if _effective_zoom_band(existing_minzoom, existing_maxzoom, band_minzoom, band_maxzoom) is None:
            continue
        variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
        variant["id"] = f"{layer_id}-{suffix}"
        variant["filter"] = _with_additional_filter_clauses(layer.get("filter"), index_filter)
        variant_paint = variant["paint"]
        assert isinstance(variant_paint, dict)
        variant_paint["line-opacity"] = line_opacity
        variants.append(variant)
    return variants or None


def _split_contour_line_opacity_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _contour_line_opacity_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _has_label_icon_visibility_expression(layer: dict[str, object]) -> bool:
    base_layer_id = base_mapbox_style_layer_id_for_qfit(layer.get("id"))
    layout = layer.get("layout")
    paint = layer.get("paint")
    return (
        base_layer_id in {_NATURAL_POINT_LABEL_LAYER_ID, _POI_LABEL_LAYER_ID}
        and layer.get("type") == "symbol"
        and isinstance(layout, dict)
        and isinstance(paint, dict)
        and paint.get("icon-opacity") == _LABEL_ICON_OPACITY_EXPRESSION
        and layout.get("text-anchor") == _LABEL_ICON_TEXT_ANCHOR_EXPRESSION
        and layout.get("text-offset") == _LABEL_ICON_TEXT_OFFSET_EXPRESSION
    )


def _label_icon_visibility_text_layer(
    layer: dict[str, object],
    *,
    layer_id: str,
    suffix: str,
    band_minzoom: float | None,
    band_maxzoom: float | None,
    sizerank_threshold: float,
) -> dict[str, object]:
    variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
    variant["id"] = f"{layer_id}-{suffix}-text"
    variant["filter"] = _with_additional_filter_clauses(
        layer.get("filter"),
        ["<", ["get", "sizerank"], sizerank_threshold],
    )
    layout = variant["layout"]
    if isinstance(layout, dict):
        for icon_key in [key for key in layout if str(key).startswith("icon-")]:
            layout.pop(icon_key, None)
        layout["text-anchor"] = "center"
        layout["text-offset"] = [0, 0]
    paint = variant["paint"]
    if isinstance(paint, dict):
        paint.pop("icon-opacity", None)
    return variant


def _label_icon_visibility_icon_layer(
    layer: dict[str, object],
    *,
    layer_id: str,
    suffix: str,
    band_minzoom: float | None,
    band_maxzoom: float | None,
    sizerank_threshold: float,
) -> dict[str, object]:
    variant = _apply_zoom_band_bounds(layer, band_minzoom, band_maxzoom)
    variant["id"] = f"{layer_id}-{suffix}-icon"
    variant["filter"] = _with_additional_filter_clauses(
        layer.get("filter"),
        [">=", ["get", "sizerank"], sizerank_threshold],
    )
    layout = variant["layout"]
    if isinstance(layout, dict):
        layout["text-anchor"] = "top"
        layout["text-offset"] = [0, 0.8]
    paint = variant["paint"]
    if isinstance(paint, dict):
        paint.pop("icon-opacity", None)
    return variant


def _label_icon_visibility_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    """Split audited POI/natural icon visibility expressions into QGIS-safe layers.

    Mapbox couples ``icon-opacity``, ``text-anchor``, and ``text-offset`` so
    labels are centered when the icon is hidden and offset above visible icons.
    QGIS skips the data-driven opacity expression, so preserve the same
    sizerank/zoom behavior with static text-only and icon variants.
    """
    if not _has_label_icon_visibility_expression(layer):
        return None

    existing_minzoom = _numeric_zoom_bound(layer.get("minzoom"))
    existing_maxzoom = _numeric_zoom_bound(layer.get("maxzoom"))
    layer_id = str(layer.get("id") or base_mapbox_style_layer_id_for_qfit(layer.get("id")))
    variants: list[dict[str, object]] = []
    for suffix, band_minzoom, band_maxzoom, sizerank_threshold in _LABEL_ICON_VISIBILITY_ZOOM_BANDS:
        if not _zoom_ranges_overlap(existing_minzoom, existing_maxzoom, band_minzoom, band_maxzoom):
            continue
        variants.append(
            _label_icon_visibility_text_layer(
                layer,
                layer_id=layer_id,
                suffix=suffix,
                band_minzoom=band_minzoom,
                band_maxzoom=band_maxzoom,
                sizerank_threshold=sizerank_threshold,
            )
        )
        variants.append(
            _label_icon_visibility_icon_layer(
                layer,
                layer_id=layer_id,
                suffix=suffix,
                band_minzoom=band_minzoom,
                band_maxzoom=band_maxzoom,
                sizerank_threshold=sizerank_threshold,
            )
        )
    return variants or None


def _split_label_icon_visibility_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _label_icon_visibility_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _gate_label_icon_image_layer_variants(layer: dict[str, object]) -> list[dict[str, object]] | None:
    layout = layer.get("layout")
    if (
        base_mapbox_style_layer_id_for_qfit(layer.get("id")) != _GATE_LABEL_LAYER_ID
        or layer.get("type") != "symbol"
        or not isinstance(layout, dict)
        or layout.get("icon-image") != _GATE_LABEL_ICON_IMAGE_EXPRESSION
    ):
        return None

    layer_id = str(layer.get("id") or _GATE_LABEL_LAYER_ID)
    variants: list[dict[str, object]] = []
    for suffix, filter_clause, icon_image in _GATE_LABEL_ICON_IMAGE_VARIANTS:
        variant = copy.deepcopy(layer)
        variant["id"] = f"{layer_id}-{suffix}"
        if variant.get("filter") is False:
            variant["filter"] = False
        else:
            variant["filter"] = _with_additional_filter_clauses(variant.get("filter"), filter_clause)
        variant_layout = variant.get("layout")
        if isinstance(variant_layout, dict):
            variant_layout["icon-image"] = icon_image
        variants.append(variant)
    return variants


def _split_gate_label_icon_image_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _gate_label_icon_image_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _expand_road_number_shield_layers_for_qgis(layers: object) -> object:
    if not isinstance(layers, list):
        return layers
    expanded_layers: list[object] = []
    for layer in layers:
        if not isinstance(layer, dict):
            expanded_layers.append(layer)
            continue
        variants = _road_number_shield_layer_variants(layer)
        expanded_layers.extend(variants if variants is not None else [layer])
    return expanded_layers


def _extract_fallback_color(expr: object) -> str | None:
    """Recursively extract a sensible literal color from a Mapbox expression.

    Mapbox uses expressions for dynamic colors. QGIS'
    ``QgsMapBoxGlStyleConverter`` can fall back to black when it cannot resolve
    them, so we collapse unsupported expressions to representative colors.

    For zoom expressions, choose the color closest to a mid-zoom instead of the
    last/max-zoom stop. That produces a closer default for the city/regional
    scales qfit usually displays while still avoiding converter black fallbacks.
    """
    if _is_literal_color(expr):
        return str(expr)
    if not isinstance(expr, list) or not expr:
        return None

    op = expr[0]
    if op == "interpolate":
        representative = _nearest_zoom_stop_value(expr)
        color = _extract_fallback_color(representative)
        if color is not None:
            return color
    if op == "step":
        representative = _step_zoom_value(expr)
        color = _extract_fallback_color(representative)
        if color is not None:
            return color

    # Walk backwards through list elements looking for a data/default fallback.
    fallback: str | None = None
    for item in reversed(expr):
        color = _extract_fallback_color(item)
        if color is not None:
            fallback = color
            break
    return fallback


def _extract_midrange_size(expr: object) -> float | None:
    """Extract a reasonable representative size from a Mapbox size expression.

    For zoom-interpolated sizes we pick the value nearest a mid-zoom (z12),
    falling back to a modest literal. This avoids collapsing all text and lines
    to either the low-zoom or high-zoom extreme.
    """
    if isinstance(expr, (int, float)):
        return float(expr)
    if not isinstance(expr, list) or not expr:
        return None
    op = expr[0]
    if op == "interpolate":
        representative = _nearest_zoom_stop_value(expr)
        value = _extract_midrange_size(representative)
        if value is not None:
            return value
        representative = _nearest_interpolate_output_value(expr)
        value = _extract_midrange_size(representative)
        if value is not None:
            return value
    if op == "step":
        representative = _step_zoom_value(expr)
        value = _extract_midrange_size(representative)
        if value is not None:
            return value
        default_val = _extract_midrange_size(expr[2]) if len(expr) >= 3 else None
        if default_val is not None:
            return default_val
    # Recurse for nested expressions, take first reasonable scalar
    for item in expr[1:]:
        val = _extract_midrange_size(item)
        if val is not None:
            return val
    return None


def _extract_zoom_scalar_size(expr: object, *, minzoom: object = None, maxzoom: object = None) -> float | None:
    """Resolve zoom-only size expressions to a representative scalar for QGIS.

    Keep data-driven sizes intact; QGIS may be able to apply them in contexts
    where a static snapshot would lose feature-specific emphasis.
    """
    target_zoom = _representative_zoom_in_layer_range(minzoom, maxzoom)
    if target_zoom is None:
        return None
    return _extract_zoom_scalar_size_at_zoom(expr, target_zoom)


def _clamp_opacity_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(float(value), 1.0))


def _representative_opacity_interpolate_output(expr: list[object]) -> object | None:
    if len(expr) < 5:
        return None
    if expr[2] != ["zoom"]:
        return None

    numeric_stops: list[tuple[float, float]] = []
    for index in range(3, len(expr) - 1, 2):
        stop = expr[index]
        output = expr[index + 1]
        if (
            isinstance(stop, bool)
            or not isinstance(stop, (int, float))
            or isinstance(output, bool)
            or not isinstance(output, (int, float))
        ):
            return None
        numeric_stops.append((float(stop), float(output)))

    target_zoom = _REPRESENTATIVE_STYLE_ZOOM
    previous_zoom, previous_output = numeric_stops[0]
    if target_zoom <= previous_zoom:
        return previous_output
    for stop_zoom, stop_output in numeric_stops[1:]:
        if target_zoom <= stop_zoom:
            progress = (target_zoom - previous_zoom) / (stop_zoom - previous_zoom)
            return previous_output + ((stop_output - previous_output) * progress)
        previous_zoom, previous_output = stop_zoom, stop_output
    return previous_output


def _representative_opacity_step_output(expr: list[object]) -> object | None:
    if len(expr) < 3:
        return None
    if expr[1] == ["zoom"]:
        return _step_zoom_value(expr)
    outputs = [expr[2], *[expr[index] for index in range(4, len(expr), 2)]]
    return _extract_opacity_from_reachable_outputs(outputs)


def _zoom_step_opacity_outputs_from_minzoom(expr: list[object], minzoom: object) -> list[object] | None:
    if len(expr) < 3 or expr[1] != ["zoom"]:
        return None
    if isinstance(minzoom, bool) or not isinstance(minzoom, (int, float)):
        return None

    visible_minzoom = float(minzoom)
    current_output = expr[2]
    outputs: list[object] = []
    for index in range(3, len(expr) - 1, 2):
        stop = expr[index]
        if isinstance(stop, bool) or not isinstance(stop, (int, float)):
            return None
        if visible_minzoom < float(stop):
            outputs.append(current_output)
        current_output = expr[index + 1]
    outputs.append(current_output)
    return outputs


def _extract_visible_range_zoom_step_opacity(expr: object, minzoom: object) -> float | None:
    if not isinstance(expr, list):
        return None
    outputs = _zoom_step_opacity_outputs_from_minzoom(expr, minzoom)
    if outputs is None:
        return None
    return _extract_opacity_from_reachable_outputs(outputs)


def _zoom_step_full_opacity_minzoom(expr: object, minzoom: object, maxzoom: object) -> float | None:
    if not isinstance(expr, list) or len(expr) < 5 or expr[0] != "step" or expr[1] != ["zoom"]:
        return None
    existing_minzoom = _numeric_zoom_bound(minzoom)
    existing_maxzoom = _numeric_zoom_bound(maxzoom)
    if minzoom is not None and existing_minzoom is None:
        return None
    if maxzoom is not None and existing_maxzoom is None:
        return None

    hidden_until_zoom: float | None = None
    current_output = expr[2]
    for index in range(3, len(expr) - 1, 2):
        stop = expr[index]
        if isinstance(stop, bool) or not isinstance(stop, (int, float)):
            return None
        current_opacity = _extract_representative_opacity(current_output)
        next_opacity = _extract_representative_opacity(expr[index + 1])
        if current_opacity is None or next_opacity is None:
            return None
        if hidden_until_zoom is None:
            if current_opacity > _FULL_OPACITY_EPSILON:
                return None
            if next_opacity > _FULL_OPACITY - _FULL_OPACITY_EPSILON:
                hidden_until_zoom = float(stop)
            elif next_opacity > _FULL_OPACITY_EPSILON:
                return None
        elif next_opacity <= _FULL_OPACITY - _FULL_OPACITY_EPSILON:
            return None
        current_output = expr[index + 1]

    if hidden_until_zoom is None:
        return None
    if existing_minzoom is not None and existing_minzoom >= hidden_until_zoom:
        return None
    if existing_maxzoom is not None and hidden_until_zoom >= existing_maxzoom:
        return None
    return hidden_until_zoom


def _extract_opacity_from_reachable_outputs(outputs: list[object]) -> float | None:
    if not outputs:
        return None
    for output in outputs:
        opacity = _extract_representative_opacity(output)
        if opacity is None:
            return None
        if opacity <= _FULL_OPACITY - _FULL_OPACITY_EPSILON:
            return opacity
    return _FULL_OPACITY


def _extract_match_opacity(expr: list[object]) -> float | None:
    if len(expr) < 4:
        return None
    outputs = [expr[index] for index in range(3, len(expr) - 1, 2)]
    outputs.append(expr[-1])
    return _extract_opacity_from_reachable_outputs(outputs)


def _extract_case_opacity(expr: list[object]) -> float | None:
    if len(expr) < 4:
        return None
    outputs = [expr[index] for index in range(2, len(expr) - 1, 2)]
    outputs.append(expr[-1])
    return _extract_opacity_from_reachable_outputs(outputs)


def _extract_coalesce_opacity(expr: list[object]) -> float | None:
    if len(expr) < 2:
        return None
    return _extract_representative_opacity(expr[1])


def _extract_representative_opacity(expr: object) -> float | None:
    scalar = _clamp_opacity_value(expr)
    if scalar is not None:
        return scalar
    if not isinstance(expr, list) or not expr:
        return None

    op = expr[0]
    if op == "interpolate":
        representative = _representative_opacity_interpolate_output(expr)
        return _extract_representative_opacity(representative)
    if op == "step":
        representative = _representative_opacity_step_output(expr)
        return _extract_representative_opacity(representative)
    if op == "match":
        return _extract_match_opacity(expr)
    if op == "case":
        return _extract_case_opacity(expr)
    if op == "coalesce":
        return _extract_coalesce_opacity(expr)
    return None


def _is_literal_number_array(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(not isinstance(item, bool) and isinstance(item, (int, float)) and item >= 0 for item in value)
    )


def _literal_line_dasharray(value: object) -> list[object] | None:
    if _is_literal_number_array(value):
        return list(value)
    if not isinstance(value, list) or not value:
        return None
    if value[0] == "literal" and len(value) == 2 and _is_literal_number_array(value[1]):
        return list(value[1])
    return None


def _representative_line_dasharray_interpolate_output(expr: list[object]) -> object | None:
    if len(expr) < 5:
        return None
    if expr[2] == ["zoom"]:
        return _nearest_zoom_stop_value(expr)
    return expr[4]


def _representative_line_dasharray_step_output(expr: list[object]) -> object | None:
    if len(expr) < 3:
        return None
    if expr[1] == ["zoom"]:
        return _step_zoom_value(expr)
    return expr[2]


def _case_expression_output_candidates(expr: list[object]) -> list[object]:
    if len(expr) < 4:
        return []
    return [expr[-1], *(expr[index] for index in range(len(expr) - 2, 1, -2))]


def _first_line_dasharray_literal(candidates: list[object]) -> list[object] | None:
    for candidate in candidates:
        literal_dasharray = _extract_line_dasharray_literal(candidate)
        if literal_dasharray is not None:
            return literal_dasharray
    return None


def _extract_line_dasharray_literal(expr: object) -> list[object] | None:
    """Extract a literal QGIS-safe dash pattern from simple Mapbox expressions."""
    literal_dasharray = _literal_line_dasharray(expr)
    if literal_dasharray is not None:
        return literal_dasharray
    if not isinstance(expr, list) or not expr:
        return None

    op = expr[0]
    if op == "interpolate":
        return _extract_line_dasharray_literal(_representative_line_dasharray_interpolate_output(expr))
    if op == "step":
        return _extract_line_dasharray_literal(_representative_line_dasharray_step_output(expr))
    if op == "match":
        return _extract_line_dasharray_literal(expr[-1]) if len(expr) >= 5 else None
    if op == "case":
        return _first_line_dasharray_literal(_case_expression_output_candidates(expr))
    if op == "coalesce":
        return _first_line_dasharray_literal(list(reversed(expr[1:])))
    return None


_FILTER_SIMPLIFICATION_NOT_AVAILABLE = object()


def _inverted_boolean_match_filter(value: object) -> object:
    if not isinstance(value, list) or len(value) != 2 or value[0] != "!":
        return _FILTER_SIMPLIFICATION_NOT_AVAILABLE
    match_expression = value[1]
    if (
        not isinstance(match_expression, list)
        or len(match_expression) < 5
        or (len(match_expression) - 3) % 2 != 0
        or match_expression[0] != "match"
    ):
        return _FILTER_SIMPLIFICATION_NOT_AVAILABLE

    normalized = ["match", copy.deepcopy(match_expression[1])]
    for output_index in range(3, len(match_expression) - 1, 2):
        output_value = match_expression[output_index]
        if not isinstance(output_value, bool):
            return _FILTER_SIMPLIFICATION_NOT_AVAILABLE
        normalized.extend([copy.deepcopy(match_expression[output_index - 1]), not output_value])
    default_value = match_expression[-1]
    if not isinstance(default_value, bool):
        return _FILTER_SIMPLIFICATION_NOT_AVAILABLE
    normalized.append(not default_value)
    return normalized


def _simple_case_filter(value: object) -> object:
    if not isinstance(value, list) or len(value) != 4 or value[0] != "case":
        return _FILTER_SIMPLIFICATION_NOT_AVAILABLE

    condition = _simplify_filter_expression_for_qgis(value[1], root=True)
    predicate = _simplify_filter_expression_for_qgis(value[2], root=True)
    default = value[3]
    if value[2] is True and default is False:
        return condition
    if value[2] is False and default is True:
        return ["!", condition]
    if default is True:
        return ["any", ["!", condition], predicate]
    if default is False:
        return ["all", condition, predicate]
    return _FILTER_SIMPLIFICATION_NOT_AVAILABLE


def _is_numeric_zero(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def _additive_identity_filter(value: list[object], *, root: bool) -> object:
    if len(value) != 3:
        return _FILTER_SIMPLIFICATION_NOT_AVAILABLE
    left, right = value[1], value[2]
    if value[0] == "+":
        if _is_numeric_zero(left):
            return _simplify_filter_expression_for_qgis(right, root=root)
        if _is_numeric_zero(right):
            return _simplify_filter_expression_for_qgis(left, root=root)
    if value[0] == "-" and _is_numeric_zero(right):
        return _simplify_filter_expression_for_qgis(left, root=root)
    return _FILTER_SIMPLIFICATION_NOT_AVAILABLE


def _simplify_filter_expression_for_qgis(value: object, *, root: bool = True) -> object:
    """Apply semantics-preserving filter rewrites that QGIS parses more reliably."""
    if isinstance(value, bool):
        if root:
            return ["==", 1, 1 if value else 0]
        return value
    if not isinstance(value, list) or not value:
        return value

    operator = value[0]
    if operator == "literal":
        return value
    inverted_match = _inverted_boolean_match_filter(value)
    if inverted_match is not _FILTER_SIMPLIFICATION_NOT_AVAILABLE:
        return inverted_match
    case_filter = _simple_case_filter(value)
    if case_filter is not _FILTER_SIMPLIFICATION_NOT_AVAILABLE:
        return case_filter
    if operator in {"+", "-"}:
        additive_identity = _additive_identity_filter(value, root=root)
        if additive_identity is not _FILTER_SIMPLIFICATION_NOT_AVAILABLE:
            return additive_identity
    return [operator, *[_simplify_filter_expression_for_qgis(item, root=False) for item in value[1:]]]


def _numeric_expression_value(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _filter_expression_depends_on_zoom(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    operator = value[0]
    if operator == "zoom" and len(value) == 1:
        return True
    children: Iterable[object]
    if operator == "literal":
        children = []
    elif operator == "match":
        children = []
        if len(value) > 1:
            children = [value[1]]
        match_outputs = [value[index] for index in range(3, len(value) - 1, 2)]
        if len(value) > 2:
            match_outputs.append(value[-1])
        children = [*children, *match_outputs]
    else:
        children = value[1:]
    return any(_filter_expression_depends_on_zoom(child) for child in children)


def _arithmetic_filter_value_at_zoom(operator: str, values: list[object]) -> object | None:
    numeric_values = [_numeric_expression_value(value) for value in values]
    if not numeric_values or any(value is None for value in numeric_values):
        return None
    numbers = [value for value in numeric_values if value is not None]
    if operator == "+":
        return sum(numbers)
    if operator == "-":
        return -numbers[0] if len(numbers) == 1 else numbers[0] - sum(numbers[1:])
    if operator == "*":
        result = 1.0
        for number in numbers:
            result *= number
        return result
    if operator == "/" and len(numbers) == 2 and numbers[1] != 0:
        return numbers[0] / numbers[1]
    return None


def _step_filter_value_at_zoom(expression: list[object], zoom: float) -> object | None:
    if len(expression) < 4:
        return None
    input_value = _numeric_expression_value(_filter_expression_value_at_zoom(expression[1], zoom))
    if input_value is None:
        return None
    selected_value = expression[2]
    for index in range(3, len(expression) - 1, 2):
        stop = _numeric_expression_value(expression[index])
        if stop is None or input_value < stop:
            break
        selected_value = expression[index + 1]
    return _filter_expression_value_at_zoom(selected_value, zoom)


def _interpolate_filter_factor(
    interpolation_type: object,
    input_value: float,
    lower_stop: float,
    upper_stop: float,
) -> float | None:
    if upper_stop == lower_stop:
        return None
    linear_factor = (input_value - lower_stop) / (upper_stop - lower_stop)
    if interpolation_type == ["linear"]:
        return linear_factor
    if (
        isinstance(interpolation_type, list)
        and len(interpolation_type) == 2
        and interpolation_type[0] == "exponential"
    ):
        base = _numeric_expression_value(interpolation_type[1])
        if base is None or base <= 0:
            return None
        if abs(base - 1.0) <= _ZOOM_BOUND_EPSILON:
            return linear_factor
        # Match Mapbox GL JS' exponentialInterpolation: the exponent uses raw
        # stop distance, not the normalized 0..1 progress fraction.
        denominator = (base ** (upper_stop - lower_stop)) - 1.0
        if denominator == 0:
            return None
        return ((base ** (input_value - lower_stop)) - 1.0) / denominator
    return None


def _interpolate_filter_stops(expression: list[object], zoom: float) -> list[tuple[float, object]]:
    stops: list[tuple[float, object]] = []
    for index in range(3, len(expression) - 1, 2):
        stop = _numeric_expression_value(expression[index])
        if stop is not None:
            stops.append((stop, _filter_expression_value_at_zoom(expression[index + 1], zoom)))
    return stops


def _interpolate_filter_output_between_stops(
    interpolation_type: object,
    input_value: float,
    lower_stop: float,
    lower_value: object,
    upper_stop: float,
    upper_value: object,
) -> object | None:
    lower_numeric = _numeric_expression_value(lower_value)
    upper_numeric = _numeric_expression_value(upper_value)
    if lower_numeric is None or upper_numeric is None:
        return lower_value
    fraction = _interpolate_filter_factor(interpolation_type, input_value, lower_stop, upper_stop)
    if fraction is None:
        return None
    return lower_numeric + ((upper_numeric - lower_numeric) * fraction)


def _interpolate_filter_value_at_zoom(expression: list[object], zoom: float) -> object | None:
    if len(expression) < 5:
        return None
    input_value = _numeric_expression_value(_filter_expression_value_at_zoom(expression[2], zoom))
    if input_value is None:
        return None
    stops = _interpolate_filter_stops(expression, zoom)
    if not stops:
        return None
    if input_value <= stops[0][0]:
        return stops[0][1]
    for (lower_stop, lower_value), (upper_stop, upper_value) in zip(stops, stops[1:]):
        if input_value <= upper_stop:
            return _interpolate_filter_output_between_stops(
                expression[1], input_value, lower_stop, lower_value, upper_stop, upper_value
            )
    return stops[-1][1]


def _match_filter_value_at_zoom(expression: list[object], zoom: float) -> object:
    if len(expression) < 5 or (len(expression) - 3) % 2 != 0:
        return _FILTER_SIMPLIFICATION_NOT_AVAILABLE
    normalized = ["match", _filter_expression_value_at_zoom(expression[1], zoom)]
    for label_index in range(2, len(expression) - 1, 2):
        normalized.extend(
            [
                copy.deepcopy(expression[label_index]),
                _filter_expression_value_at_zoom(expression[label_index + 1], zoom),
            ]
        )
    normalized.append(_filter_expression_value_at_zoom(expression[-1], zoom))
    return normalized


def _operator_filter_value_at_zoom(operator: object, expression: list[object], zoom: float) -> object:
    if operator == "zoom" and len(expression) == 1:
        return zoom
    if operator == "step":
        step_value = _step_filter_value_at_zoom(expression, zoom)
        return step_value if step_value is not None else expression
    if operator == "interpolate":
        interpolate_value = _interpolate_filter_value_at_zoom(expression, zoom)
        return interpolate_value if interpolate_value is not None else expression
    if operator == "match":
        return _match_filter_value_at_zoom(expression, zoom)
    return _FILTER_SIMPLIFICATION_NOT_AVAILABLE


def _arithmetic_filter_expression_value_at_zoom(operator: object, expression: list[object], zoom: float) -> object:
    if not isinstance(operator, str) or operator not in {"+", "-", "*", "/"}:
        return _FILTER_SIMPLIFICATION_NOT_AVAILABLE
    if not _filter_expression_depends_on_zoom(expression):
        return _FILTER_SIMPLIFICATION_NOT_AVAILABLE
    arithmetic_value = _arithmetic_filter_value_at_zoom(
        operator,
        [_filter_expression_value_at_zoom(item, zoom) for item in expression[1:]],
    )
    if arithmetic_value is None:
        return _FILTER_SIMPLIFICATION_NOT_AVAILABLE
    return arithmetic_value


def _filter_expression_value_at_zoom(value: object, zoom: float) -> object:
    """Collapse Mapbox filter zoom expressions to a QGIS-parser-friendly snapshot."""
    if not isinstance(value, list) or not value:
        return value
    operator = value[0]
    if operator == "literal":
        return value
    operator_value = _operator_filter_value_at_zoom(operator, value, zoom)
    if operator_value is not _FILTER_SIMPLIFICATION_NOT_AVAILABLE:
        return operator_value
    arithmetic_value = _arithmetic_filter_expression_value_at_zoom(operator, value, zoom)
    if arithmetic_value is not _FILTER_SIMPLIFICATION_NOT_AVAILABLE:
        return arithmetic_value
    return [_filter_expression_value_at_zoom(item, zoom) for item in value]


def _zoom_normalized_filter_expression_for_qgis(layer: dict[str, object], value: object) -> object:
    # Some Mapbox Outdoors road layers begin just below their high-detail filter
    # branch. Snapshot those layers at the branch threshold instead of the layer
    # minzoom so QGIS keeps service-road detail in high-zoom renders.
    override_zoom = _FILTER_NORMALIZATION_ZOOM_OVERRIDES.get(str(layer.get("id") or ""))
    target_zoom = (
        _zoom_in_layer_range(override_zoom, layer.get("minzoom"), layer.get("maxzoom"))
        if override_zoom is not None
        else None
    )
    if target_zoom is None:
        target_zoom = _representative_zoom_in_layer_range(layer.get("minzoom"), layer.get("maxzoom"))
    if target_zoom is None:
        target_zoom = _REPRESENTATIVE_STYLE_ZOOM
    return _filter_expression_value_at_zoom(value, target_zoom)


def _should_zoom_normalize_filter_for_qgis(layer: dict[str, object]) -> bool:
    # QGIS' Mapbox converter rejects zoom-dependent filters. Restrict static
    # zoom snapshots to the high-signal label layers from #949 visual audits:
    # repeated road labels, pedestrian path label noise, ferry/transit label
    # leakage, road shields/one-way arrows, terrain/landcover layers, and the
    # road line filters whose normalized branches improved #949 visual audits.
    # Applying the same approximation broadly can hide high-zoom path geometry
    # or over-suppress POIs/places, so keep this deliberately small.
    normalized_layer_id = base_mapbox_style_layer_id_for_qfit(layer.get("id"))
    layer_type = layer.get("type")
    return (
        (layer_type == "symbol" and normalized_layer_id in _ZOOM_NORMALIZED_SYMBOL_FILTER_LAYER_IDS)
        or (layer_type == "fill" and normalized_layer_id in _ZOOM_NORMALIZED_FILL_FILTER_LAYER_IDS)
        or (layer_type == "line" and normalized_layer_id in _ZOOM_NORMALIZED_LINE_FILTER_LAYER_IDS)
    )


def _line_layout_choice(expr: object, choices: set[str]) -> str | None:
    if not isinstance(expr, list) or len(expr) < 3 or expr[0] != "step" or expr[1] != ["zoom"]:
        return None
    output = expr[-1] if len(expr) >= 5 else expr[2]
    return output if isinstance(output, str) and output in choices else None


def _zoom_step_layout_choice(
    expr: object,
    choices: set[str],
    *,
    minzoom: object = None,
    maxzoom: object = None,
) -> str | None:
    if not isinstance(expr, list) or len(expr) < 3 or expr[0] != "step" or expr[1] != ["zoom"]:
        return None
    target_zoom = _representative_zoom_in_layer_range(minzoom, maxzoom)
    if target_zoom is None:
        return None
    output = _step_zoom_value(expr, target_zoom=target_zoom)
    return output if isinstance(output, str) and output in choices else None


def _is_simple_text_field_reference(expr: object) -> bool:
    return isinstance(expr, list) and len(expr) == 2 and expr[0] == "get" and isinstance(expr[1], str)


def _is_text_font_stack(expr: object) -> bool:
    return isinstance(expr, list) and len(expr) > 0 and all(isinstance(item, str) for item in expr)


def _first_text_field_reference_child(
    children: list[object],
    *,
    allow_concat: bool,
    allow_to_string: bool = True,
) -> object | None:
    for child in children:
        if isinstance(child, dict):
            continue
        reference = _first_simple_text_field_reference(
            child,
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
        if reference is not None:
            return reference
    return None


def _text_field_reference_name(reference: object) -> str | None:
    if (
        isinstance(reference, list)
        and len(reference) == 2
        and reference[0] == "get"
        and isinstance(reference[1], str)
    ):
        return reference[1]
    return None


def _is_localized_name_reference(reference: object) -> bool:
    name = _text_field_reference_name(reference)
    return name is not None and name.startswith(("name_", "name:"))


def _prefer_generic_name_reference(references: list[object]) -> object | None:
    for index, reference in enumerate(references):
        if _text_field_reference_name(reference) == "name":
            if index == 0 or all(_is_localized_name_reference(item) for item in references[:index]):
                return reference
    return references[0] if references else None


def _prefer_airport_name_reference(references: list[object]) -> object | None:
    for reference in references:
        if _text_field_reference_name(reference) == "name":
            return reference
    for reference in references:
        if _is_localized_name_reference(reference):
            return reference
    return None


def _all_simple_text_field_references(expr: object) -> list[object]:
    if isinstance(expr, dict):
        return []
    if _is_simple_text_field_reference(expr):
        return [expr]
    if not isinstance(expr, list):
        return []
    references: list[object] = []
    for child in expr[1:]:
        references.extend(_all_simple_text_field_references(child))
    return references


def _text_field_references_from_children(
    children: list[object],
    *,
    allow_concat: bool,
    allow_to_string: bool,
) -> list[object]:
    references: list[object] = []
    for child in children:
        if isinstance(child, dict):
            continue
        reference = _first_simple_text_field_reference(
            child,
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
        if reference is not None:
            references.append(reference)
    return references


def _first_coalesced_text_field_reference(expr: list[object]) -> object | None:
    references = _text_field_references_from_children(
        expr[1:],
        allow_concat=False,
        allow_to_string=False,
    )
    if references:
        return _prefer_generic_name_reference(references)
    return _prefer_generic_name_reference(
        _text_field_references_from_children(
            expr[1:],
            allow_concat=True,
            allow_to_string=True,
        )
    )


def _text_field_output_candidates(expr: list[object]) -> list[object]:
    """Return expression outputs from text-field control-flow operators."""
    op = expr[0]
    if op == "step" and len(expr) >= 3:
        return [expr[2], *expr[4::2]]
    if op == "case" and len(expr) >= 4:
        return [*expr[2:-1:2], expr[-1]]
    if op == "match" and len(expr) >= 5:
        return [*expr[3:-1:2], expr[-1]]
    return []


def _first_text_field_output_reference(
    expr: list[object],
    *,
    allow_concat: bool,
    allow_to_string: bool,
) -> object | None:
    return _prefer_generic_name_reference(
        _text_field_references_from_children(
            _text_field_output_candidates(expr),
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
    )


def _first_simple_text_field_reference(
    expr: object,
    *,
    allow_concat: bool = True,
    allow_to_string: bool = True,
) -> object | None:
    """Return the first direct ``['get', field]`` from text-oriented expressions."""
    if not isinstance(expr, list) or not expr:
        return None
    if _is_simple_text_field_reference(expr):
        return expr
    op = expr[0]
    if op == "coalesce":
        return _first_coalesced_text_field_reference(expr)
    if op == "concat" and not allow_concat:
        return None
    if op in {"concat", "format"}:
        return _first_text_field_reference_child(
            expr[1:],
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
    if op == "to-string" and len(expr) >= 2:
        if not allow_to_string:
            return None
        return _first_simple_text_field_reference(
            expr[1],
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
    if op in {"case", "match", "step"}:
        return _first_text_field_output_reference(
            expr,
            allow_concat=allow_concat,
            allow_to_string=allow_to_string,
        )
    return None


def _simplify_text_field(expr: object) -> object:
    """Simplify a Mapbox text-field expression to the first simple field reference.

    QGIS handles ``['get', 'name']`` but not richer Mapbox label expressions such
    as ``coalesce`` or ``format``. We extract the first useful ``['get', <field>]``
    reference so formatted labels still render with their primary label text.
    """
    if not isinstance(expr, list) or not expr:
        return expr
    op = expr[0]
    if op in {"case", "coalesce", "concat", "format", "match", "step", "to-string"}:
        reference = _first_simple_text_field_reference(expr)
        if reference is not None:
            return reference
    if op == "step":
        # step expressions for text-field — find the first literal string fallback
        for item in expr[1:]:
            if isinstance(item, str) and item:
                return item
    return expr


def _simplify_airport_label_text_field(base_layer_id: str, expr: object) -> object | None:
    """Keep airport labels name-first instead of collapsing to the short ref code."""
    if (
        base_layer_id != _AIRPORT_LABEL_LAYER_ID
        or not isinstance(expr, list)
        or len(expr) < 3
        or expr[0] != "step"
        or expr[1] != ["get", "sizerank"]
    ):
        return None
    references: list[object] = []
    for output in _text_field_output_candidates(expr):
        references.extend(_all_simple_text_field_references(output))
    if not any(_text_field_reference_name(reference) == "ref" for reference in references):
        return None
    return copy.deepcopy(_prefer_airport_name_reference(references))


def _drop_icon_opacity_without_icon_image(layer: dict[str, object]) -> None:
    """Remove no-op icon opacity expressions after qfit drops an empty icon-image."""
    paint = layer.get("paint")
    if not isinstance(paint, dict) or "icon-opacity" not in paint:
        return
    layout = layer.get("layout")
    if isinstance(layout, dict) and "icon-image" in layout:
        return
    del paint["icon-opacity"]


def _line_blur_width_mm(expr: object, *, minzoom: object = None, maxzoom: object = None) -> float | None:
    """Return a representative QGIS line-blur width in millimetres."""
    blur_px = _extract_zoom_scalar_size(expr, minzoom=minzoom, maxzoom=maxzoom)
    if blur_px is None:
        return None
    return max(0.0, min(blur_px * _MAPBOX_PIXEL_TO_MM, _MAX_LINE_WIDTH_MM))


def _boundary_bg_line_opacity(
    base_layer_id: str,
    prop: str,
    expr: object,
    *,
    minzoom: object = None,
    maxzoom: object = None,
) -> float | None:
    """Return a representative scalar opacity for admin boundary background lines."""
    if base_layer_id not in _BOUNDARY_BG_LINE_OPACITY_LAYER_IDS or prop != "line-opacity":
        return None
    opacity = _extract_zoom_scalar_size(expr, minzoom=minzoom, maxzoom=maxzoom)
    return _clamp_opacity_value(opacity)


def simplify_mapbox_style_expressions(style_definition: dict[str, object]) -> dict[str, object]:
    """Return a copy of a Mapbox style with expression-based colors replaced by
    literal fallback colors so QGIS' converter does not render them as black.

    Also simplifies ``text-field`` coalesce expressions to their first simple
    ``['get', field]`` reference so QGIS can resolve the label field name,
    literalizes simple ``line-dasharray`` expressions so dashed routes and paths
    survive QGIS conversion, rewrites a few semantics-preserving filter shapes,
    snapshots selected zoom-dependent filters at a representative layer zoom that
    QGIS can parse, splits visible hillshade, landcover, landuse, waterway,
    path background, and road class colors into static class layers, and collapses
    Mapbox font stacks to a QGIS-safe local fallback to avoid
    warning spam from proprietary Mapbox font
    family names.

    Only color properties whose values are Mapbox expressions (lists) are
    simplified.  Literal strings (``hsl(...)``, ``#rrggbb``) are kept as-is.
    """
    style = copy.deepcopy(style_definition)
    style["layers"] = _split_regional_major_road_width_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_major_link_width_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_road_class_line_color_layers_for_qgis(style.get("layers"))
    style["layers"] = _expand_road_number_shield_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_path_type_filter_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_path_background_line_color_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_poi_label_filter_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_label_icon_visibility_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_gate_label_icon_image_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_settlement_dot_icon_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_country_label_layout_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_continent_label_text_opacity_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_cliff_line_pattern_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_building_fill_opacity_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_hillshade_fill_color_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_landcover_fill_opacity_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_landcover_class_fill_color_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_landuse_fill_opacity_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_landuse_class_fill_color_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_national_park_fill_opacity_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_wetland_fill_opacity_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_road_pedestrian_polygon_pattern_fill_opacity_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_rail_track_line_opacity_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_gate_fence_hedge_line_opacity_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_water_shadow_translate_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_aeroway_line_width_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_waterway_line_width_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_turning_feature_circle_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_waterway_label_symbol_spacing_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_water_label_typography_layers_for_qgis(style.get("layers"))
    style["layers"] = _split_contour_line_opacity_layers_for_qgis(style.get("layers"))
    color_props = {
        "line-color", "fill-color", "fill-outline-color", "circle-color",
        "circle-stroke-color", "text-color", "text-halo-color",
        "icon-color", "icon-halo-color", "background-color",
        "fill-extrusion-color",
    }
    # Line width properties: expression values can reach 200–300 at max zoom;
    # QGIS may pick a large stop and produce QPen::setWidthF warnings.
    # We extract a z12-representative value and cap to a sane maximum.
    _WIDTH_PROPS = {"line-width", "line-gap-width", "line-offset"}
    _LINE_BLUR_PROPS = {"line-blur"}
    _LINE_LAYOUT_CHOICES = {
        "line-cap": {"butt", "round", "square"},
        "line-join": {"bevel", "round", "miter"},
    }
    # Per-layer-id text-size overrides to restore cartographic hierarchy.
    _TEXT_SIZE_OVERRIDES: dict[str, object] = {
        "natural-point-label": 9.0,
        "natural-line-label": 10.0,
        "poi-label": 9.0,
        "road-label": 10.0,
        "path-pedestrian-label": 9.0,
        "waterway-label": 10.0,
        "water-line-label": 11.0,
        "water-point-label": 12.0,
        "airport-label": 11.0,
        "settlement-subdivision-label": 8.0,
        "settlement-minor-label": 10.0,
        "settlement-major-label": 14.0,
        "state-label": 13.0,
        "country-label": 16.0,
        "continent-label": 16.0,
    }

    # Settlement layer label policy:
    # - settlement-major-label: only cities (type=city) — Geneva, Bern, Lyon, Lausanne
    # - settlement-minor-label: only towns (type=town) with filterrank<=2 — regional centres
    # - settlement-subdivision-label: suppress entirely
    # filterrank is available in tiles (verified z10: Cologny=3, Corsier=5, Geneva=1)
    # Filter by `type` only — filterrank is zoom-dependent and unreliable for
    # consistent cross-zoom filtering. `type` is stable across all zoom levels.
    # Mapbox Streets v8 settlement types: city, town, village, hamlet, suburb,
    # neighbourhood, quarter, borough
    _SETTLEMENT_FILTERS: dict[str, object] = {
        "settlement-major-label": ["match", ["get", "type"], ["city"], True, False],
        "settlement-minor-label": ["match", ["get", "type"], ["town"], True, False],
        "settlement-subdivision-label": None,
    }

    for layer in style.get("layers", []):
        layer_id = layer.get("id", "")
        base_layer_id = base_mapbox_style_layer_id_for_qfit(layer_id)

        # Suppress or filter settlement label layers
        settlement_filter = _SETTLEMENT_FILTERS.get(base_layer_id, "NOTSET")
        if settlement_filter != "NOTSET":
            if settlement_filter is None:
                layer["layout"] = layer.get("layout", {})
                layer["layout"]["visibility"] = "none"
            else:
                existing_filter = layer.get("filter")
                if existing_filter:
                    layer["filter"] = ["all", existing_filter, settlement_filter]
                else:
                    layer["filter"] = settlement_filter

        filter_value = layer.get("filter")
        if isinstance(filter_value, (bool, list)):
            if _should_zoom_normalize_filter_for_qgis(layer):
                filter_value = _zoom_normalized_filter_expression_for_qgis(layer, filter_value)
            layer["filter"] = _simplify_filter_expression_for_qgis(filter_value)

        if base_layer_id == _AEROWAY_POLYGON_LAYER_ID and layer.get("type") == "fill":
            paint = layer.get("paint")
            if isinstance(paint, dict) and paint.get("fill-color") == _AEROWAY_POLYGON_FILL_COLOR:
                # QGIS renders Mapbox's airport aeroway fill too close to the
                # surrounding airport landuse in the Geneva z14 comparison.
                paint["fill-color"] = _AEROWAY_POLYGON_QGIS_CONTRAST_FILL_COLOR

        for section in ("paint", "layout"):
            props = layer.get(section)
            if not isinstance(props, dict):
                continue
            for prop in list(props.keys()):
                val = props[prop]
                if (
                    section == "layout"
                    and prop == "symbol-sort-key"
                    and base_layer_id in {_SETTLEMENT_MAJOR_LABEL_LAYER_ID, _SETTLEMENT_MINOR_LABEL_LAYER_ID}
                    and val == _SETTLEMENT_SYMBOL_SORT_KEY_EXPRESSION
                ):
                    del props[prop]
                    continue
                if section == "layout" and prop == "icon-image":
                    if val == "":
                        del props[prop]
                        continue
                    icon_image = _literal_step_icon_image(
                        val,
                        minzoom=layer.get("minzoom"),
                        maxzoom=layer.get("maxzoom"),
                    )
                    if icon_image is not _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE:
                        if icon_image:
                            props[prop] = icon_image
                        else:
                            del props[prop]
                        continue
                    icon_image = _icon_image_empty_match_fallback(base_layer_id, val)
                    if icon_image is not _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE:
                        props[prop] = icon_image
                        continue
                    icon_image = _icon_image_get_fallback(base_layer_id, val)
                    if icon_image is not _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE:
                        props[prop] = icon_image
                        continue
                    icon_image = _poi_label_icon_image_fallback(base_layer_id, val)
                    if icon_image is not _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE:
                        props[prop] = icon_image
                        continue
                    icon_image = _road_exit_shield_icon_fallback(layer_id, val)
                    if icon_image is not _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE:
                        props[prop] = icon_image
                        continue
                if section == "layout" and base_layer_id == _TRANSIT_LABEL_LAYER_ID:
                    layout_value = _transit_label_non_entrance_layout_value(
                        prop,
                        val,
                        layer.get("filter"),
                    )
                    if layout_value is not _LAYOUT_SIMPLIFICATION_NOT_AVAILABLE:
                        props[prop] = layout_value
                        continue
                if not isinstance(val, list):
                    continue
                if prop in color_props:
                    fallback = _extract_fallback_color(val)
                    if fallback is not None:
                        props[prop] = fallback
                elif prop in _WIDTH_PROPS:
                    width = None
                    is_regional_road_width_variant = (
                        prop in _REGIONAL_MAJOR_ROAD_STROKE_WIDTH_PROPS
                        and _is_regional_major_road_width_variant(layer_id)
                    )
                    if is_regional_road_width_variant:
                        width = _extract_zoom_scalar_size(
                            val,
                            minzoom=layer.get("minzoom"),
                            maxzoom=layer.get("maxzoom"),
                        )
                    if width is None:
                        width = _extract_midrange_size(val)
                    if width is not None:
                        if is_regional_road_width_variant:
                            width *= _regional_major_road_width_scale(layer_id)
                        # Convert px → mm (96 DPI) and clamp to sane range
                        width_mm = width * _MAPBOX_PIXEL_TO_MM
                        if is_regional_road_width_variant and prop == "line-width":
                            width_mm = max(width_mm, _regional_major_road_min_width_mm(layer_id))
                        props[prop] = max(0.1, min(width_mm, _MAX_LINE_WIDTH_MM))
                elif prop in _LINE_BLUR_PROPS:
                    blur_width_mm = _line_blur_width_mm(
                        val,
                        minzoom=layer.get("minzoom"),
                        maxzoom=layer.get("maxzoom"),
                    )
                    if blur_width_mm is not None:
                        props[prop] = blur_width_mm
                elif prop == "line-dasharray":
                    dasharray = _extract_line_dasharray_literal(val)
                    if dasharray is not None:
                        props[prop] = dasharray
                elif prop in _FULL_OPACITY_PROPS:
                    boundary_bg_opacity = _boundary_bg_line_opacity(
                        base_layer_id,
                        prop,
                        val,
                        minzoom=layer.get("minzoom"),
                        maxzoom=layer.get("maxzoom"),
                    )
                    if boundary_bg_opacity is not None:
                        props[prop] = boundary_bg_opacity
                        continue
                    visibility_minzoom = _zoom_step_full_opacity_minzoom(
                        val,
                        minzoom=layer.get("minzoom"),
                        maxzoom=layer.get("maxzoom"),
                    )
                    if visibility_minzoom is not None:
                        layer["minzoom"] = visibility_minzoom
                        del props[prop]
                        continue
                    opacity = _extract_representative_opacity(val)
                    if opacity is None or opacity <= _FULL_OPACITY - _FULL_OPACITY_EPSILON:
                        opacity = _extract_visible_range_zoom_step_opacity(val, layer.get("minzoom"))
                    if opacity is not None and opacity > _FULL_OPACITY - _FULL_OPACITY_EPSILON:
                        props[prop] = _FULL_OPACITY
                elif prop == "text-field":
                    airport_text_field = _simplify_airport_label_text_field(base_layer_id, val)
                    props[prop] = airport_text_field if airport_text_field is not None else _simplify_text_field(val)
                elif prop == "text-font" and _is_text_font_stack(val):
                    props[prop] = [QGIS_TEXT_FONT_FALLBACK]
                elif prop == "text-size":
                    override = _TEXT_SIZE_OVERRIDES.get(base_layer_id)
                    if override is not None:
                        props[prop] = override
                    else:
                        size = _extract_midrange_size(val)
                        if size is not None:
                            props[prop] = size
                elif prop == "icon-size":
                    size = _extract_zoom_scalar_size(
                        val,
                        minzoom=layer.get("minzoom"),
                        maxzoom=layer.get("maxzoom"),
                    )
                    if size is not None:
                        props[prop] = size
                elif prop == "symbol-spacing" and _is_road_number_shield_layer_id(layer_id):
                    spacing = _extract_zoom_scalar_size(
                        val,
                        minzoom=layer.get("minzoom"),
                        maxzoom=layer.get("maxzoom"),
                    )
                    if spacing is not None:
                        props[prop] = spacing
                elif prop == "symbol-placement" and _is_road_number_shield_layer_id(layer_id):
                    placement = _zoom_step_layout_choice(
                        val,
                        {"line", "point"},
                        minzoom=layer.get("minzoom"),
                        maxzoom=layer.get("maxzoom"),
                    )
                    if placement is not None:
                        props[prop] = placement
                elif prop in _LINE_LAYOUT_CHOICES:
                    choice = _line_layout_choice(val, _LINE_LAYOUT_CHOICES[prop])
                    if choice is not None:
                        props[prop] = choice
        _drop_icon_opacity_without_icon_image(layer)
    return style


def extract_mapbox_vector_source_ids(style_definition: dict[str, object]) -> list[str]:
    """Extract vector tileset IDs from a Mapbox style definition.

    Supports sources declared like: {"url": "mapbox://tilesetA,tilesetB"}
    """
    sources = style_definition.get("sources") if isinstance(style_definition, dict) else None
    if not isinstance(sources, dict):
        raise MapboxConfigError("Mapbox style JSON does not declare any sources.")

    tileset_ids: list[str] = []
    for source in sources.values():
        if not isinstance(source, dict) or source.get("type") != "vector":
            continue
        url = str(source.get("url") or "").strip()
        if not url.startswith("mapbox://"):
            continue
        raw_ids = url.removeprefix("mapbox://")
        for tileset_id in raw_ids.split(","):
            normalized = tileset_id.strip()
            if normalized and normalized not in tileset_ids:
                tileset_ids.append(normalized)

    if not tileset_ids:
        raise MapboxConfigError("Mapbox style JSON does not expose a vector tileset source for QGIS.")
    return tileset_ids


def build_mapbox_vector_tiles_url(
    access_token: str,
    style_owner: str,
    style_id: str,
    *,
    tileset_ids: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Return the Mapbox vector tile endpoint URL for a given style/source."""
    token, owner, resolved_style_id = _validated_mapbox_style_parts(access_token, style_owner, style_id)

    if tileset_ids:
        joined_tilesets = ",".join(quote(tileset_id.strip(), safe=".,-_") for tileset_id in tileset_ids if tileset_id.strip())
    else:
        joined_tilesets = "{owner}.{style_id}".format(
            owner=quote(owner, safe=""),
            style_id=quote(resolved_style_id, safe=""),
        )

    return (
        "https://api.mapbox.com/v4/{tilesets}/{{z}}/{{x}}/{{y}}.mvt"
        "?access_token={token}"
    ).format(
        tilesets=joined_tilesets,
        token=quote(token, safe=""),
    )


def build_mapbox_style_json_url(
    access_token: str,
    style_owner: str,
    style_id: str,
) -> str:
    """Return the Mapbox style JSON URL for use with QGIS vector tile styling."""
    token, owner, resolved_style_id = _validated_mapbox_style_parts(access_token, style_owner, style_id)

    return (
        "https://api.mapbox.com/styles/v1/{owner}/{style_id}?access_token={token}"
    ).format(
        owner=quote(owner, safe=""),
        style_id=quote(resolved_style_id, safe=""),
        token=quote(token, safe=""),
    )


def build_mapbox_sprite_url(
    access_token: str,
    style_owner: str,
    style_id: str,
    *,
    file_type: str,
    retina: bool = False,
) -> str:
    """Return a Mapbox sprite JSON or PNG URL for a style."""
    token, owner, resolved_style_id = _validated_mapbox_style_parts(access_token, style_owner, style_id)
    if file_type not in {"json", "png"}:
        raise MapboxConfigError("Mapbox sprite file_type must be 'json' or 'png'.")
    return _format_mapbox_sprite_url(
        token,
        owner,
        resolved_style_id,
        (),
        file_type=file_type,
        retina=retina,
    )


def build_mapbox_sprite_file_url(
    access_token: str,
    sprite_url: str,
    *,
    file_type: str,
    retina: bool = False,
) -> str:
    """Return a concrete sprite JSON or PNG URL from a style JSON sprite base URL."""
    token = access_token.strip()
    sprite_base_url = sprite_url.strip()
    if not token:
        raise MapboxConfigError("Enter a Mapbox access token to load the selected background map.")
    if not sprite_base_url:
        raise MapboxConfigError("Mapbox style JSON does not define a sprite URL.")
    if file_type not in {"json", "png"}:
        raise MapboxConfigError("Mapbox sprite file_type must be 'json' or 'png'.")

    if sprite_base_url.startswith("mapbox://sprites/"):
        sprite_path = sprite_base_url.removeprefix("mapbox://sprites/")
        path_segments = tuple(unquote(segment) for segment in sprite_path.split("/"))
        if len(path_segments) < 2 or not path_segments[0] or not path_segments[1] or any(
            not segment for segment in path_segments[2:]
        ):
            raise MapboxConfigError("Mapbox sprite URL must include an owner and style ID.")
        return _format_mapbox_sprite_url(
            token,
            path_segments[0],
            path_segments[1],
            path_segments[2:],
            file_type=file_type,
            retina=retina,
        )

    parsed = urlparse(sprite_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MapboxConfigError("Mapbox sprite URL must be a mapbox://, http://, or https:// URL.")

    retina_suffix = "@2x" if retina else ""
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if _is_mapbox_hostname(parsed.hostname) and not any(key == "access_token" for key, _ in query):
        query.append(("access_token", token))
    return urlunparse(
        parsed._replace(
            path=f"{parsed.path}{retina_suffix}.{file_type}",
            query=urlencode(query),
        )
    )


def fetch_mapbox_sprite_resources(
    access_token: str,
    style_owner: str,
    style_id: str,
    *,
    sprite_url: str | None = None,
    retina: bool = False,
) -> MapboxSpriteResources:
    """Fetch Mapbox sprite definitions and image bytes for a style."""
    url_builder = build_mapbox_sprite_url
    url_args = (access_token, style_owner, style_id)
    if sprite_url:
        url_builder = build_mapbox_sprite_file_url
        url_args = (access_token, sprite_url)
    definitions_url = url_builder(*url_args, file_type="json", retina=retina)
    image_url = url_builder(*url_args, file_type="png", retina=retina)
    with urlopen(definitions_url, timeout=20) as response:  # noqa: S310
        definitions = json.loads(response.read().decode("utf-8"))
    if not isinstance(definitions, dict):
        raise MapboxConfigError("Mapbox sprite definitions response must be a JSON object.")
    with urlopen(image_url, timeout=20) as response:  # noqa: S310
        image_bytes = response.read()
    return MapboxSpriteResources(definitions=definitions, image_bytes=image_bytes)


def build_vector_tile_layer_uri(
    access_token: str,
    style_owner: str,
    style_id: str,
    *,
    tileset_ids: list[str] | tuple[str, ...] | None = None,
    include_style_url: bool = True,
) -> str:
    """Return a QGIS-compatible vector tile layer URI for a Mapbox style."""
    tiles_url = build_mapbox_vector_tiles_url(
        access_token,
        style_owner,
        style_id,
        tileset_ids=tileset_ids,
    )
    uri = "type=xyz&url={url}&zmin=0&zmax=22".format(
        url=quote(tiles_url, safe=":/?{}=%@,"),
    )
    if include_style_url:
        style_url = build_mapbox_style_json_url(access_token, style_owner, style_id)
        uri += "&styleUrl={style_url}".format(
            style_url=quote(style_url, safe=":/?{}=%@"),
        )
    return uri


def build_background_layer_name(preset_name: str | None, style_owner: str, style_id: str) -> str:
    label = (preset_name or DEFAULT_BACKGROUND_PRESET).strip() or DEFAULT_BACKGROUND_PRESET
    if label == "Custom":
        if style_owner and style_id:
            return f"{BACKGROUND_LAYER_PREFIX} — {style_owner}/{style_id}"
        return BACKGROUND_LAYER_PREFIX
    return f"{BACKGROUND_LAYER_PREFIX} — {label}"
