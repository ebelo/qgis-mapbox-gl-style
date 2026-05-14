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
QGIS_TEXT_FONT_FALLBACK = "Noto Sans"
_ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE = object()
_ICON_IMAGE_EMPTY_MATCH_FALLBACKS_BY_LAYER = {
    "gate-label": "gate",
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


def _representative_zoom_in_layer_range(minzoom: object, maxzoom: object) -> float | None:
    target_zoom = _REPRESENTATIVE_STYLE_ZOOM
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
    if not isinstance(expr, list) or len(expr) < 4:
        return None
    target_zoom = _representative_zoom_in_layer_range(minzoom, maxzoom)
    if target_zoom is None:
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
    target_zoom = _representative_zoom_in_layer_range(layer.get("minzoom"), layer.get("maxzoom"))
    if target_zoom is None:
        target_zoom = _REPRESENTATIVE_STYLE_ZOOM
    return _filter_expression_value_at_zoom(value, target_zoom)


def _should_zoom_normalize_filter_for_qgis(layer: dict[str, object]) -> bool:
    # QGIS' Mapbox converter rejects zoom-dependent filters. Restrict static
    # zoom snapshots to the high-signal label layers from #949 visual audits:
    # repeated road labels, pedestrian path label noise, ferry/transit label
    # leakage, road shields/one-way arrows, and terrain/landcover layers whose
    # normalized filters are QGIS-parser-friendly. Applying the same
    # approximation broadly can hide high-zoom road/path geometry or
    # over-suppress POIs/places, so keep this deliberately small.
    layer_id = layer.get("id")
    return (
        layer.get("type") == "symbol" and layer_id in _ZOOM_NORMALIZED_SYMBOL_FILTER_LAYER_IDS
    ) or (layer.get("type") == "fill" and layer_id in _ZOOM_NORMALIZED_FILL_FILTER_LAYER_IDS)


def _line_layout_choice(expr: object, choices: set[str]) -> str | None:
    if not isinstance(expr, list) or len(expr) < 3 or expr[0] != "step" or expr[1] != ["zoom"]:
        return None
    output = expr[-1] if len(expr) >= 5 else expr[2]
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


def simplify_mapbox_style_expressions(style_definition: dict[str, object]) -> dict[str, object]:
    """Return a copy of a Mapbox style with expression-based colors replaced by
    literal fallback colors so QGIS' converter does not render them as black.

    Also simplifies ``text-field`` coalesce expressions to their first simple
    ``['get', field]`` reference so QGIS can resolve the label field name,
    literalizes simple ``line-dasharray`` expressions so dashed routes and paths
    survive QGIS conversion, rewrites a few semantics-preserving filter shapes,
    snapshots selected zoom-dependent filters at a representative layer zoom that
    QGIS can parse, and collapses Mapbox font stacks to a QGIS-safe local fallback to avoid
    warning spam from proprietary Mapbox font
    family names.

    Only color properties whose values are Mapbox expressions (lists) are
    simplified.  Literal strings (``hsl(...)``, ``#rrggbb``) are kept as-is.
    """
    style = copy.deepcopy(style_definition)
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
    _MAX_LINE_WIDTH_MM = 3.0  # ~11px at 96 DPI — sane max for cartographic lines
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

        # Suppress or filter settlement label layers
        settlement_filter = _SETTLEMENT_FILTERS.get(layer_id, "NOTSET")
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

        for section in ("paint", "layout"):
            props = layer.get(section)
            if not isinstance(props, dict):
                continue
            for prop in list(props.keys()):
                val = props[prop]
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
                    icon_image = _icon_image_empty_match_fallback(layer_id, val)
                    if icon_image is not _ICON_IMAGE_SIMPLIFICATION_NOT_AVAILABLE:
                        props[prop] = icon_image
                        continue
                if not isinstance(val, list):
                    continue
                if prop in color_props:
                    fallback = _extract_fallback_color(val)
                    if fallback is not None:
                        props[prop] = fallback
                elif prop in _WIDTH_PROPS:
                    width = _extract_midrange_size(val)
                    if width is not None:
                        # Convert px → mm (96 DPI) and clamp to sane range
                        width_mm = width * 25.4 / 96.0
                        props[prop] = max(0.1, min(width_mm, _MAX_LINE_WIDTH_MM))
                elif prop == "line-dasharray":
                    dasharray = _extract_line_dasharray_literal(val)
                    if dasharray is not None:
                        props[prop] = dasharray
                elif prop in _FULL_OPACITY_PROPS:
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
                    props[prop] = _simplify_text_field(val)
                elif prop == "text-font" and _is_text_font_stack(val):
                    props[prop] = [QGIS_TEXT_FONT_FALLBACK]
                elif prop == "text-size":
                    override = _TEXT_SIZE_OVERRIDES.get(layer_id)
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
                elif prop in _LINE_LAYOUT_CHOICES:
                    choice = _line_layout_choice(val, _LINE_LAYOUT_CHOICES[prop])
                    if choice is not None:
                        props[prop] = choice
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
