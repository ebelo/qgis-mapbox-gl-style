from __future__ import annotations

import copy
import json
from dataclasses import dataclass
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
    if op in {"coalesce", "concat", "format", "to-string"}:
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
    survive QGIS conversion, rewrites a few semantics-preserving filter shapes
    that QGIS parses more reliably, and collapses Mapbox font stacks to a
    QGIS-safe local fallback to avoid warning spam from proprietary Mapbox font
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
            layer["filter"] = _simplify_filter_expression_for_qgis(filter_value)

        for section in ("paint", "layout"):
            props = layer.get(section)
            if not isinstance(props, dict):
                continue
            for prop in list(props.keys()):
                val = props[prop]
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
